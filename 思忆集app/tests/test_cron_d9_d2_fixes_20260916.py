# -*- coding: utf-8 -*-
"""D-9 / D-2 / D-10 修复的结构断言（2026-09-16 知识管线审计批）。

锁死三件事，防止回归：
1. D-9  _catch_up_missed 必须覆盖 repeat="cron" 任务（生产实测 11 个 cron
    任务自 08-19 起饿死：晚间开机错过晨间触发点，无补跑、无死信、零告警）。
2. D-2  死信 last_error 永不为空白（生产实测 12 条空错误死信：
    subagent.spawn / journal.daily_draft 未注册 → unknown_action 只带 reason）。
3. D-10 播种的 cron action_id 必须存在于 cron_actions 注册表——
    「ensure_* 有、注册表无」的不对称结构性消除（tech_watch.collect 同类发现）。
"""

from __future__ import annotations

import asyncio
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest


APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from services.cron_service import CronJob, CronService, _last_cron_trigger
from data.local_storage import LocalDataStore


class _SettingsRepo:
    def __init__(self, values=None):
        self._values = dict(values or {})

    def get_setting(self, key, default=None):
        return self._values.get(key, default)


def _make_service(tmp_path, executed):
    return CronService(
        data_store=LocalDataStore(base_dir=str(tmp_path)),
        executor=lambda action_id, payload: executed.append((action_id, payload)) or {"ok": True},
        settings_repository=_SettingsRepo({"cron_engine": "poll"}),
    )


# ── D-9：_last_cron_trigger ──────────────────────────────────────────


def test_last_cron_trigger_returns_past_minute_granularity():
    now = datetime(2026, 9, 16, 18, 45)
    trig = _last_cron_trigger("30 7 * * *", now)
    assert trig == datetime(2026, 9, 16, 7, 30)


def test_last_cron_trigger_rolls_back_to_yesterday_before_fire():
    now = datetime(2026, 9, 16, 5, 0)
    trig = _last_cron_trigger("30 7 * * *", now)
    assert trig == datetime(2026, 9, 15, 7, 30)


def test_last_cron_trigger_respects_dow():
    """[反转说明 2026-09-18] 原断言 ``0 3 * * 0`` → 周一 09-14，并注明"属新发现
    D-11（待决策，勿改）"——那是把缺陷**固化**成期望。

    历史事实（反转前勘察结论）：``_cron_matches`` 以 ``dt.weekday()``（0=周一）
    比较，与标准 cron（0=周日）及调用侧注释"周日 03:00 自动备份"不一致。

    批3 · P2-1 修复后：数字 dow 按**标准 cron** 解读（0=周日）。
    2026-09-16 是周三 → 最近一个周日 = 2026-09-13。
    全量覆盖见 ``tests/test_batch3_time_semantics_20260918.py``。
    """
    now = datetime(2026, 9, 16, 18, 0)
    trig = _last_cron_trigger("0 3 * * 0", now)
    assert trig == datetime(2026, 9, 13, 3, 0)
    assert _last_cron_trigger("0 3 * * sun", now) == trig


def test_last_cron_trigger_rejects_rrule_and_garbage():
    now = datetime(2026, 9, 16, 18, 0)
    assert _last_cron_trigger("FREQ=MONTHLY;BYDAY=MO", now) is None
    assert _last_cron_trigger("", now) is None
    assert _last_cron_trigger("0 9 * *", now) is None  # 4 字段


# ── D-9：_catch_up_missed 覆盖 cron 族 ──────────────────────────────


def test_catch_up_reruns_stale_cron_job(tmp_path):
    """last_run 早于最近一次触发点 → 必须补跑（旧实现直接跳过 repeat=cron）。"""
    executed = []
    service = _make_service(tmp_path, executed)
    service.add_job("巡检", "system.memory_health", {}, schedule="0 4 * * *", repeat="cron")
    # last_run 默认空 → 视为错过（今天 04:00 是否已过均成立：空 last_run 必补）
    service._catch_up_missed()
    assert executed == [("system.memory_health", {})]
    jobs = service.list_jobs(include_disabled=True)
    assert jobs[0]["last_run"], "补跑应写 last_run 防 monitor 周期内重复触发"


def test_catch_up_skips_cron_job_fresh_after_last_trigger(tmp_path):
    executed = []
    service = _make_service(tmp_path, executed)
    service.add_job("巡检", "system.memory_health", {}, schedule="0 4 * * *", repeat="cron")
    # 模拟"最近一次触发点之后已经跑过"
    trig = _last_cron_trigger("0 4 * * *", datetime.now())
    assert trig is not None
    service._jobs[0].last_run = (trig + timedelta(minutes=1)).strftime("%Y-%m-%d %H:%M:%S")
    service._catch_up_missed()
    assert executed == []


def test_catch_up_ignores_rrule_schedules(tmp_path):
    """[反转说明 2026-09-18] 原用例断言 RRULE 可被 ``add_job`` **静默收下**、
    只是补跑时跳过——那等于承认"死任务"可以入库。

    历史事实（反转前勘察结论）：生产 ``cron.list`` 实测 ``tool.zcode.open``
    第 2 实例 ``schedule="FREQ=MONTHLY;BYDAY=MO"``、``next_run_at=""`` ——
    一条永不调度且零告警的死任务（NEW-1）。

    批3 修复后：``repeat=cron`` 的非法 schedule 在**入口拒收**（ValueError +
    完整性台账）；存量非法任务读取时标记 ``invalid_schedule`` 且**不被补跑**。
    两条路径分别断言如下。
    """
    from services.cron_service import CronService

    executed: list = []
    service = _make_service(tmp_path, executed)

    # ① 新增路径：非法 schedule 拒收（不再静默入库）
    with pytest.raises(ValueError):
        service.add_job(
            "boundary-rrule",
            "tool.zcode.open",
            {},
            schedule="FREQ=MONTHLY;BYDAY=MO",
            repeat="cron",
        )
    assert service.list_jobs(include_disabled=True) == []

    # ② 存量路径：已落库的非法任务被标记，且补跑判据仍跳过它（防重复执行）
    legacy = CronService(data_store=LocalDataStore(base_dir=str(tmp_path / "legacy")))
    legacy._jobs.append(
        CronJob(
            job_id="legacy-rrule",
            name="legacy-rrule",
            action_id="tool.zcode.open",
            payload={},
            schedule="FREQ=MONTHLY;BYDAY=MO",
            repeat="cron",
        )
    )
    assert legacy.list_jobs(include_disabled=True)[0]["invalid_schedule"] is True
    legacy._catch_up_missed()
    assert executed == []


# ── D-10：播种 ↔ 注册表 对称性（结构测试，先红后绿）────────────────


def _seeded_action_ids() -> set[str]:
    """静态扫描两处播种源里出现的所有 action_id 字面量。"""
    cron_service_src = (APP_ROOT / "services" / "cron_service.py").read_text(encoding="utf-8")
    ctx_cron_src = (APP_ROOT / "app_runtime" / "_context_cron.py").read_text(encoding="utf-8")
    ids = set(re.findall(r'"action_id":\s*"([a-z0-9_.]+)"', cron_service_src))
    ids |= set(re.findall(r'action_id="([a-z0-9_.]+)"', ctx_cron_src))
    return ids


def test_all_seeded_cron_actions_are_registered():
    """ensure_default_jobs / _ensure_* 播种的 action 必须在 cron_actions 注册表。

    历史教训：subagent.spawn、journal.daily_draft、tech_watch.collect 被播种
    但 Wave-1 迁移未注册 → 每次触发走 unknown_action → dead_letter（D-10）。
    本测试把该不对称永久锁死。
    """
    from services.cron_actions import list_cron_actions

    registered = {item["action_id"] for item in list_cron_actions()}
    # 豁免：AppContext 层直处理的 action（_context_cron.py 在查注册表前短路，
    # 属设计内路径，非孤儿）
    app_context_native = {"system.self_maintenance"}
    seeded = _seeded_action_ids()
    missing = seeded - registered - app_context_native
    assert not missing, f"播种了但未注册进 cron_actions 注册表: {sorted(missing)}"


# ── D-2：死信 last_error 永不为空白 ─────────────────────────────────


def test_worker_dead_letter_error_never_blank(tmp_path):
    """executor 返回 success=False 且 error 为空串 → 死信必须落非空 last_error。"""
    from services.task_queue import PersistentTaskQueue

    db = str(tmp_path / "tq.db")
    queue = PersistentTaskQueue(db_path=db)
    tid = queue.enqueue(capability_id="cap.blank_fail", payload={}, max_retries=0)
    task = queue.dequeue(queue_name="default")
    assert task is not None and task.task_id == tid

    async def _drive():
        from services.task_queue import TaskQueueWorker

        worker = TaskQueueWorker(queue, executor=lambda t: {"success": False, "error": "  "}, queue_name="default")
        await worker._run_task(task)

    asyncio.run(_drive())
    import sqlite3

    row = sqlite3.connect(db).execute("SELECT status, last_error FROM task_queue WHERE task_id=?", (tid,)).fetchone()
    assert row[0] == "dead_letter"
    assert (row[1] or "").strip(), f"死信 last_error 不允许空白（D-2）: {row!r}"


def test_unknown_cron_action_synthesizes_error_text():
    """_execute_cron_action 未注册 action 时必须返回带非空 error 的失败结果。

    通知与 task_queue 死信只读 error 键——reason-only 会产生空白 last_error
    （D-2 生产实测 12 条）。unknown_action 分支不触碰 self 任何属性，
    因此可用 __new__ 裸实例直调。
    """
    from app_runtime._context_cron import CronContextMixin

    ctx = CronContextMixin.__new__(CronContextMixin)
    result = ctx._execute_cron_action("no.such.action", {})
    assert result["success"] is False
    assert (result.get("error") or "").strip(), f"unknown_action 必须带非空 error: {result!r}"
    assert result.get("action_id") == "no.such.action"
