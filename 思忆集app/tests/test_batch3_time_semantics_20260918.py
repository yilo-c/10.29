"""批3「时间语义」治本改造的机器断言（2026-09-18）。

覆盖 4 项缺陷 + 1 条结构性防线：

* **P2-1** cron weekday 语义错位（方案 B：数字按**标准 cron** 解读后立即名字化，
  两引擎消费同一份规范化结果）；
* **P1-5** cron 补跑用回拨墙钟覆写 ``last_run`` → 时钟恢复后双触发；
* **P2-2** task_queue lease 用墙钟判据 → 回拨方向 lease 永不过期；
* **P2-NEW-1** RRULE 混进 cron 字段 → 死任务永不调度；
* **两引擎一致性契约**（本次发现的真正结构性防线）：同表达式下内部补跑判据与
  APScheduler 的"下次触发"必须逐分钟一致。修复前 ``*/30 7-23 * * *`` /
  ``0 9 * * 1-5`` 内部判据恒 False 而 APScheduler 照常触发——该分裂由本契约锁死。

## 反转说明（诚实性纪律）

``tests/test_env_drill_t2_20260917.py`` 与 ``tests/test_cron_d9_d2_fixes_20260916.py``
中若干断言把缺陷"固化"成期望，本批已**同批反转**为正向契约：

* ``test_weekday_semantic_bug`` 原为 ``xfail(strict=True)``（锁住"0=周一"旧行为）；
* ``test_last_cron_trigger_respects_dow`` 原断言 ``0 3 * * 0`` → 周一 09-14；
* ``test_catch_up_ignores_rrule_schedules`` 原断言 RRULE 可静默入库。

历史事实（反转前勘察结论）保留在上述测试的 docstring 中供追溯。

## 口径

* 所有"现在"注入都打在 ``core.clock`` 的**消费侧**（``services.cron_service`` /
  ``services.task_queue`` 的模块属性），因为它们是 ``from core.clock import ...``
  的绑定名；这样测试自身也不直读墙钟。
* 不使用 ``time.time()`` / ``datetime.now()`` 做断言基准（同样受棘轮约束）。
"""

from __future__ import annotations

import importlib.util
import re
from datetime import datetime, timedelta
from pathlib import Path

import pytest

APP_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = APP_ROOT.parent

from core import cron_expr, integrity
from services import cron_service as cron_service_module
from services import task_queue as task_queue_module
from services.cron_service import CronJob, CronService, _last_cron_trigger


# ─────────────────────────────────────────────────────────────────────────────
# 测试替身
# ─────────────────────────────────────────────────────────────────────────────


class _MemoryStore:
    """cron_jobs 的进程内存储替身（绝不触碰生产 data/）。"""

    def __init__(self, initial: dict | None = None) -> None:
        self._kv: dict = dict(initial or {})

    def load_json(self, key, default=None):
        return self._kv.get(key, default)

    def save_json(self, key, value) -> None:
        self._kv[key] = value


class _SettingsRepo:
    def __init__(self, values: dict | None = None) -> None:
        self._values = dict(values or {})

    def get_setting(self, key, default=None):
        return self._values.get(key, default)


def _freeze_cron_clock(monkeypatch, now: datetime) -> None:
    """把 cron_service 的"现在"冻结到 ``now``。

    ``cron_service`` 只经 ``core.clock.wall_now()`` 读"现在"，其余时间戳都由该
    datetime 派生（``now.timestamp()``），故冻结一处即可；若将来它改为直接读
    ``wall_ts()``，本辅助函数会一并冻结（避免测试悄悄失去注入能力）。
    """
    monkeypatch.setattr(cron_service_module, "wall_now", lambda: now)
    if hasattr(cron_service_module, "wall_ts"):
        monkeypatch.setattr(cron_service_module, "wall_ts", lambda: now.timestamp())


def _make_cron_service(executed: list | None = None, store: _MemoryStore | None = None) -> CronService:
    sink = executed if executed is not None else []
    return CronService(
        data_store=store if store is not None else _MemoryStore(),
        executor=lambda action_id, payload: sink.append(action_id) or {"success": True},
        settings_repository=_SettingsRepo({"cron_engine": "poll"}),
    )


def _apscheduler_next(expr: str, ref: datetime) -> datetime:
    """APScheduler 侧的"下次触发"（消费规范化后的表达式）。"""
    from apscheduler.triggers.cron import CronTrigger

    minute, hour, day, month, dow = cron_expr.split_fields(cron_expr.normalize_cron_schedule(expr))
    trigger = CronTrigger(minute=minute, hour=hour, day=day, month=month, day_of_week=dow)
    nxt = trigger.get_next_fire_time(None, ref.astimezone())
    assert nxt is not None, f"APScheduler 未能给出 {expr!r} 的下次触发（表达式或断言基准有误）"
    return nxt.replace(tzinfo=None)


def _internal_next(expr: str, ref: datetime, limit_minutes: int = 60 * 24 * 400) -> datetime | None:
    """内部判据侧的"下次触发"：从 ref（含）起逐分钟找第一个命中点。

    必须**含 ref 本身**——APScheduler 的 ``get_next_fire_time(None, now)`` 语义是
    ">= now 的第一个触发点"；若 ref 恰好落在触发点上，两边都必须给出 ref。
    """
    probe = ref.replace(second=0, microsecond=0)
    for _ in range(limit_minutes + 1):
        if cron_expr.cron_matches(expr, probe):
            return probe
        probe += timedelta(minutes=1)
    return None


# ═════════════════════════════════════════════════════════════════════════════
# 一、P2-1 星期语义：数字按标准 cron（0=Sun）解读后立即名字化
# ═════════════════════════════════════════════════════════════════════════════


def test_normalize_weekday_field_follows_standard_cron():
    """数字 dow 一律按标准 cron 解读并**立即**变成名字（消除二义载体）。

    历史事实（反转前勘察结论）：旧 ``_WEEKDAY_MAP`` 用 Python 语义
    （0=Mon..6=Sun），数字 0 被当成周一，与标准 cron / 用户心智 / 前端
    weekdays 数组（周日优先）全部相反。
    """
    assert cron_expr.normalize_weekday_field("0") == "sun"
    assert cron_expr.normalize_weekday_field("1") == "mon"
    assert cron_expr.normalize_weekday_field("6") == "sat"
    assert cron_expr.normalize_weekday_field("7") == "sun"
    assert cron_expr.normalize_weekday_field("1-5") == "mon,tue,wed,thu,fri"
    assert cron_expr.normalize_weekday_field("0,6") == "sun,sat"
    assert cron_expr.normalize_weekday_field("*/2") == "sun,tue,thu,sat"
    # 名字写法保持名字（幂等）
    assert cron_expr.normalize_weekday_field("mon") == "mon"
    assert cron_expr.normalize_weekday_field("*") == "*"
    # 整条表达式
    assert cron_expr.normalize_cron_schedule("0 3 * * 0") == "0 3 * * sun"
    assert cron_expr.normalize_cron_schedule("0 8 * * 1") == "0 8 * * mon"


def test_numeric_dow_is_read_as_standard_cron_not_python_weekday():
    """实测锚点：2026-09-24 是周四，`0 3 * * 0` 的上一次触发必须是**周日** 09-20。

    历史事实（反转前）：旧实现给 2026-09-21（周一），与注释"周日 03:00"相反。
    """
    ref = datetime(2026, 9, 24, 12, 0)  # 周四
    assert ref.weekday() == 3
    assert _last_cron_trigger("0 3 * * 0", ref) == datetime(2026, 9, 20, 3, 0)  # 周日
    assert _last_cron_trigger("0 8 * * 1", ref) == datetime(2026, 9, 21, 8, 0)  # 周一
    assert _last_cron_trigger("0 9 * * 6", ref) == datetime(2026, 9, 19, 9, 0)  # 周六
    # 名字与数字在规范化后必须等价（同一份判据）
    assert _last_cron_trigger("0 3 * * sun", ref) == _last_cron_trigger("0 3 * * 0", ref)
    assert _last_cron_trigger("0 9 * * sat", ref) == _last_cron_trigger("0 9 * * 6", ref)


def test_cronjob_normalizes_numeric_dow_on_construction():
    """任何构造路径都经 ``CronJob.revalidate_schedule`` → 数字 dow 不再残留。"""
    job = CronJob(job_id="j", name="n", action_id="a.b", payload={}, schedule="0 3 * * 0", repeat="cron")
    assert job.schedule == "0 3 * * sun"
    assert job.invalid_schedule is False
    assert cron_expr.is_numeric_weekday_literal(job.schedule) is False


def test_legacy_numeric_dow_migrated_on_load_to_intended_days():
    """存量迁移：3 个生产周级任务迁移后**实际执行日**与注释/命名一致。

    §10.1 净效果：auto_backup 周一→周日、error_digest 周二→周一、
    tech_watch.judge 仍是周日（其数字 6 是旧 ``_WEEKDAY_MAP`` 由名字 sun 转来的，
    若按标准 cron 盲读会漂到周六——这是本迁移必须防住的"修 2 个改坏 1 个"）。
    """
    store = _MemoryStore(
        {
            "cron_jobs": [
                {
                    "job_id": "backup",
                    "name": "每周自动备份",
                    "action_id": "system.auto_backup",
                    "payload": {},
                    "schedule": "0 3 * * 0",
                    "repeat": "cron",
                    "enabled": True,
                },
                {
                    "job_id": "digest",
                    "name": "每周错误摘要",
                    "action_id": "system.error_digest",
                    "payload": {},
                    "schedule": "0 8 * * 1",
                    "repeat": "cron",
                    "enabled": True,
                },
                {
                    "job_id": "judge",
                    "name": "技术瞭望评估",
                    "action_id": "tech_watch.judge",
                    "payload": {},
                    "schedule": "0 9 * * 6",
                    "repeat": "cron",
                    "enabled": True,
                },
            ]
        }
    )
    service = CronService(data_store=store)
    schedules = {job["action_id"]: job["schedule"] for job in service.list_jobs(include_disabled=True)}
    assert schedules["system.auto_backup"] == "0 3 * * sun"
    assert schedules["system.error_digest"] == "0 8 * * mon"
    assert schedules["tech_watch.judge"] == "0 9 * * sun"

    ref = datetime(2026, 9, 24, 12, 0)  # 周四
    assert _last_cron_trigger(schedules["system.auto_backup"], ref) == datetime(2026, 9, 20, 3, 0)
    assert _last_cron_trigger(schedules["system.error_digest"], ref) == datetime(2026, 9, 21, 8, 0)
    assert _last_cron_trigger(schedules["tech_watch.judge"], ref) == datetime(2026, 9, 20, 9, 0)
    # 迁移必须落盘（否则每次启动都重算，且外部读到的仍是歧义字面量）
    persisted = store.load_json("cron_jobs", [])
    assert {item["schedule"] for item in persisted} == {"0 3 * * sun", "0 8 * * mon", "0 9 * * sun"}


def test_user_created_numeric_dow_migrates_by_standard_cron():
    """用户自建任务（非种子 action）按**标准 cron** 解读——前端 weekdays 周日优先。"""
    store = _MemoryStore(
        {
            "cron_jobs": [
                {
                    "job_id": "u1",
                    "name": "用户周任务",
                    "action_id": "tool.custom",
                    "payload": {},
                    "schedule": "0 10 * * 0",
                    "repeat": "cron",
                    "enabled": True,
                }
            ]
        }
    )
    service = CronService(data_store=store)
    assert service.list_jobs(include_disabled=True)[0]["schedule"] == "0 10 * * sun"


def test_seed_tech_watch_judge_emits_weekday_name_not_number():
    """播种侧（_context_cron）不得再把星期名字经数字中转。"""
    from app_runtime._context_cron import CronContextMixin

    service = _make_cron_service()
    ctx = CronContextMixin.__new__(CronContextMixin)
    ctx.cron_service = service
    ctx.tech_watch_collector = object()
    ctx.tech_watch_judge = object()
    ctx.settings_repository = _SettingsRepo({"tech_watch_judge_day": "sun"})
    ctx._ensure_tech_watch_jobs()
    schedules = {job["action_id"]: job["schedule"] for job in service.list_jobs(include_disabled=True)}
    assert schedules["tech_watch.judge"] == "0 9 * * sun"
    # 非默认设置同样以名字落库
    service2 = _make_cron_service()
    ctx2 = CronContextMixin.__new__(CronContextMixin)
    ctx2.cron_service = service2
    ctx2.tech_watch_collector = object()
    ctx2.tech_watch_judge = object()
    ctx2.settings_repository = _SettingsRepo({"tech_watch_judge_day": "fri"})
    ctx2._ensure_tech_watch_jobs()
    schedules2 = {job["action_id"]: job["schedule"] for job in service2.list_jobs(include_disabled=True)}
    assert schedules2["tech_watch.judge"] == "0 9 * * fri"


def _referenced_identifiers(path: Path) -> set[str]:
    """AST 级取出源码里真正被引用的标识符（注释/docstring 里的提及不计）。"""
    import ast

    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            found.add(node.id)
        elif isinstance(node, ast.Attribute):
            found.add(node.attr)
        elif isinstance(node, ast.alias):
            found.add(node.name.rsplit(".", 1)[-1])
    return found


def test_python_weekday_map_is_gone_from_cron_service():
    """棘轮：旧 ``_WEEKDAY_MAP``（0=Mon）是歧义源头，不得复活。

    AST 级判定：只禁**引用/定义**；注释与 docstring 里为了讲清历史而提及该
    名字不算违规（文本级扫描会把说明文字误判成违规）。
    """
    for rel in ("services/cron_service.py", "app_runtime/_context_cron.py", "assistant/_router_schedule.py"):
        referenced = _referenced_identifiers(APP_ROOT / rel)
        assert "_WEEKDAY_MAP" not in referenced, f"{rel} 重新引入了旧星期映射表"


_SCHEDULE_LITERAL_RE = re.compile(r"""schedule\s*=\s*f?["']([^"']*)["']""")

# 扫描排除目录：测试自身需要数字 dow 作反例；venv/缓存是第三方或产物。
_SCAN_EXCLUDED_PARTS = {"tests", ".venv", "__pycache__", ".pytest-tmp", ".pytest-tmp-root"}


def _iter_source_schedule_literals():
    for path in APP_ROOT.rglob("*.py"):
        rel_parts = path.relative_to(APP_ROOT).parts
        if any(part in _SCAN_EXCLUDED_PARTS for part in rel_parts):
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        for literal in _SCHEDULE_LITERAL_RE.findall(text):
            yield path.relative_to(APP_ROOT), literal


def test_no_numeric_weekday_literal_in_cron_seed_sites():
    """棘轮：生产源码里禁止再出现**数字 dow** 的 cron 字面量。

    数字 dow 是"标准 cron / Python"两套读法的载体；规范化的输出永远是名字
    （或 ``*``）。种子任务必须写 ``sun``/``mon`` 等名字。
    """
    offenders = [f"{rel}: {literal!r}" for rel, literal in _iter_source_schedule_literals() if cron_expr.is_numeric_weekday_literal(literal)]
    assert not offenders, f"发现数字 dow 的 cron 字面量（应改为星期名字）：{offenders}"


# ═════════════════════════════════════════════════════════════════════════════
# 二、两引擎一致性契约（本次发现的真正结构性防线）
# ═════════════════════════════════════════════════════════════════════════════

# 覆盖：纯 ``*``、步长、区间、列表、月名、日+周 AND、跨周区间、单点
_CONSISTENCY_EXPRESSIONS = (
    "0 3 * * sun",
    "0 8 * * mon",
    "0 9 * * sat",
    "0 9 * * *",
    "30 7 * * *",
    "*/30 7-23 * * *",
    "0 9 * * mon,tue,wed,thu,fri",
    "0 12 * * sat,sun",
    "0 9 1 * *",
    "0 0 1 jan *",
    "15 6 * feb mon",
    "0 3 * * 0",
    "0 8 * * 1",
    "0 9 * * 1-5",
)


@pytest.mark.parametrize("expr", _CONSISTENCY_EXPRESSIONS)
def test_two_engines_agree_on_next_fire_time(expr):
    """契约：内部补跑判据与 APScheduler 的"下次触发"逐分钟一致。

    历史事实（反转前勘察结论）：修复前内部 ``_cron_matches`` 只认单值 + ``*``，
    ``*/30 7-23 * * *`` 与 ``0 9 * * 1-5`` 恒返回 False，而 APScheduler 照常触发
    → poll 引擎下 ``proactive.tick`` 永不触发、永不补跑。这是本仓 cron 最危险的
    隐性分裂，故做成参数化契约而非单点断言。
    """
    ref = datetime(2026, 9, 18, 12, 0)  # 周五
    expected = _apscheduler_next(expr, ref)
    actual = _internal_next(expr, ref)
    assert actual == expected, f"{expr!r}: 内部判据 {actual} != APScheduler {expected}"
    # 触发点本身必须被内部判据认可（反之亦然）
    assert cron_expr.cron_matches(expr, expected) is True


@pytest.mark.parametrize("expr", _CONSISTENCY_EXPRESSIONS)
def test_apscheduler_consumes_normalized_weekday(expr):
    """契约：交给 APScheduler 的 dow 必须是规范化后的名字/``*``，不是数字。"""
    normalized = cron_expr.normalize_cron_schedule(expr)
    dow = cron_expr.split_fields(normalized)[4]
    assert cron_expr.is_numeric_weekday_literal(dow) is False
    assert dow == "*" or all(token.strip() in cron_expr.WEEKDAY_NAMES for token in dow.split(","))


def test_build_trigger_passes_normalized_weekday_to_apscheduler():
    """端到端：CronJob 的 dow 经 _build_trigger 到达 APScheduler 时已是名字。"""
    service = _make_cron_service()
    legacy = CronJob(job_id="j1", name="n", action_id="a.b", payload={}, schedule="0 9 * * 0", repeat="cron")
    trigger = service._build_trigger(legacy)
    assert trigger is not None
    assert "sun" in str(trigger)
    next_fire = trigger.get_next_fire_time(None, datetime(2026, 9, 18, 12, 0).astimezone())
    assert next_fire.replace(tzinfo=None) == datetime(2026, 9, 20, 9, 0)  # 周日

    named = CronJob(job_id="j2", name="n", action_id="a.b", payload={}, schedule="0 9 * * sun", repeat="cron")
    assert str(service._build_trigger(named)) == str(trigger)


def test_build_trigger_refuses_invalid_schedule():
    """非法 schedule 不交给 APScheduler（否则可能抛异常或静默死任务）。"""
    service = _make_cron_service()
    job = CronJob(job_id="j", name="n", action_id="a.b", payload={}, schedule="", repeat="cron")
    job.schedule = "FREQ=MONTHLY;BYDAY=MO"  # 绕过 __post_init__ 模拟存量坏数据
    assert service._build_trigger(job) is None


# ═════════════════════════════════════════════════════════════════════════════
# 三、P2-NEW-1 schedule 形状校验：新增拒收 + 存量标记不崩
# ═════════════════════════════════════════════════════════════════════════════


@pytest.mark.parametrize(
    "bad",
    ("FREQ=MONTHLY;BYDAY=MO", "RRULE:FREQ=DAILY", "0 9 * *", "0 9 * * bogus", "0 9 * * * *"),
)
def test_add_job_rejects_invalid_schedule_shape(bad):
    """非法形状在入口拒收（ValueError）+ 台账告警，绝不落库成死任务。"""
    integrity.reset()
    service = _make_cron_service()
    with pytest.raises(ValueError):
        service.add_job("坏任务", "tool.zcode.open", {}, schedule=bad, repeat="cron")
    assert service.list_jobs(include_disabled=True) == []
    assert integrity.snapshot()["counts"].get(cron_service_module.KIND_CRON_SCHEDULE, 0) >= 1


def test_empty_schedule_for_cron_repeat_falls_back_to_schedulable_job():
    """``repeat=cron`` 但 schedule 为空 → 回落到可调度的 daily（**不是**死任务）。

    这是 ``_normalize_schedule`` 的既有兜底语义（RPC 可能收到残缺输入），本批
    刻意不改（改动爆炸半径大于收益）。此断言把它**固化**下来：兜底产物必须是
    合法 5 字段 cron，从而仍然满足"绝不产生永不调度的死任务"这一不变量。
    """
    service = _make_cron_service()
    job = service.add_job("空表达式", "tool.zcode.open", {}, schedule="   ", repeat="cron")
    assert job["invalid_schedule"] is False
    assert cron_expr.validate_schedule_shape(job["schedule"]).ok is True
    assert job["repeat"] in ("cron", "daily")


def test_legacy_invalid_schedule_marked_and_cron_list_survives():
    """存量非法任务：标记 ``invalid_schedule`` + 台账，**不 raise**、不搞崩列表。

    历史事实（反转前勘察结论）：修复前 ``FREQ=MONTHLY;BYDAY=MO`` 会被静默收下，
    生产实测该任务 ``next_run_at=""`` —— 一条永不调度的死任务，且无任何告警。
    """
    integrity.reset()
    store = _MemoryStore(
        {
            "cron_jobs": [
                {
                    "job_id": "dead",
                    "name": "zcode 打开（坏）",
                    "action_id": "tool.zcode.open",
                    "payload": {},
                    "schedule": "FREQ=MONTHLY;BYDAY=MO",
                    "repeat": "cron",
                    "enabled": True,
                },
                {
                    "job_id": "ok",
                    "name": "正常任务",
                    "action_id": "tool.zcode.open",
                    "payload": {},
                    "schedule": "0 9 * * *",
                    "repeat": "cron",
                    "enabled": True,
                },
            ]
        }
    )
    service = CronService(data_store=store)
    jobs = {job["job_id"]: job for job in service.list_jobs(include_disabled=True)}
    assert len(jobs) == 2, "一条坏数据不得让 cron.list 整体失败"
    assert jobs["dead"]["invalid_schedule"] is True
    assert jobs["dead"]["invalid_schedule_reason"] == "rrule_misplaced"
    assert jobs["ok"]["invalid_schedule"] is False
    assert integrity.snapshot()["counts"].get(cron_service_module.KIND_CRON_SCHEDULE, 0) >= 1


def test_update_job_allows_repairing_legacy_invalid_schedule():
    """只改名字不能被坏 schedule 卡住（否则用户无法把死任务救回来）。"""
    store = _MemoryStore(
        {
            "cron_jobs": [
                {
                    "job_id": "dead",
                    "name": "坏任务",
                    "action_id": "tool.zcode.open",
                    "payload": {},
                    "schedule": "FREQ=MONTHLY;BYDAY=MO",
                    "repeat": "cron",
                    "enabled": True,
                }
            ]
        }
    )
    service = CronService(data_store=store)
    assert service.update_job("dead", name="改个名") is not None
    with pytest.raises(ValueError):
        service.update_job("dead", schedule="FREQ=DAILY")
    repaired = service.update_job("dead", schedule="0 9 * * mon")
    assert repaired["schedule"] == "0 9 * * mon"
    assert repaired["invalid_schedule"] is False
    assert repaired["invalid_schedule_reason"] == ""


# ═════════════════════════════════════════════════════════════════════════════
# 四、P1-5 last_run 防毒写：回拨补跑不再造成恢复后双触发
# ═════════════════════════════════════════════════════════════════════════════


def test_last_run_write_refuses_to_regress_on_wall_rollback(monkeypatch):
    """单元：墙钟回拨时 ``_safe_last_run_write`` 保留基准，绝不把 last_run 拖回过去。"""
    service = _make_cron_service()
    job = CronJob(
        job_id="j",
        name="n",
        action_id="a.b",
        payload={},
        schedule="0 7 * * *",
        repeat="cron",
        last_run="2026-09-10 07:00:00",
    )
    _freeze_cron_clock(monkeypatch, datetime(2026, 9, 17, 7, 5, 0))
    service._observe_wall(datetime(2026, 9, 17, 7, 5, 0).timestamp())
    # 回拨 2 天后写 last_run → 必须保留高水位（09-17 07:05），不得写成 09-15
    service._safe_last_run_write(job, datetime(2026, 9, 15, 7, 30, 0))
    assert job.last_run == "2026-09-17 07:05:00"


def test_rollback_catch_up_does_not_double_fire_after_clock_restored(monkeypatch):
    """端到端：回拨期间补跑一次，时钟恢复后**不得**再次补跑。

    历史事实（反转前实测输出）：
        初始 last_run = 2026-09-10 07:00:00
        [回拨帧 09-15 07:30] executed = ['system.memory_health']  last_run -> 2026-09-15 07:30:00
        [恢复帧 09-17 07:31] executed = ['system.memory_health']  last_run -> 2026-09-17 07:31:00
        → 恢复帧再次触发 = 双触发（P1-5）
    """
    executed: list = []
    service = _make_cron_service(executed)
    service.add_job("巡检", "system.memory_health", {}, schedule="0 4 * * *", repeat="cron")
    job = service._jobs[0]

    # ① 先让服务在**正确**墙钟下观测一次（禁用 → 不触发，只记录高水位）
    job.enabled = False
    _freeze_cron_clock(monkeypatch, datetime(2026, 9, 17, 4, 5, 0))
    service._catch_up_missed()
    assert executed == []

    # ② 墙钟被回拨 2 天后启用并补跑：last_run 不得被写成回拨帧时间
    job.enabled = True
    _freeze_cron_clock(monkeypatch, datetime(2026, 9, 15, 4, 30, 0))
    service._catch_up_missed()
    assert executed == ["system.memory_health"], "回拨帧应补跑一次"
    assert job.last_run >= "2026-09-17", f"last_run 被回拨污染: {job.last_run}"

    # ③ 墙钟恢复：不得再次补跑
    _freeze_cron_clock(monkeypatch, datetime(2026, 9, 17, 4, 31, 0))
    executed.clear()
    service._catch_up_missed()
    assert executed == [], "时钟恢复后同一触发点被二次判'错过' = 双触发（P1-5）"


# ═════════════════════════════════════════════════════════════════════════════
# 五、P2-2 task_queue lease：单调钟为主 + 墙钟倒退护栏
# ═════════════════════════════════════════════════════════════════════════════


def _make_queue(tmp_path):
    return task_queue_module.PersistentTaskQueue(db_path=str(tmp_path / "tq.db"))


def test_requeue_expired_still_reclaims_on_wall_forward_jump(tmp_path, monkeypatch):
    """回归：前进方向（原有行为）必须保持不变——lease 到期即回收。"""
    queue = _make_queue(tmp_path)
    tid = queue.enqueue(capability_id="cap.fwd", payload={})
    task = queue.dequeue(lease_seconds=60)
    assert task is not None and task.task_id == tid
    real = task_queue_module.wall_ts()
    monkeypatch.setattr(task_queue_module, "wall_ts", lambda: real + 3600)
    assert queue.requeue_expired() == 1
    assert queue.get(tid).status == "pending"


def test_requeue_expired_reclaims_after_wall_rollback(tmp_path, monkeypatch):
    """缺陷复现：墙钟回拨 2 天时，崩溃前残留的 running 任务必须仍被回收。

    历史事实（反转前勘察结论）：判据只有 ``leased_until < now``；回拨后该式为
    恒假 → 崩溃前 ``running`` 的任务永不回 ``pending``（cron 的 dispatched
    历史条目永不被 AC-2 翻转）。修法：``updated_at``（= 租约起始墙钟）作倒退
    基准，回拨超过容忍窗即保守回收。
    """
    queue = _make_queue(tmp_path)
    tid = queue.enqueue(capability_id="cap.rollback", payload={})
    task = queue.dequeue(lease_seconds=3600)
    assert task is not None and task.task_id == tid
    real = task_queue_module.wall_ts()
    monkeypatch.setattr(task_queue_module, "wall_ts", lambda: real - 2 * 86400)
    # 清掉进程内单调租约 → 强制走"墙钟 + 倒退护栏"这条跨进程/重启路径
    with queue._lease_lock:
        queue._lease_deadlines.clear()
    assert queue.requeue_expired() == 1
    row = queue.get(tid)
    assert row.status == "pending"
    assert "lease_expired" in (row.last_error or "")


def test_requeue_expired_uses_monotonic_clock_when_wall_is_frozen(tmp_path, monkeypatch):
    """单调钟为主判据：墙钟完全冻结（未到期、未倒退）时，进程内租约仍按时回收。"""
    queue = _make_queue(tmp_path)
    tid = queue.enqueue(capability_id="cap.mono", payload={})
    task = queue.dequeue(lease_seconds=30)
    assert task is not None and task.task_id == tid
    deadline = queue._lease_deadlines[tid]
    frozen = task_queue_module.wall_ts()
    monkeypatch.setattr(task_queue_module, "wall_ts", lambda: frozen)
    monkeypatch.setattr(task_queue_module, "mono_ts", lambda: deadline + 1.0)
    assert queue.requeue_expired() == 1
    row = queue.get(tid)
    assert row.status == "pending"
    assert "lease_expired_mono" in (row.last_error or "")


def test_monotonic_lease_tracking_is_cleared_on_state_transitions(tmp_path):
    """租约表必须随任务离开 running 而清空（防字典无界增长）。"""
    queue = _make_queue(tmp_path)
    tid = queue.enqueue(capability_id="cap.cleanup", payload={})
    queue.dequeue(lease_seconds=30)
    assert tid in queue._lease_deadlines
    assert queue.complete(tid) is True
    assert tid not in queue._lease_deadlines

    tid2 = queue.enqueue(capability_id="cap.cancel", payload={})
    queue.dequeue(lease_seconds=30)
    assert queue.cancel(tid2) is True
    assert tid2 not in queue._lease_deadlines


def test_active_dedup_unique_index_survives(tmp_path):
    """回归：``idx_tq_active_dedup``（前进方向防双跑的既有防线）不得被破坏。"""
    queue = _make_queue(tmp_path)
    index_rows = queue._connect().execute("SELECT name FROM sqlite_master WHERE type='index' AND name='idx_tq_active_dedup'").fetchall()
    assert len(index_rows) == 1
    first = queue.enqueue(capability_id="cap.dedup", payload={"a": 1})
    queue.dequeue(lease_seconds=60)
    # 同 (queue, capability, payload) 在 pending/running 期间必须去重（回同一个 id），
    # 这是"前进方向 lease 立即过期 → reaper 回炉"时防双跑的半保护。
    again = queue.enqueue(capability_id="cap.dedup", payload={"a": 1})
    assert again == first


# ═════════════════════════════════════════════════════════════════════════════
# 六、时钟纪律棘轮：批3 相关模块直读归零
# ═════════════════════════════════════════════════════════════════════════════


def _load_clock_discipline_module():
    spec = importlib.util.spec_from_file_location("_clock_discipline_probe", REPO_ROOT / "scripts" / "check_clock_discipline.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_batch3_modules_have_zero_direct_clock_reads():
    """棘轮：TARGETS 内所有时间判据模块的直读计数必须为 0。"""
    module = _load_clock_discipline_module()
    counts = module._collect()
    offenders = {rel: n for rel, n in counts.items() if n != 0}
    assert not offenders, f"时间敏感模块仍有直读墙钟/单调钟：{offenders}"


def test_clock_discipline_baseline_is_tightened_to_zero():
    """棘轮基线必须已收紧到真实值（0），否则棘轮形同虚设。"""
    module = _load_clock_discipline_module()
    baseline = module._load_baseline()
    stale = {rel: n for rel, n in baseline.items() if n != 0}
    assert not stale, f"基线未收紧（存量应已归零）：{stale}"
