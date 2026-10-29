"""CronService — 后台定时任务调度器。

优先使用 APScheduler 提供可靠的 cron/date 调度；如果环境未安装 APScheduler，
则回退为 daemon 线程 + 60 秒轮询。

任务列表持久化到 data/cron_jobs.json。

## 时间与星期语义（批3 · 2026-09-18 治本）

* **时钟**：一切"现在"读数经 ``core.clock``（``wall_now`` / ``wall_ts``）；
  ``last_run`` 落盘走 ``_safe_last_run_write`` 单一入口（防回拨毒化，P1-5）。
* **星期**：数字 dow 一律经 ``core.cron_expr`` 按**标准 cron**（0=Sun）解读后
  立即规范化为名字；内部补跑判据与 APScheduler 触发器消费**同一份**规范化
  结果，杜绝两引擎分裂（P2-1）。本模块不再存在"名字↔数字"映射表。
* **形状**：``schedule`` 必须是合法 5 字段 cron；RRULE 等一律拒收 + 告警
  （P2-NEW-1）。存量非法任务读取时标记 ``invalid_schedule`` 并留台账，不抛异常。
"""

from __future__ import annotations

import logging
import os
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from core import cron_expr
from core.clock import safe_timestamp_write, wall_now
from core.integrity import record as record_integrity
from core.settings_keys import KEY_ASSISTANT_DAILY_BRIEF_TIME

try:
    from apscheduler.schedulers.background import BackgroundScheduler
    from apscheduler.triggers.cron import CronTrigger
    from apscheduler.triggers.date import DateTrigger
    from apscheduler.triggers.interval import IntervalTrigger
except ImportError:
    BackgroundScheduler = None
    CronTrigger = None
    DateTrigger = None
    IntervalTrigger = None


logger = logging.getLogger("siyi.cron")

# 落盘时间戳格式（cron_jobs.json 的既有口径，勿改：_parse_datetime 依赖它）。
_TS_FMT = "%Y-%m-%d %H:%M:%S"

# 完整性台账 kind：schedule 形状非法（P2-NEW-1）。
KIND_CRON_SCHEDULE = "cron_schedule"

# 存量迁移白名单（批3 · P2-1）：这些 action 的历史 dow 数字**不是**标准 cron，
# 而是旧 ``_WEEKDAY_MAP``（Python 语义 0=Mon..6=Sun）由星期**名字**转换而来
# —— 数字本身丢了意图，只有种子代码知道它原本是哪个名字（``tech_watch.judge``
# 的 ``_WEEKDAY_MAP.get("sun", 6) -> 6``）。其余 job 的数字 dow 一律按标准
# cron 解读（用户/前端按标准 cron 书写）。
_LEGACY_PY_WEEKDAY_ACTIONS = frozenset({"tech_watch.judge"})


def _cron_matches(cron_expr_str: str, dt: datetime) -> bool:
    """内部补跑判据——委托 ``core.cron_expr.cron_matches``（两引擎同源）。"""
    return cron_expr.cron_matches(cron_expr_str, dt)


def _legacy_weekday_numbers_to_names(field: str) -> str:
    """旧 Python 语义数字 → 星期名字（仅存量迁移用，新代码禁止调用）。"""
    names = []
    for token in str(field or "").split(","):
        text = token.strip()
        if text.isdigit() and 0 <= int(text) <= 6:
            names.append(cron_expr.PY_WEEKDAY_TO_NAME[int(text)])
        elif text:
            names.append(text)
    return ",".join(names) if names else field


def _last_cron_trigger(expr: str, now: datetime, lookback_days: int = 35) -> datetime | None:
    """回溯找 cron 表达式在 now 之前最近一次触发时刻（分钟粒度）。

    用于冷启动补跑判定（D-9，2026-09-16）：last_run 早于该触发点即视为"错过"。
    窗口默认 35 天，覆盖 daily/weekly/monthly 全部合法 5 字段表达式；
    返回 None = 表达式无效（如误传 RRULE）或窗口内从未触发 → 调用方跳过补跑，
    绝不能让不可解析表达式引发重复执行或崩溃。
    """
    parts = str(expr or "").strip().split()
    if len(parts) != 5:
        return None
    day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    upper = now.replace(second=0, microsecond=0)
    for i in range(lookback_days + 1):
        d = day_start - timedelta(days=i)
        probe = upper if i == 0 else (d + timedelta(days=1))
        m = probe - timedelta(minutes=1)
        while m >= d:
            if _cron_matches(expr, m):
                return m
            m -= timedelta(minutes=1)
    return None


def _parse_datetime(value: str) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    for pattern in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M"):
        try:
            return datetime.strptime(text, pattern)
        except ValueError:
            continue
    return None


# ── N-A（2026-09-17）：任务队列优先级分层（与 task_queue.PRIORITY_* 数值对齐） ──
# cron 队列由单 worker 串行消费（headless_main cron_queue_worker），dequeue 按
# priority DESC, next_retry_at ASC 排序——冷启动补跑若与常规/手动执行同优先级，
# 分钟级的 tech_watch.collect 补跑会队头阻塞后续一切任务。分层：
#   手动 run_now(+1) > 常规调度(0) > 冷启动补跑(-1)
# 注：-1 低于 PRIORITY_NORMAL，正是"错峰垫底"语义；补跑之间按 job 注册顺序 FIFO。
PRIORITY_MANUAL_RUN = 1
PRIORITY_SCHEDULED = 0
PRIORITY_CATCHUP = -1

# 补跑队尾放大的"重"动作前缀（分钟级重活，优先级再降一档垫到最后）
# R2-D（2026-09-17）补齐叙事/报告族：这些 action 同为分钟级（LLM 生成 / 全库
# 扫描 + 收件箱投递），冷启动补跑时若与秒级轻任务同优先级，会重演 J3 队头阻塞
# （单 worker 串行 dequeue）。真名取自注册真源 services/cron_actions.py
# （register_cron_action 调用点）与 backend/handlers/_info_proxy.py：
# report.daily_summary / report.generate / evolution.scan_and_report /
# knowledge.brief / knowledge.synthesize / system.error_digest。
# startswith 元组匹配："report." 覆盖 report.generate 与 report.daily_summary，
# 且无轻任务误命中（对照回归：journal.daily_draft 是模板拼接 + 本地写，
# 不属分钟级重活，故**不**入清单）。
_CATCHUP_HEAVY_PREFIXES = (
    "tech_watch.collect",
    "subagent.spawn",
    "report.",
    "evolution.scan_and_report",
    "knowledge.brief",
    "knowledge.synthesize",
    "system.error_digest",
)
PRIORITY_CATCHUP_HEAVY = -2

# ── P0-4：连续失败告警配置（环境变量可调） ──
_ALERT_THRESHOLD_DEFAULT = 3
_ALERT_COOLDOWN_DEFAULT = 3600.0


def _resolve_alert_threshold() -> int:
    """连续失败告警阈值；<=0 表示禁用告警。"""
    try:
        return int(os.environ.get("SIYI_CRON_ALERT_THRESHOLD", str(_ALERT_THRESHOLD_DEFAULT)))
    except ValueError:
        return _ALERT_THRESHOLD_DEFAULT


def _resolve_alert_cooldown() -> float:
    """同一任务两次告警的最小间隔（秒），防告警风暴。"""
    try:
        return float(os.environ.get("SIYI_CRON_ALERT_COOLDOWN", str(_ALERT_COOLDOWN_DEFAULT)))
    except ValueError:
        return _ALERT_COOLDOWN_DEFAULT


# ── N-A（2026-09-17）：冷启动补跑错峰参数（环境变量可调） ──
_CATCHUP_STAGGER_DEFAULT = 30.0


def _resolve_catchup_stagger() -> float:
    """补跑逐条推迟的步长（秒）；0 表示关闭错峰（保持旧的齐射行为）。"""
    try:
        return max(0.0, float(os.environ.get("SIYI_CRON_CATCHUP_STAGGER", str(_CATCHUP_STAGGER_DEFAULT))))
    except ValueError:
        return _CATCHUP_STAGGER_DEFAULT


def _extract_time_parts(value: str) -> tuple[int, int]:
    parsed = _parse_datetime(value)
    if parsed is not None:
        return parsed.hour, parsed.minute
    text = str(value or "").strip()
    if len(text) >= 5 and ":" in text:
        try:
            hour_text, minute_text = text[-5:].split(":", 1)
            return max(0, min(int(hour_text), 23)), max(0, min(int(minute_text), 59))
        except ValueError:
            logger.debug("Non-critical error (see details)", exc_info=True)

    return 8, 0


@dataclass
class CronJob:
    job_id: str
    name: str
    action_id: str
    payload: dict[str, Any]
    schedule: str = ""
    run_at: str = ""
    repeat: str = "none"
    enabled: bool = True
    # interval 重复模式：每 N 天（repeat="interval" 时生效）
    run_every_days: int = 0
    last_run: str = ""
    created_at: str = ""
    next_run_at: str = ""
    max_retries: int = 0
    retry_count: int = 0
    retry_backoff: int = 60
    # P0-4：连续失败告警状态（跨调度周期累计，成功即清零）
    consecutive_failures: int = 0
    last_alert_at: str = ""
    # P2-NEW-1：schedule 形状非法（如 RRULE 误填）——读取时标记，不抛异常，
    # 使 cron.list 不会被存量坏数据整体搞崩；台账/告警由 _load_jobs 统一留痕。
    invalid_schedule: bool = False
    invalid_schedule_reason: str = ""
    history: list[dict[str, Any]] = field(default_factory=list)

    def __post_init__(self):
        if not self.created_at:
            self.created_at = wall_now().strftime(_TS_FMT)
        self.revalidate_schedule()

    def revalidate_schedule(self) -> None:
        """形状校验 + 星期规范化（批3 · P2-1/P2-NEW-1 的**单一收敛点**）。

        任何构造路径（``add_job`` / ``from_dict`` / 直接 ``CronJob(...)``）都经此，
        因此"数字 dow 残留"与"非法 schedule 静默入库"在结构上不可能发生。
        纯函数、无副作用——告警/台账由调用方在合适的时机留痕。
        """
        if self.repeat != "cron" or not self.schedule:
            self.invalid_schedule = False
            self.invalid_schedule_reason = ""
            return
        check = cron_expr.validate_schedule_shape(self.schedule)
        if check.ok:
            self.schedule = check.normalized
            self.invalid_schedule = False
            self.invalid_schedule_reason = ""
            return
        self.invalid_schedule = True
        self.invalid_schedule_reason = check.reason

    def to_dict(self) -> dict[str, Any]:
        return {
            "job_id": self.job_id,
            "name": self.name,
            "action_id": self.action_id,
            "payload": self.payload,
            "schedule": self.schedule,
            "run_at": self.run_at,
            "repeat": self.repeat,
            "enabled": self.enabled,
            "run_every_days": self.run_every_days,
            "last_run": self.last_run,
            "created_at": self.created_at,
            "next_run_at": self.next_run_at,
            "max_retries": self.max_retries,
            "retry_count": self.retry_count,
            "retry_backoff": self.retry_backoff,
            "consecutive_failures": self.consecutive_failures,
            "last_alert_at": self.last_alert_at,
            "invalid_schedule": self.invalid_schedule,
            "invalid_schedule_reason": self.invalid_schedule_reason,
            "history": list(self.history),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CronJob:
        return cls(
            job_id=str(data.get("job_id") or uuid.uuid4().hex[:12]),
            name=str(data.get("name") or ""),
            action_id=str(data.get("action_id") or ""),
            payload=dict(data.get("payload") or {}),
            schedule=str(data.get("schedule") or ""),
            run_at=str(data.get("run_at") or ""),
            repeat=str(data.get("repeat") or "none"),
            enabled=bool(data.get("enabled", True)),
            run_every_days=int(data.get("run_every_days", 0) or 0),
            last_run=str(data.get("last_run") or ""),
            created_at=str(data.get("created_at") or ""),
            next_run_at=str(data.get("next_run_at") or ""),
            max_retries=int(data.get("max_retries", 0) or 0),
            retry_count=int(data.get("retry_count", 0) or 0),
            retry_backoff=int(data.get("retry_backoff", 60) or 60),
            consecutive_failures=int(data.get("consecutive_failures", 0) or 0),
            last_alert_at=str(data.get("last_alert_at") or ""),
            history=list(data.get("history") or []),
        )

    def should_run_now(self, now: datetime) -> bool:
        if not self.enabled:
            return False
        # 重试调度优先级最高：若已安排重试且时间已到，立即执行
        if self.retry_count > 0 and self.next_run_at:
            next_run = _parse_datetime(self.next_run_at)
            if next_run is not None and next_run <= now:
                return True
        now_str = now.strftime("%Y-%m-%d %H:%M")
        if self.last_run and self.last_run[:16] == now_str:
            return False
        if self.repeat == "cron" and self.schedule:
            return _cron_matches(self.schedule, now)
        if self.repeat == "none" and self.run_at:
            return self.run_at[:16] <= now_str and not self.last_run
        if self.repeat in ("daily", "weekly", "monthly", "interval"):
            hour, minute = _extract_time_parts(self.run_at)
            if now.hour != hour or now.minute != minute:
                return False
            if self.last_run:
                last = _parse_datetime(self.last_run)
                if last is not None:
                    delta = now - last
                    if self.repeat == "daily":
                        return delta >= timedelta(hours=23)
                    if self.repeat == "weekly":
                        return delta >= timedelta(days=6)
                    if self.repeat == "monthly":
                        return delta >= timedelta(days=28)
                    if self.repeat == "interval":
                        return delta >= timedelta(days=max(1, self.run_every_days))
            return True
        return False


class CronService:
    POLL_INTERVAL = 60

    def __init__(
        self,
        data_store=None,
        observability_service=None,
        executor: Callable[[str, dict[str, Any]], dict[str, Any]] | None = None,
        settings_repository=None,
        task_queue=None,
        event_bus=None,
    ):
        self._data_store = data_store
        self._observability = observability_service
        self._executor = executor
        self._settings_repository = settings_repository
        self._task_queue = task_queue  # PersistentTaskQueue 实例，可选
        self._event_bus = event_bus  # EventBus 实例，用于任务状态通知
        self._trace_recorder = None  # P1-2：TraceRecorder，运行时注入（可选）
        self._jobs: list[CronJob] = []
        # AC-2：task_id → (job_id, history 条目引用) 的进程内关联索引。
        # dispatch 入队时登记，worker 终态回调 on_queue_task_final 据此把
        # history 里的 dispatched 条目翻转为真实结果。仅存内存：进程重启后
        # 索引丢失，历史条目保持 dispatched（"结果未知"），不伪造成败。
        self._dispatch_index: dict[str, tuple[str, dict[str, Any]]] = {}
        self._lock = threading.RLock()
        # P1-5：本进程**观测到的墙钟高水位**。墙钟倒退检测的 reference 必须是
        # "上次观测到的墙钟"（core.clock.detect_rollback 文档），而不是条目自身的
        # 时间戳——条目时间戳可能已被回拨污染，用它做基准检不出倒退。
        self._wall_high_water = 0.0
        self._running = False
        self._thread: threading.Thread | None = None
        self._scheduler = None
        self._engine = "poll"
        self._status = {"running": False, "engine": "poll", "job_count": 0, "last_error": ""}
        self._load_jobs()

    def set_task_queue(self, task_queue) -> None:
        """运行时注入 PersistentTaskQueue，使定时任务走持久队列执行。"""
        self._task_queue = task_queue
        logger.info("CronService 已接入 TaskQueue: %s", task_queue)

    def set_event_bus(self, event_bus) -> None:
        """运行时注入 EventBus，使任务执行状态可通知用户。"""
        self._event_bus = event_bus
        logger.info("CronService 已接入 EventBus: %s", event_bus)

    def set_trace_recorder(self, recorder) -> None:
        """P1-2：运行时注入 TraceRecorder，使定时任务执行纳入统一观测。"""
        self._trace_recorder = recorder
        logger.info("CronService 已接入 TraceRecorder: %s", recorder)

    def _notify(self, event_type: str, payload: dict[str, Any]) -> None:
        """通过 EventBus 发布任务状态通知（同步、容错）。

        事件类型:
          - cron.job_failed: 单次执行失败（将重试）
          - cron.job_exhausted: 重试耗尽，任务最终失败
          - cron.job_succeeded: 任务执行成功（仅 high 风险任务通知，避免噪音）

        批次5（2026-08-26）契约收敛：全仓库一致以 ``AppEvent`` 单参调用 ``emit``。
        真实 EventBus（core.events）只有 ``emit(AppEvent)``，无 ``emit_sync``；
        旧实现 `emit(event_type, payload)` 双参会在运行时被 try/except 吞掉，
        cron 通知实际从未经 EventBus 发出。此处：

          - 优先尝试真实统一契约：``emit(AppEvent(...))``
          - 若 bus 提供 ``emit_sync``（仅测试 mock 使用）则走之，保持兼容
          - 否则回退 ``emit(AppEvent(...))``
        """
        if self._event_bus is None:
            return
        from core.events import AppEvent

        # 兼容测试 mock（_FakeEventBus 提供同步 emit_sync），否则统一走 AppEvent 单参契约
        emit_sync = getattr(self._event_bus, "emit_sync", None)
        if emit_sync is not None:
            try:
                emit_sync(event_type, payload, source="cron_service")
                return
            except Exception:
                logger.debug("cron notify emit_sync failed: %s", event_type, exc_info=True)
        try:
            self._event_bus.emit(AppEvent(event_type, payload=payload, source="cron_service"))
        except Exception:
            logger.debug("cron notify failed: %s", event_type, exc_info=True)

    def _load_jobs(self):
        if self._data_store is None:
            self._jobs = []
            return
        migrated = 0
        try:
            raw = self._data_store.load_json("cron_jobs", [])
            # 批3 · P2-1 存量迁移：必须在 CronJob.from_dict（会按标准 cron 规范化）
            # **之前**对原始字面量动手——旧 `_WEEKDAY_MAP` 产出的数字一旦被当标准
            # cron 解读，tech_watch.judge 会从周日漂到周六（把修 2 个变成修坏 1 个）。
            for item in raw:
                if isinstance(item, dict) and self._migrate_legacy_schedule(item):
                    migrated += 1
            self._jobs = [CronJob.from_dict(item) for item in raw if isinstance(item, dict)]
        except Exception as exc:
            logger.warning("加载定时任务失败: %s", exc)
            self._jobs = []
        self._status["job_count"] = len(self._jobs)
        if migrated:
            logger.warning("cron 存量星期语义迁移：%d 个任务已按标准 cron（0=周日）改写", migrated)
            self._save_jobs()
        self._report_invalid_schedules()

    @staticmethod
    def _migrate_legacy_schedule(item: dict[str, Any]) -> bool:
        """把存量 job dict 的 schedule 一次性迁移到标准 cron 语义。

        仅对"星期字段是纯数字"的 cron 任务生效（名字写法无需迁移）。
        ``_LEGACY_PY_WEEKDAY_ACTIONS`` 里的 action 走旧 Python 语义还原名字，
        其余一律按标准 cron 解读——返回是否发生改动。
        """
        repeat = str(item.get("repeat") or "")
        schedule = str(item.get("schedule") or "")
        if repeat not in ("cron", "") or not cron_expr.is_numeric_weekday_literal(schedule):
            return False
        parts = cron_expr.split_fields(schedule)
        if parts is None:
            return False
        if str(item.get("action_id") or "") in _LEGACY_PY_WEEKDAY_ACTIONS:
            parts[4] = _legacy_weekday_numbers_to_names(parts[4])
        item["schedule"] = cron_expr.normalize_cron_schedule(" ".join(parts))
        return item["schedule"] != schedule

    def _report_invalid_schedules(self) -> None:
        """存量非法 schedule 的告警出口（P2-NEW-1 向后兼容）。

        刻意**不 raise**：`cron.list` 是用户排障的第一入口，一条坏数据不能让
        整个列表打不开。改为标记 + 台账 + WARNING，让"失败可观测"而非"失败即崩"。
        """
        for job in self._jobs:
            if not job.invalid_schedule:
                continue
            detail = (
                f"schedule 形状非法（{job.invalid_schedule_reason}），该任务不会被调度："
                f"{job.schedule!r}；请改成 5 字段 cron（星期用 sun/mon/... 名字）"
            )
            logger.warning("定时任务 schedule 非法: id=%s name=%s %s", job.job_id, job.name, detail)
            record_integrity(KIND_CRON_SCHEDULE, detail, target=job.job_id)

    def _validate_or_reject_schedule(self, schedule: str, repeat: str) -> None:
        """新增/更新路径的 schedule 形状校验：非法即**拒收**（P2-NEW-1）。

        拒收走 ValueError（RPC handler 已统一捕获并回 ``{"success": false, "error"}``），
        同时留台账/告警——"静默收下一个永不调度的死任务"是本缺陷的原形态。
        """
        if repeat != "cron":
            return
        check = cron_expr.validate_schedule_shape(schedule)
        if check.ok:
            return
        detail = f"拒收非法 schedule（{check.reason}）：{schedule!r} —— {check.hint}"
        logger.warning("定时任务 schedule 拒收: %s", detail)
        record_integrity(KIND_CRON_SCHEDULE, detail, target=schedule[:120])
        raise ValueError(detail)

    def _save_jobs(self):
        if self._data_store is None:
            return
        try:
            self._data_store.save_json("cron_jobs", [job.to_dict() for job in self._jobs])
        except Exception as exc:
            logger.warning("保存定时任务失败: %s", exc)

    def list_jobs(self, include_disabled: bool = False) -> list[dict[str, Any]]:
        with self._lock:
            return [job.to_dict() for job in self._jobs if include_disabled or job.enabled]

    def get_status_snapshot(self) -> dict[str, Any]:
        with self._lock:
            snapshot = dict(self._status)
            snapshot["job_count"] = len(self._jobs)
            return snapshot

    def add_job(
        self,
        name: str,
        action_id: str,
        payload: dict[str, Any] | None = None,
        schedule: str = "",
        run_at: str = "",
        repeat: str = "none",
        max_retries: int = 0,
        retry_backoff: int = 60,
        run_every_days: int = 0,
    ) -> dict[str, Any]:
        normalized_schedule, normalized_run_at, normalized_repeat = self._normalize_schedule(schedule, run_at, repeat)
        # P2-NEW-1：非法 schedule（RRULE 等）在**入口**拒收，绝不落库成死任务。
        self._validate_or_reject_schedule(normalized_schedule, normalized_repeat)
        job = CronJob(
            job_id=uuid.uuid4().hex[:12],
            name=name,
            action_id=action_id,
            payload=dict(payload or {}),
            schedule=normalized_schedule,
            run_at=normalized_run_at,
            repeat=normalized_repeat,
            run_every_days=max(1, int(run_every_days or 0)) if normalized_repeat == "interval" else int(run_every_days or 0),
            max_retries=max(0, int(max_retries or 0)),
            retry_backoff=max(1, int(retry_backoff or 60)),
        )
        with self._lock:
            # NL-1（2026-09-16）：完全相同规格且启用的 job 幂等去重。
            # 生产实测：test_integration_boundary 的 cron.add 残渣在生产 kv 累积
            # 8 条同名同 schedule 的 tool.zcode.open（remove 未净 + 无幂等保护）。
            # 按 (name, action_id, schedule, run_at, repeat, run_every_days, payload)
            # 命中既有启用 job → 直接返回该 job，不再 append。禁用 job 不算命中
            # （用户刻意停用后重新 add 视为新注册）。
            for existing in self._jobs:
                if (
                    existing.enabled
                    and existing.name == job.name
                    and existing.action_id == job.action_id
                    and existing.schedule == job.schedule
                    and existing.run_at == job.run_at
                    and existing.repeat == job.repeat
                    and existing.run_every_days == job.run_every_days
                    and existing.payload == job.payload
                ):
                    logger.info(
                        "定时任务幂等命中（不重复注册）: id=%s name=%s action=%s",
                        existing.job_id,
                        existing.name,
                        existing.action_id,
                    )
                    return existing.to_dict()
            self._jobs.append(job)
            self._refresh_job_schedule_locked(job)
            self._save_jobs()
            self._status["job_count"] = len(self._jobs)
        logger.info("新增定时任务: id=%s name=%s action=%s repeat=%s", job.job_id, name, action_id, normalized_repeat)
        return job.to_dict()

    def add_smart_job(
        self,
        action_id: str,
        name: str,
        *,
        peak_hour: int = 9,
        frequency: int = 5,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """根据习惯模式自动注册定时任务（HabitLearner 调用）。

        peak_hour: 习惯峰值小时（0-23），决定执行时间
        frequency: 历史出现次数，决定执行频率
        """
        freq = max(1, min(frequency, 100))
        hour_clamped = max(0, min(23, peak_hour))

        # 高频（>=10 次）→ 每日定时；中频（>=5 次）→ 工作日；低频 → 仅周一
        if freq >= 10:
            schedule = f"0 {hour_clamped} * * *"
            repeat = "cron"
        elif freq >= 5:
            # 批3：工作日写**名字** mon-fri（数字 1-5 在标准 cron 下同样是
            # 周一到周五，但数字会重新引入两套读法，见 core.cron_expr 模块说明）。
            schedule = f"0 {hour_clamped} * * mon-fri"
            repeat = "cron"
        else:
            schedule = ""
            run_at_smart = wall_now().replace(hour=hour_clamped, minute=0, second=0) + timedelta(days=1)
            return self.add_job(
                name=f"{name} (习惯)",
                action_id=action_id,
                payload=payload,
                schedule="",
                run_at=run_at_smart.strftime("%Y-%m-%d %H:%M"),
                repeat="once",
            )

        return self.add_job(
            name=f"{name} (习惯)",
            action_id=action_id,
            payload=payload,
            schedule=schedule,
            run_at="",
            repeat=repeat,
        )

    def remove_job(self, job_id: str) -> bool:
        with self._lock:
            for index, job in enumerate(self._jobs):
                if job.job_id != job_id:
                    continue
                self._remove_from_scheduler_locked(job.job_id)
                self._jobs.pop(index)
                self._save_jobs()
                self._status["job_count"] = len(self._jobs)
                return True
        return False

    def toggle_job(self, job_id: str, enabled: bool) -> bool:
        with self._lock:
            for job in self._jobs:
                if job.job_id != job_id:
                    continue
                job.enabled = enabled
                self._refresh_job_schedule_locked(job)
                self._save_jobs()
                return True
        return False

    def toggle_jobs(self, job_ids: list[str], enabled: bool) -> dict[str, Any]:
        """U2（S3-5，2026-09-16）：批量启用/禁用定时任务。

        单锁内一次遍历、一次落盘——区别于调用方循环 N 次 toggle_job
        （N 次磁盘写 + N 次调度器刷新，中途崩溃会留下半批生效的中间态）。
        供「一键静音自主任务」等批量操作使用；job_ids 由调用方（前端）
        按分类挑选，后端只提供通用批量原语。

        Returns:
            {"changed": [已切换的 job 字典], "not_found": [未命中的 id]}
        """
        wanted = {str(jid).strip() for jid in (job_ids or []) if str(jid).strip()}
        changed: list[dict[str, Any]] = []
        with self._lock:
            found = set()
            for job in self._jobs:
                if job.job_id not in wanted:
                    continue
                found.add(job.job_id)
                if job.enabled == enabled:
                    continue
                job.enabled = enabled
                self._refresh_job_schedule_locked(job)
                changed.append(job.to_dict())
            if changed:
                self._save_jobs()
        not_found = sorted(wanted - found)
        if not_found:
            logger.warning("批量切换定时任务未命中 id: %s", not_found)
        return {"changed": changed, "not_found": not_found}

    def update_job(
        self,
        job_id: str,
        name: str | None = None,
        action_id: str | None = None,
        payload: dict[str, Any] | None = None,
        schedule: str | None = None,
        run_at: str | None = None,
        repeat: str | None = None,
        max_retries: int | None = None,
        retry_backoff: int | None = None,
        run_every_days: int | None = None,
    ) -> dict[str, Any] | None:
        """更新指定定时任务的可变字段，保留其余字段不变。"""
        with self._lock:
            for job in self._jobs:
                if job.job_id != job_id:
                    continue
                # P2-NEW-1：先校验、后落值——否则"改个名 + 传了坏 schedule"会留下
                # 半改状态（名字变了、schedule 没变），比整体拒收更难排查。
                # 只对**显式传入**的 schedule 做拒收：存量坏任务改个名字也要能改
                # （否则用户无法把死任务救回来，只能删掉重建）。
                current_schedule = schedule if schedule is not None else job.schedule
                current_run_at = run_at if run_at is not None else job.run_at
                current_repeat = repeat if repeat is not None else job.repeat
                normalized_schedule, normalized_run_at, normalized_repeat = self._normalize_schedule(current_schedule, current_run_at, current_repeat)
                if schedule is not None:
                    self._validate_or_reject_schedule(normalized_schedule, normalized_repeat)
                if name is not None:
                    job.name = str(name).strip() or job.name
                if action_id is not None:
                    job.action_id = str(action_id).strip() or job.action_id
                if payload is not None:
                    job.payload = dict(payload)
                job.schedule = normalized_schedule
                job.run_at = normalized_run_at
                job.repeat = normalized_repeat
                job.revalidate_schedule()
                if run_every_days is not None:
                    job.run_every_days = max(1, int(run_every_days or 0)) if normalized_repeat == "interval" else int(run_every_days or 0)
                if max_retries is not None:
                    job.max_retries = max(0, int(max_retries or 0))
                if retry_backoff is not None:
                    job.retry_backoff = max(1, int(retry_backoff or 60))
                job.retry_count = 0
                self._refresh_job_schedule_locked(job)
                self._save_jobs()
                return job.to_dict()
        return None

    def start(self):
        with self._lock:
            if self._running:
                return self.get_status_snapshot()
            self._running = True
            self._engine = self._resolve_engine()
            self._status.update({"running": True, "engine": self._engine, "last_error": ""})
            if self._engine == "apscheduler":
                self._start_apscheduler_locked()
            else:
                self._thread = threading.Thread(target=self._monitor_loop, daemon=True, name="cron-service")
                self._thread.start()
        logger.info("CronService 已启动 (engine=%s)", self._engine)
        # P1-2026-08-18：错过后补跑 —— 早间任务在 app 长时间未运行后启动时立即补跑
        self._catch_up_missed()
        return self.get_status_snapshot()

    def _observe_wall(self, now_ts: float) -> float:
        """记录并返回本服务**观测到的墙钟高水位**（P1-5 倒退检测的基准）。

        为什么不能用条目自身的时间戳做基准：``last_run`` 一旦被回拨污染，
        再拿它当 reference 就永远检不出倒退（污染值本身就"在将来"）。
        高水位随进程存活累积，是"上次观测到的墙钟"的可靠代理。
        局限：进程重启后归零——"开机即处于错误时钟"的场景无法自证，已在报告注明。
        """
        self._wall_high_water = max(self._wall_high_water, float(now_ts))
        return self._wall_high_water

    def _safe_last_run_write(self, job: CronJob, now: datetime) -> None:
        """``last_run`` 的**防毒写**——本模块唯一允许写 last_run 的入口（P1-5 治本）。

        回拨场景实证（2026-09-18 实测）：墙钟被回拨后补跑把 ``last_run`` 写成
        "过去的"时间，时钟恢复后同一触发点又被判"错过" → **双触发**。
        经 ``core.clock.safe_timestamp_write`` 后，``last_run`` 在墙钟倒退时
        **拒绝后退**（保留基准值），恢复后 ``last < trig`` 不成立 → 不再二次触发。

        基准取 ``max(高水位, 既有 last_run)``：高水位管"回拨已发生"，既有值管
        "last_run 自身已被写成将来"（跨进程/跨重启场景）。
        """
        now_ts = float(now.timestamp())
        reference = self._observe_wall(now_ts)
        previous = _parse_datetime(job.last_run)
        previous_ts = float(previous.timestamp()) if previous is not None else None
        baseline = reference if previous_ts is None else max(reference, previous_ts)
        written = safe_timestamp_write(now_ts, baseline)
        job.last_run = datetime.fromtimestamp(written).strftime(_TS_FMT)

    def _catch_up_missed(self) -> None:
        """错过后补跑：app 冷启动时检查周期任务是否逾期未执行，是则立即补跑。

        仅对 daily/weekly/monthly 且 enabled 的任务；判定"逾期"：
          - last_run 为空（从未执行）且今天该时间点已过 → 补跑
          - last_run 距今超过周期阈值（daily≥23h / weekly≥6d / monthly≥28d）→ 补跑
        补跑调用 _execute_job（自动入队持久队列，崩溃可恢复）。
        """
        now = wall_now()
        # P1-5：每次调度巡检都记一次墙钟观测——高水位是倒退检测的基准，
        # 只在写 last_run 时才观测的话，"服务一直在跑、某任务被启用后才回拨"
        # 这类路径就检不出倒退。
        self._observe_wall(now.timestamp())
        overdue: list[CronJob] = []
        with self._lock:
            for job in self._jobs:
                if not job.enabled:
                    continue
                # D-9 修复（2026-09-16）：repeat=cron 的 5 字段任务同样需要补跑。
                # 既往只认 daily/weekly/monthly/interval，导致用户作息转为晚间开机后，
                # 晨间/深夜 cron 任务（早报组/备份/巡检）错过触发点即永久饿死——
                # 生产实测 11 个 cron 任务自 08-19 起 last_run 集体冻结、零死信零告警。
                if job.repeat == "cron":
                    trig = _last_cron_trigger(job.schedule, now)
                    if trig is None:
                        continue  # 表达式不可解析（如 RRULE 误填）→ 不补跑，防重复执行
                    last = _parse_datetime(job.last_run)
                    if last is None or last < trig:
                        overdue.append(job)
                    continue
                if job.repeat not in ("daily", "weekly", "monthly", "interval"):
                    continue
                try:
                    hour, minute = _extract_time_parts(job.run_at)
                except Exception:  # noqa: BLE001
                    continue
                # 今天该时间点尚未到 → 等到点自然触发，不补跑
                if (now.hour, now.minute) < (hour, minute):
                    continue
                missed = False
                if not job.last_run:
                    missed = True
                else:
                    last = _parse_datetime(job.last_run)
                    if last is not None:
                        delta = now - last
                        if job.repeat == "daily" and delta >= timedelta(hours=23):
                            missed = True
                        elif job.repeat == "weekly" and delta >= timedelta(days=6):
                            missed = True
                        elif job.repeat == "monthly" and delta >= timedelta(days=28):
                            missed = True
                        elif job.repeat == "interval" and delta >= timedelta(days=max(1, job.run_every_days)):
                            missed = True
                if missed:
                    overdue.append(job)
            if overdue:
                # 标记 last_run 避免 monitor 周期内重复补跑；经防毒写入口，
                # 墙钟回拨时不把 last_run 拖回过去（否则恢复后双触发）。
                for job in overdue:
                    self._safe_last_run_write(job, now)
                    self._save_jobs()
        # N-A（2026-09-17）补跑错峰：冷启动一次性灌入十几个补跑任务时，
        # cron 队列单 worker 串行消费 → 分钟级网络任务（tech_watch.collect /
        # subagent.spawn）排队头阻塞后续一切任务（隔离实例 J3 实测 15s 窗口
        # 必超时；生产 22:11 早报死信同根因）。对策：
        #   1) 轻重分层——重动作排到补跑序列尾部，且入队优先级再降一档
        #      （PRIORITY_CATCHUP_HEAVY，dequeue 时垫底）；
        #   2) 逐条 stagger——每条推迟 idx*stagger 秒才可调度（postpone），
        #      摊平入队风暴；
        #   3) 用户手动 cron.run_now 以 PRIORITY_MANUAL_RUN(+1) 入队，
        #      无论补跑队列多长都插队到最前，不再"立即执行却排在分钟级任务后"。
        stagger = _resolve_catchup_stagger()
        heavy = [j for j in overdue if j.action_id.startswith(_CATCHUP_HEAVY_PREFIXES)]
        heavy_ids = {j.job_id for j in heavy}
        ordered = [j for j in overdue if j.job_id not in heavy_ids] + heavy
        for idx, job in enumerate(ordered):
            priority = PRIORITY_CATCHUP_HEAVY if job.job_id in heavy_ids else PRIORITY_CATCHUP
            logger.info(
                "定时任务错过后补跑: id=%s name=%s action=%s priority=%s defer=%.0fs",
                job.job_id,
                job.name,
                job.action_id,
                priority,
                idx * stagger,
            )
            self._execute_job(job, priority=priority, defer_seconds=idx * stagger)

    def stop(self):
        with self._lock:
            self._running = False
            thread = self._thread
            self._thread = None
            scheduler = self._scheduler
            self._scheduler = None
        if scheduler is not None:
            try:
                scheduler.shutdown(wait=False)
            except Exception:
                logger.debug("Cron scheduler shutdown failed", exc_info=True)
        if thread is not None and thread.is_alive():
            thread.join(timeout=5)
        with self._lock:
            self._save_jobs()
            self._status["running"] = False
        logger.info("CronService 已停止")

    def _resolve_engine(self) -> str:
        configured = "auto"
        if self._settings_repository is not None:
            try:
                configured = str(self._settings_repository.get_setting("cron_engine", "auto") or "auto").strip().lower()
            except Exception:
                configured = "auto"
        if configured == "poll":
            return "poll"
        if BackgroundScheduler is not None:
            return "apscheduler"
        return "poll"

    def _start_apscheduler_locked(self) -> None:
        if BackgroundScheduler is None:
            self._engine = "poll"
            self._thread = threading.Thread(target=self._monitor_loop, daemon=True, name="cron-service")
            self._thread.start()
            return
        scheduler = BackgroundScheduler(job_defaults={"coalesce": True, "max_instances": 1})
        scheduler.start()
        self._scheduler = scheduler
        for job in self._jobs:
            self._refresh_job_schedule_locked(job)

    def _monitor_loop(self):
        while self._running:
            try:
                self._tick()
            except Exception as exc:
                logger.error("CronService tick 异常: %s", exc)
                with self._lock:
                    self._status["last_error"] = str(exc)
            time.sleep(self.POLL_INTERVAL)

    def _tick(self):
        now = wall_now()
        self._observe_wall(now.timestamp())
        with self._lock:
            jobs_to_run = [job for job in self._jobs if job.should_run_now(now)]
            for job in self._jobs:
                self._update_next_run_fallback_locked(job, now)
            self._save_jobs()
        for job in jobs_to_run:
            self._execute_job(job)

    def _record_history_locked(
        self,
        job: CronJob,
        now: datetime,
        success: bool,
        result: dict[str, Any],
    ) -> None:
        """记录单次执行历史，限制单任务历史条目数。"""
        entry = {
            "timestamp": now.strftime("%Y-%m-%d %H:%M:%S"),
            "success": success,
            "output": result.get("output") if isinstance(result.get("output"), str) else None,
            "error": result.get("error") if not success else None,
        }
        # AC-2：透传 status（dispatched/completed/dead_letter…），供 worker 终态
        # 回写翻转时识别 dispatched 占位条目；普通执行无 status 时不写键，
        # 保持既有信封字段不变。
        _status_field = result.get("status") if isinstance(result, dict) else None
        if _status_field:
            entry["status"] = str(_status_field)
        job.history.append(entry)
        # 限制历史长度，避免无界增长
        max_history = 50
        if len(job.history) > max_history:
            job.history = job.history[-max_history:]
            # AC-2：环形淘汰后收敛 dispatch 索引，防止无界增长
            self._prune_dispatch_index_locked()
        # P1-2：直接执行的最终结果同步写入统一观测（dispatched 入队条目不记，
        # 其队列层观测由 AC-2 回写链补记——见 on_queue_task_final 的
        # start_run/finish_run；job.history 保留兼容现有 cron.history RPC）
        recorder = self._trace_recorder
        if recorder is not None and result.get("status") != "dispatched":
            try:
                task_id = f"cron-{job.job_id}-{int(now.timestamp())}"
                recorder.start_run(task_id, "cron", title=job.name, source="cron")
                recorder.finish_run(
                    task_id,
                    "completed" if success else "failed",
                    error="" if success else str(result.get("error") or "")[:300],
                )
            except Exception:  # noqa: BLE001
                logger.debug("cron trace record failed", exc_info=True)

    def _schedule_retry_locked(self, job: CronJob, error: str) -> None:
        """指数退避安排下一次重试。"""
        attempt = job.retry_count
        delay = min(job.retry_backoff * (2 ** (attempt - 1)), 3600)
        next_run = wall_now() + timedelta(seconds=delay)
        job.next_run_at = next_run.strftime(_TS_FMT)
        logger.warning(
            "定时任务触发重试: id=%s attempt=%s delay=%ss next=%s error=%s",
            job.job_id,
            attempt,
            delay,
            job.next_run_at,
            error,
        )
        if self._engine == "apscheduler" and self._scheduler is not None:
            self._remove_from_scheduler_locked(job.job_id)
            try:
                self._scheduler.add_job(
                    self._run_job,
                    trigger=DateTrigger(run_date=next_run),
                    args=[job.job_id],
                    id=job.job_id,
                    replace_existing=True,
                )
            except Exception:
                logger.warning("failed to schedule retry for job %s", job.job_id, exc_info=True)

    def _prune_dispatch_index_locked(self) -> None:
        """AC-2：清理 dispatch 索引中已被历史环形（max_history=50）淘汰的条目引用。

        job.history 截断后，索引里指向被丢弃 dict 的映射永远翻转不到可见历史，
        留着只会无界增长；在 _load_jobs 之后顺带收敛一次即可。
        """
        if not self._dispatch_index:
            return
        alive: set[int] = set()
        for job in self._jobs:
            for entry in job.history:
                alive.add(id(entry))
        dead = [tid for tid, (_jid, entry) in self._dispatch_index.items() if id(entry) not in alive]
        for tid in dead:
            self._dispatch_index.pop(tid, None)

    def on_queue_task_final(
        self,
        task_id: str,
        *,
        final_status: str,
        success: bool | None = None,
        error: str = "",
        output: str = "",
    ) -> "dict[str, Any] | None":
        """AC-2 回写链：TaskQueue worker 到达终态后回写对应 cron job 的 dispatched 历史条目。

        dispatch 入队时 ``_execute_job`` 记了一条
        ``{success: False, status: "dispatched"}`` 占位（语义是"仅入队未执行"，不是
        失败）；worker ``complete``/``fail`` 落到真终态时经本方法把**同一条目**翻转
        为真实结果，使 cron.history 不再永远停留在 dispatched：

        - completed → success=True, status="completed", 清 error（output 可带执行摘要）
        - dead_letter → success=False, status="dead_letter", error=错误摘要
        - 重试中间态（fail 后回 pending）不调本方法——终态只报 completed/dead_letter

        设计约束：只翻转 dispatched 条目（重复/迟到回调幂等安全，已终态条目绝不
        覆盖）；仅进程内索引——重启后结果未知，历史条目保持 dispatched。
        由 headless_main 注入 worker 的 on_task_final 回调；异常不得冒泡进 worker
        主循环（调用点已 try 包裹），本方法内部亦全程容错。

        Returns:
            翻转后的 history 条目；无关联（非 cron 任务/重启后/已终态）返回 None。
        """
        tid = str(task_id or "")
        if not tid:
            return None
        with self._lock:
            mapped = self._dispatch_index.pop(tid, None)
            if mapped is None:
                return None
            job_id, entry = mapped
            if entry.get("status") != "dispatched":
                return None  # 已被翻转过——迟到/重复回调，不覆盖真实终态
            ok = success if isinstance(success, bool) else (final_status == "completed")
            entry["success"] = bool(ok)
            entry["status"] = str(final_status or ("completed" if ok else "failed"))
            entry["output"] = str(output or f"{entry['status']}_by_task_queue:{tid}")[:500]
            if ok:
                entry["error"] = None
            else:
                entry["error"] = (str(error).strip() or f"queue_task_{entry['status']}")[:500]
            # 队列层真终态补记统一观测：_record_history_locked 的 trace 记录有意
            # 跳过 dispatched 占位（见其 P1-2 注释），回写翻转后在此补上
            # start_run/finish_run——队列层观测已由 history 回写覆盖，不留两处半截。
            recorder = self._trace_recorder
            if recorder is not None:
                try:
                    rec_task_id = f"cron-{job_id}-{tid}"
                    recorder.start_run(rec_task_id, "cron", source="cron")
                    recorder.finish_run(
                        rec_task_id,
                        "completed" if ok else "failed",
                        error="" if ok else str(entry["error"])[:300],
                    )
                except Exception:  # noqa: BLE001
                    logger.debug("cron writeback trace record failed", exc_info=True)
            job = next((item for item in self._jobs if item.job_id == job_id), None)
            if job is not None:
                # 连续失败告警语义与直接执行路径对齐：入队成功≠执行失败，只有
                # worker 真终态才计入 consecutive_failures。
                try:
                    self._track_final_result_locked(job, bool(ok), str(entry.get("error") or ""))
                except Exception:  # noqa: BLE001
                    logger.debug("cron writeback track_final failed", exc_info=True)
                self._save_jobs()
            return entry

    def get_job_history(self, job_id: str, limit: int = 20) -> list[dict[str, Any]]:
        """获取指定任务的执行历史。"""
        with self._lock:
            job = next((item for item in self._jobs if item.job_id == job_id), None)
            if job is None:
                return []
            return list(job.history[-max(1, int(limit)) :])

    def _track_final_result_locked(self, job: CronJob, success: bool, error: str) -> None:
        """P0-4：跟踪周期最终结果 —— 成功清零，失败累计并按阈值/冷却发告警。

        与 cron.job_failed（单次失败将重试）/cron.job_exhausted（重试耗尽）
        互补：job_alert 反映跨调度周期的持续健康问题（24 小时监控承诺）。
        需在持有 self._lock 时调用。
        """
        if success:
            if job.consecutive_failures:
                logger.info(
                    "定时任务恢复正常: id=%s (此前连续失败 %d 次)",
                    job.job_id,
                    job.consecutive_failures,
                )
            job.consecutive_failures = 0
            return
        job.consecutive_failures += 1
        threshold = _resolve_alert_threshold()
        if threshold <= 0 or job.consecutive_failures < threshold:
            return
        cooldown = _resolve_alert_cooldown()
        now = wall_now()
        last = _parse_datetime(job.last_alert_at) if job.last_alert_at else None
        if last is not None and (now - last).total_seconds() < cooldown:
            return
        job.last_alert_at = now.strftime(_TS_FMT)
        logger.error(
            "定时任务连续失败告警: id=%s name=%s consecutive=%d",
            job.job_id,
            job.name,
            job.consecutive_failures,
        )
        self._notify(
            "cron.job_alert",
            {
                "job_id": job.job_id,
                "job_name": job.name,
                "action_id": job.action_id,
                "consecutive_failures": job.consecutive_failures,
                "error": (error or "unknown error")[:300],
            },
        )

    def _execute_job(
        self,
        job: CronJob,
        *,
        priority: int = 0,
        defer_seconds: float = 0.0,
    ) -> "dict[str, Any] | None":
        """执行一次任务：有 TaskQueue 走持久队列（dispatched），否则直跑。

        priority=1 入队提权（cron.run_now 旁路补跑队头）；
        defer_seconds>0 时入队后把 next_retry_at 推后，实现补跑错峰
        （避免冷启动风暴中分钟级网络任务霸占单 worker 队列头）。
        返回：dispatched 路径返回入队信息 dict；直跑路径返回 executor 结果；
        入队失败走内部失败分支时返回 None（历史/通知已就地记录）。
        """
        logger.info(
            "触发定时任务: id=%s name=%s action=%s attempt=%s",
            job.job_id,
            job.name,
            job.action_id,
            job.retry_count + 1,
        )

        # ── TaskQueue 优先路径：持久化执行，崩溃后自动恢复 ──
        if self._task_queue is not None:
            try:
                task_id = self._task_queue.enqueue(
                    capability_id=job.action_id,
                    payload=dict(job.payload),
                    queue_name="cron",
                    priority=int(priority),
                    max_retries=max(job.max_retries, 1),
                    backoff_base=max(job.retry_backoff, 1.0),
                    backoff_cap=3600.0,
                )
                if defer_seconds > 0:
                    try:
                        self._task_queue.postpone(task_id, defer_seconds)
                    except Exception:
                        logger.warning(
                            "补跑错峰失败，退回立即可调度: job=%s task_id=%s",
                            job.job_id,
                            task_id,
                            exc_info=True,
                        )
                now = wall_now()
                with self._lock:
                    # P2-fix: 标记为 dispatched（非 success），任务仅入队未经执行
                    self._record_history_locked(job, now, False, {"output": f"dispatched_to_task_queue:{task_id}", "status": "dispatched"})
                    # AC-2：登记 task_id↔(job_id, 该 dispatched 条目) 关联，worker
                    # 终态时由 on_queue_task_final 把同一条目翻转为真实结果。
                    # 索引封顶防泄漏：任务被 cancel/租约过期等路径永不到达终态时，
                    # 未翻转条目保持 dispatched（"结果未知"），不伪造成败。
                    if job.history:
                        while len(self._dispatch_index) >= 1000:
                            self._dispatch_index.pop(next(iter(self._dispatch_index)), None)
                        self._dispatch_index[str(task_id)] = (job.job_id, job.history[-1])
                    self._safe_last_run_write(job, now)
                    job.retry_count = 0
                    if job.repeat == "none":
                        job.enabled = False
                    self._refresh_job_schedule_locked(job)
                    self._save_jobs()
                logger.info("定时任务已入队持久队列: job=%s task_id=%s", job.job_id, task_id)
                return {"dispatched": True, "task_id": str(task_id)}
            except Exception as exc:
                error: "str | None" = str(exc)
                logger.error("定时任务入队失败，不执行任务: id=%s error=%s", job.job_id, error)
                now = wall_now()
                with self._lock:
                    result: "dict[str, Any]" = {"error": error, "dispatch_failed": True}
                    self._record_history_locked(job, now, False, result)
                    if job.retry_count < job.max_retries:
                        job.retry_count += 1
                        try:
                            self._schedule_retry_locked(job, error or "unknown error")
                        except Exception:
                            logger.error(
                                "定时任务重试安排失败，保留失败状态: job=%s",
                                job.job_id,
                                exc_info=True,
                            )
                        # 通知: 入队失败，将重试
                        self._notify(
                            "cron.job_failed",
                            {
                                "job_id": job.job_id,
                                "job_name": job.name,
                                "action_id": job.action_id,
                                "error": f"dispatch_failed: {error}",
                                "retry_count": job.retry_count,
                                "max_retries": job.max_retries,
                                "next_run_at": job.next_run_at,
                            },
                        )
                    else:
                        job.retry_count = 0
                        if job.repeat == "none":
                            job.enabled = False
                        self._refresh_job_schedule_locked(job)
                        # P0-4：入队失败且重试耗尽，计入连续失败
                        self._track_final_result_locked(job, False, f"dispatch_failed: {error}")
                        # 通知: 入队失败，重试耗尽
                        self._notify(
                            "cron.job_exhausted",
                            {
                                "job_id": job.job_id,
                                "job_name": job.name,
                                "action_id": job.action_id,
                                "error": f"dispatch_failed: {error}",
                                "max_retries": job.max_retries,
                            },
                        )
                    self._save_jobs()
                # N-A：入队失败如实回传（run_job_now 据此不谎报 dispatched 成功）
                return {"dispatch_failed": True, "error": error}

        # ── 直接执行路径（无 TaskQueue 或入队失败时的降级）──
        result = {}
        error = None
        try:
            if self._executor is not None:
                result = self._executor(job.action_id, dict(job.payload)) or {}
            error = result.get("error") or None
        except Exception as exc:
            logger.error("定时任务执行失败: id=%s error=%s", job.job_id, exc)
            error = str(exc)
            result = {"error": error}

        # P0-fix(4): success 以 result["success"] 为权威，缺失时才回退到 error 判断。
        # warning/error 文本不覆盖 executor 明确返回的 success=True。
        # H3: 加 isinstance 守卫——executor 返回非 dict（dataclass/str）时避免 `in` 行为不确定
        if isinstance(result, dict) and "success" in result:
            success = bool(result["success"])
        else:
            success = error is None
        now = wall_now()
        with self._lock:
            self._record_history_locked(job, now, success, result)
            self._safe_last_run_write(job, now)

            if not success and job.retry_count < job.max_retries:
                job.retry_count += 1
                self._schedule_retry_locked(job, error or "unknown error")
                self._save_jobs()
                # 通知: 单次失败，将重试
                self._notify(
                    "cron.job_failed",
                    {
                        "job_id": job.job_id,
                        "job_name": job.name,
                        "action_id": job.action_id,
                        "error": error or "unknown error",
                        "retry_count": job.retry_count,
                        "max_retries": job.max_retries,
                        "next_run_at": job.next_run_at,
                    },
                )
                return dict(result) if isinstance(result, dict) else {"success": False}

            # 成功或重试耗尽
            # P0-4：周期最终结果跟踪（成功清零/失败累计告警）
            self._track_final_result_locked(job, success, error or "unknown error")
            retry_exhausted = not success and job.max_retries > 0
            job.retry_count = 0
            if job.repeat == "none":
                job.enabled = False
            self._refresh_job_schedule_locked(job)
            self._save_jobs()

        # 通知: 重试耗尽（最终失败）
        if retry_exhausted:
            self._notify(
                "cron.job_exhausted",
                {
                    "job_id": job.job_id,
                    "job_name": job.name,
                    "action_id": job.action_id,
                    "error": error or "unknown error",
                    "max_retries": job.max_retries,
                },
            )
        # 通知: 成功（仅 max_retries > 0 的任务通知，避免普通成功任务产生噪音）
        elif success and job.max_retries > 0:
            self._notify(
                "cron.job_succeeded",
                {
                    "job_id": job.job_id,
                    "job_name": job.name,
                    "action_id": job.action_id,
                },
            )

        if self._observability is not None:
            try:
                self._observability.track_event(
                    "cron.job_executed",
                    "info" if success else "degraded",
                    {
                        "job_id": job.job_id,
                        "action_id": job.action_id,
                        "success": success,
                        "retry_count": job.retry_count,
                    },
                )
            except Exception:
                logger.debug("cron observability skipped", exc_info=True)
        # N-A：直跑路径把最终结果如实返回，供 run_job_now 判定真实成败
        # （杜绝"入队成功≠执行成功"的假成功）。
        return dict(result) if isinstance(result, dict) else {"success": bool(success)}

    def _run_job(self, job_id: str) -> None:
        """调度器/重试入口（返回值不消费）；如实语义见 run_job_now。"""
        self.run_job_now(job_id)

    def run_job_now(self, job_id: str, *, priority: int = 0) -> dict[str, Any]:
        """立即触发一次任务，如实报告触发结果（杜绝假成功）。

        与旧 _run_job 的区别：不再对 job 不存在 / 已禁用静默 return，
        而是返回结构化结果供 cron.run_now RPC 映射为 success/false：
          - triggered + dispatched：已入队持久队列（真实结果由 AC-2 终态回写翻转）
          - triggered + executed：无 TaskQueue 时同步直跑，带回执行结果
          - not_triggered：任务不存在 / 已禁用（禁用任务不可手动绕过，需先启用）

        priority=1（PRIORITY_HIGH）时入队提权，插队到补跑/周期任务之前。
        """
        with self._lock:
            job = next((item for item in self._jobs if item.job_id == job_id), None)
        if job is None:
            return {"triggered": False, "reason": "not_found", "error": "job not found"}
        if not job.enabled:
            return {"triggered": False, "reason": "disabled", "error": "job is disabled"}
        if self._task_queue is not None:
            dispatch = self._execute_job(job, priority=int(priority))
            if isinstance(dispatch, dict) and dispatch.get("dispatch_failed"):
                # 入队失败 ≠ 触发成功——如实报告，不谎报 dispatched
                return {
                    "triggered": False,
                    "reason": "dispatch_failed",
                    "error": str(dispatch.get("error") or "dispatch failed"),
                    "job_id": job.job_id,
                }
            # 手动提权：enqueue 对同 (queue,capability,payload) 的 pending 任务会去重
            # 复用旧 task_id（不重新提权/清 stagger）。补跑刚把该任务 postpone 推后时，
            # 用户点「立即执行」必须能插队——拿回 task_id 显式 promote 到即时可调度。
            if int(priority) > 0 and isinstance(dispatch, dict) and dispatch.get("task_id"):
                try:
                    self._task_queue.promote(str(dispatch["task_id"]), priority=int(priority))
                except Exception:
                    logger.debug("cron.run_now promote failed", exc_info=True)
            return {"triggered": True, "mode": "dispatched", "job_id": job.job_id}
        result = self._execute_job(job, priority=int(priority))
        outcome: dict[str, Any] = {"triggered": True, "mode": "executed", "job_id": job.job_id}
        if isinstance(result, dict):
            success = result.get("success")
            if success is not None:
                outcome["success"] = bool(success)
            err = result.get("error")
            if err and not success:
                outcome["error"] = str(err)
        return outcome

    def _refresh_job_schedule_locked(self, job: CronJob) -> None:
        self._remove_from_scheduler_locked(job.job_id)
        if not job.enabled:
            job.next_run_at = ""
            return
        if self._engine == "apscheduler" and self._scheduler is not None:
            trigger = self._build_trigger(job)
            if trigger is None:
                job.next_run_at = ""
                return
            scheduled = self._scheduler.add_job(
                self._run_job,
                trigger=trigger,
                args=[job.job_id],
                id=job.job_id,
                replace_existing=True,
            )
            next_run = getattr(scheduled, "next_run_time", None)
            job.next_run_at = next_run.strftime("%Y-%m-%d %H:%M:%S") if next_run is not None else ""
            return
        self._update_next_run_fallback_locked(job, wall_now())

    def _remove_from_scheduler_locked(self, job_id: str) -> None:
        if self._scheduler is None:
            return
        try:
            self._scheduler.remove_job(job_id)
        except Exception:
            logger.warning("failed to remove job %s from scheduler", job_id)

    def _build_trigger(self, job: CronJob):
        if CronTrigger is None or DateTrigger is None:
            return None
        if job.repeat == "cron" and job.schedule:
            # P2-1：APScheduler 与内部判据必须消费**同一份**规范化结果。
            # 旧实现把 dow 原样字符串交给 CronTrigger（其数字语义 0=Mon），
            # 只改内部判据会立刻造成两引擎分裂 → 双触发/漏跑。此处显式经
            # cron_expr 校验+规范化（星期已是名字，无歧义）。
            check = cron_expr.validate_schedule_shape(job.schedule)
            if not check.ok:
                return None
            minute_s, hour_s, day_s, month_s, dow_s = cron_expr.split_fields(check.normalized)
            return CronTrigger(minute=minute_s, hour=hour_s, day=day_s, month=month_s, day_of_week=dow_s)
        if job.repeat == "interval":
            if IntervalTrigger is None or job.run_every_days < 1:
                return None
            hour, minute = _extract_time_parts(job.run_at)
            start = wall_now().replace(hour=hour, minute=minute, second=0, microsecond=0)
            if start <= wall_now():
                start += timedelta(days=job.run_every_days)
            return IntervalTrigger(days=job.run_every_days, start_date=start)
        if job.repeat == "none":
            run_date = _parse_datetime(job.run_at)
            return DateTrigger(run_date=run_date) if run_date is not None else None
        hour, minute = _extract_time_parts(job.run_at)
        if job.repeat == "daily":
            return CronTrigger(hour=hour, minute=minute)
        if job.repeat == "weekly":
            # 星期一律传**名字**：数字会再次引入"标准 cron / Python"两套读法。
            reference = _parse_datetime(job.run_at) or wall_now()
            return CronTrigger(day_of_week=cron_expr.weekday_name(reference), hour=hour, minute=minute)
        if job.repeat == "monthly":
            reference = _parse_datetime(job.run_at) or wall_now()
            return CronTrigger(day=reference.day, hour=hour, minute=minute)
        return None

    def _update_next_run_fallback_locked(self, job: CronJob, now: datetime) -> None:
        if not job.enabled:
            job.next_run_at = ""
            return
        if job.repeat == "none":
            scheduled = _parse_datetime(job.run_at)
            if scheduled is not None and scheduled >= now and not job.last_run:
                job.next_run_at = scheduled.strftime("%Y-%m-%d %H:%M:%S")
            else:
                job.next_run_at = ""
            return
        if job.repeat == "cron" and job.schedule:
            probe = now.replace(second=0, microsecond=0)
            for _ in range(0, 60 * 24 * 31):
                probe += timedelta(minutes=1)
                if _cron_matches(job.schedule, probe):
                    job.next_run_at = probe.strftime("%Y-%m-%d %H:%M:%S")
                    return
            job.next_run_at = ""
            return
        hour, minute = _extract_time_parts(job.run_at)
        probe = now.replace(second=0, microsecond=0, hour=hour, minute=minute)
        if probe <= now:
            if job.repeat == "interval":
                probe += timedelta(days=max(1, job.run_every_days))
            else:
                probe += timedelta(days=1 if job.repeat == "daily" else 7 if job.repeat == "weekly" else 30)
        job.next_run_at = probe.strftime("%Y-%m-%d %H:%M:%S")

    def _normalize_schedule(self, schedule: str, run_at: str, repeat: str) -> tuple[str, str, str]:
        normalized_repeat = str(repeat or "none").strip().lower() or "none"
        normalized_schedule = str(schedule or "").strip()
        normalized_run_at = str(run_at or "").strip()
        if normalized_repeat == "cron" and normalized_schedule:
            return normalized_schedule, normalized_run_at, normalized_repeat
        if normalized_repeat in {"daily", "weekly", "monthly", "interval"}:
            if not normalized_run_at:
                normalized_run_at = wall_now().strftime("%Y-%m-%d 08:00")
            return normalized_schedule, normalized_run_at, normalized_repeat
        if normalized_repeat == "none" and normalized_run_at:
            return normalized_schedule, normalized_run_at, normalized_repeat
        if normalized_schedule:
            return normalized_schedule, normalized_run_at, "cron"
        if normalized_run_at:
            return normalized_schedule, normalized_run_at, "none"
        return "0 8 * * *", wall_now().strftime("%Y-%m-%d 08:00"), "daily"

    def ensure_default_jobs(self):
        daily_brief_time = self._get_daily_brief_time()
        defaults = [
            {
                "name": "每日早报",
                "action_id": "subagent.spawn",
                "payload": {
                    "workflow": "weekly_tech_digest",
                    "label": "每日早报",
                    "title": "小问每日情报简报",
                    "delivery_target": "email",
                    "trigger": "cron_daily_brief",
                },
                "run_at": daily_brief_time,
                "repeat": "daily",
            },
            {
                "name": "每日复盘",
                "action_id": "journal.daily_draft",
                "payload": {"title": "每日复盘"},
                "schedule": "0 21 * * *",
                "repeat": "cron",
            },
        ]
        with self._lock:
            existing_by_name = {job.name: job for job in self._jobs}
            changed = False
            for item in defaults:
                existing = existing_by_name.get(item["name"])
                if existing is None:
                    self._jobs.append(
                        CronJob(
                            job_id=uuid.uuid4().hex[:12],
                            name=item["name"],
                            action_id=item["action_id"],
                            payload=dict(item.get("payload") or {}),
                            schedule=str(item.get("schedule") or ""),
                            run_at=str(item.get("run_at") or ""),
                            repeat=str(item.get("repeat") or "none"),
                        )
                    )
                    changed = True
                    continue
                if self._reconcile_default_job(existing, item):
                    changed = True
            if changed:
                for job in self._jobs:
                    self._refresh_job_schedule_locked(job)
                self._save_jobs()
                self._status["job_count"] = len(self._jobs)

    def _get_daily_brief_time(self) -> str:
        if self._settings_repository is None:
            return "08:30"
        try:
            value = str(self._settings_repository.get_setting(KEY_ASSISTANT_DAILY_BRIEF_TIME, "08:30") or "08:30").strip()
        except Exception:
            return "08:30"
        return value or "08:30"

    @staticmethod
    def _reconcile_default_job(job: CronJob, expected: dict[str, Any]) -> bool:
        changed = False
        for field_name in ("action_id", "schedule", "run_at", "repeat"):
            expected_value = str(expected.get(field_name) or "")
            if getattr(job, field_name) != expected_value:
                setattr(job, field_name, expected_value)
                changed = True
        expected_payload = dict(expected.get("payload") or {})
        if job.payload != expected_payload:
            job.payload = expected_payload
            changed = True
        return changed
