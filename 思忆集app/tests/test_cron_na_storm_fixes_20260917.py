# -*- coding: utf-8 -*-
"""N-A（2026-09-17）cron 风暴三件套修复的契约测试。

锁死三处修复，防止回归：
1. run_now 假成功 —— 旧实现调 _run_job（job 不存在/已禁用/入队失败均静默），
   handler 恒报 success:true。现 run_job_now 如实回传 not_triggered
   （not_found / disabled / dispatch_failed），handler 映射为 success:false；
   直跑路径带回真实执行成败；手动执行以 PRIORITY_MANUAL_RUN(+1) 入队插队（J3）。
2. 冷启动补跑错峰 —— _catch_up_missed 逐条 postpone(idx*stagger)，重动作
   （tech_watch.collect / subagent.spawn）排尾部且优先级垫底
   （PRIORITY_CATCHUP_HEAVY），摊平入队风暴（默认步长 30s，可调 0 关闭）。
3. tech_watch.collect 离线短路 —— SIYI_TEST_OFFLINE=1 时零网络（不搜索/
   不抓取/不摘要），返回 skipped（分钟级网络任务不再霸占 cron 单 worker 队头）。
外加 promote 竞态：补跑刚 postpone 的 pending 任务，用户点「立即执行」
（priority>0，enqueue 幂等复用旧 task_id）必须经 promote 拉回即时可调度。

全部 tmp_path / 内存 fake 隔离，绝不触碰真实 data/。
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

import pytest

from backend.handlers._cron import register_cron
from data.local_storage import LocalDataStore
from services.cron_service import (
    PRIORITY_CATCHUP,
    PRIORITY_CATCHUP_HEAVY,
    PRIORITY_MANUAL_RUN,
    CronService,
    _resolve_catchup_stagger,
)


class _SettingsRepo:
    def __init__(self, values=None):
        self._values = dict(values or {})

    def get_setting(self, key, default=None):
        return self._values.get(key, default)


def _make_service(tmp_path, *, executor=None, task_queue=None):
    return CronService(
        data_store=LocalDataStore(base_dir=str(tmp_path)),
        executor=executor or (lambda action_id, payload: {"ok": True}),
        settings_repository=_SettingsRepo({"cron_engine": "poll"}),
        task_queue=task_queue,
    )


def _register(service):
    """把 service 接到 cron.* RPC handler 上，返回 registry。"""
    from types import SimpleNamespace

    from backend.method_registry import MethodRegistry

    reg = MethodRegistry()
    register_cron(reg, SimpleNamespace(cron_service=service))
    return reg


class _RecordingQueue:
    """内存假队列：记录 enqueue/postpone/promote 调用，断言错峰与插队参数。"""

    def __init__(self, *, enqueue_raises=None):
        self.enqueued: list[tuple[str, dict]] = []
        self.postponed: list[tuple[str, float]] = []
        self.promoted: list[tuple[str, int]] = []
        self._enqueue_raises = enqueue_raises
        self._next_id = 0

    def enqueue(self, **kwargs):
        if self._enqueue_raises is not None:
            raise self._enqueue_raises
        self._next_id += 1
        tid = f"fake-task-{self._next_id}"
        self.enqueued.append((tid, kwargs))
        return tid

    def postpone(self, task_id, delay_seconds):
        self.postponed.append((task_id, float(delay_seconds)))
        return True

    def promote(self, task_id, priority=1):
        self.promoted.append((str(task_id), int(priority)))
        return True


# ── 1. run_job_now / cron.run_now 如实语义（杜绝假成功） ──────────────


def test_run_job_now_not_found_is_not_triggered(tmp_path):
    service = _make_service(tmp_path)
    out = service.run_job_now("ghost-id")
    assert out["triggered"] is False
    assert out["reason"] == "not_found"
    assert out.get("error")


def test_run_job_now_disabled_is_not_triggered(tmp_path):
    """禁用任务不可手动绕过——必须先启用（旧实现静默成功是生产事故根因）。"""
    service = _make_service(tmp_path)
    job = service.add_job("巡检", "system.memory_health", {}, schedule="0 4 * * *", repeat="cron")
    service.toggle_job(job["job_id"], False)
    out = service.run_job_now(job["job_id"])
    assert out["triggered"] is False
    assert out["reason"] == "disabled"
    assert out.get("error")


@pytest.mark.asyncio
async def test_rpc_run_now_not_found_reports_success_false(tmp_path):
    """回归锁：生产实测 {"job_id":"nonexistent"} 曾返回 {"success": true}。"""
    service = _make_service(tmp_path)
    handler = _register(service)._handlers["cron.run_now"]

    r = await handler(job_id="nonexistent")
    assert r["success"] is False
    assert r["reason"] == "not_found"
    assert r.get("error")


@pytest.mark.asyncio
async def test_rpc_run_now_disabled_reports_success_false(tmp_path):
    service = _make_service(tmp_path)
    job = service.add_job("早报", "subagent.spawn", {}, schedule="0 8 * * *", repeat="cron")
    service.toggle_job(job["job_id"], False)
    handler = _register(service)._handlers["cron.run_now"]

    r = await handler(job_id=job["job_id"])
    assert r["success"] is False
    assert r["reason"] == "disabled"


def test_run_job_now_dispatch_failed_does_not_claim_dispatched(tmp_path):
    """入队失败 ≠ 触发成功——如实 reason=dispatch_failed，不谎报 dispatched。"""
    queue = _RecordingQueue(enqueue_raises=RuntimeError("db is locked"))
    service = _make_service(tmp_path, task_queue=queue)
    job = service.add_job("早报", "subagent.spawn", {}, schedule="0 8 * * *", repeat="cron")

    out = service.run_job_now(job["job_id"])
    assert out["triggered"] is False
    assert out["reason"] == "dispatch_failed"
    assert "db is locked" in out["error"]


@pytest.mark.asyncio
async def test_rpc_run_now_dispatch_failed_reports_success_false(tmp_path):
    queue = _RecordingQueue(enqueue_raises=RuntimeError("enqueue exploded"))
    service = _make_service(tmp_path, task_queue=queue)
    job = service.add_job("早报", "subagent.spawn", {}, schedule="0 8 * * *", repeat="cron")
    handler = _register(service)._handlers["cron.run_now"]

    r = await handler(job_id=job["job_id"])
    assert r["success"] is False
    assert r["reason"] == "dispatch_failed"
    assert "enqueue exploded" in r["error"]


@pytest.mark.asyncio
async def test_rpc_run_now_uses_manual_priority_and_reports_dispatched(tmp_path):
    """dispatched 路径：以 PRIORITY_MANUAL_RUN(+1) 入队（插队补跑队列），
    success:true 仅表示入队成功（真实结果由 AC-2 终态回写翻转）。"""
    queue = _RecordingQueue()
    service = _make_service(tmp_path, task_queue=queue)
    job = service.add_job("早报", "subagent.spawn", {}, schedule="0 8 * * *", repeat="cron")
    handler = _register(service)._handlers["cron.run_now"]

    r = await handler(job_id=job["job_id"])
    assert r["success"] is True
    assert r["mode"] == "dispatched"
    assert len(queue.enqueued) == 1
    _tid, kwargs = queue.enqueued[0]
    assert kwargs["priority"] == PRIORITY_MANUAL_RUN
    assert kwargs["queue_name"] == "cron"


def test_run_job_now_direct_run_carries_real_failure(tmp_path):
    """无 TaskQueue 直跑路径：executor 失败必须如实带回 success=False+error。"""
    service = _make_service(tmp_path, executor=lambda a, p: {"success": False, "error": "boom"})
    job = service.add_job("巡检", "system.memory_health", {}, schedule="0 4 * * *", repeat="cron")

    out = service.run_job_now(job["job_id"])
    assert out["triggered"] is True
    assert out["mode"] == "executed"
    assert out["success"] is False
    assert "boom" in out["error"]


@pytest.mark.asyncio
async def test_rpc_run_now_direct_run_success_false(tmp_path):
    service = _make_service(tmp_path, executor=lambda a, p: {"success": False, "error": "boom"})
    job = service.add_job("巡检", "system.memory_health", {}, schedule="0 4 * * *", repeat="cron")
    handler = _register(service)._handlers["cron.run_now"]

    r = await handler(job_id=job["job_id"])
    assert r["success"] is False
    assert r["mode"] == "executed"
    assert "boom" in r["error"]

    # 对照组：直跑成功 → success:true + mode=executed
    service2 = _make_service(tmp_path / "ok", executor=lambda a, p: {"success": True})
    job2 = service2.add_job("巡检", "system.memory_health", {}, schedule="0 4 * * *", repeat="cron")
    handler2 = _register(service2)._handlers["cron.run_now"]
    r2 = await handler2(job_id=job2["job_id"])
    assert r2["success"] is True and r2["mode"] == "executed"


# ── 2. 补跑错峰（_catch_up_missed stagger + 轻重分层） ─────────────────


def test_catchup_stagger_default_and_override(monkeypatch):
    monkeypatch.delenv("SIYI_CRON_CATCHUP_STAGGER", raising=False)
    assert _resolve_catchup_stagger() == 30.0
    monkeypatch.setenv("SIYI_CRON_CATCHUP_STAGGER", "5")
    assert _resolve_catchup_stagger() == 5.0
    monkeypatch.setenv("SIYI_CRON_CATCHUP_STAGGER", "0")
    assert _resolve_catchup_stagger() == 0.0  # 显式关闭错峰
    monkeypatch.setenv("SIYI_CRON_CATCHUP_STAGGER", "not-a-number")
    assert _resolve_catchup_stagger() == 30.0  # 坏值回退默认


def _seed_overdue_daily(service, names_actions):
    """注册多条 daily 任务（run_at=00:00 恒已过期、last_run 空 → 必补跑）。"""
    for name, action_id in names_actions:
        service.add_job(name, action_id, {}, run_at="00:00", repeat="daily")


def test_catch_up_postpones_each_job_with_increasing_stagger(tmp_path, monkeypatch):
    """错峰可断言：postpone 参数序列 17/34/51…严格递增互不相同；首条 defer=0 不 postpone。"""
    monkeypatch.setenv("SIYI_CRON_CATCHUP_STAGGER", "17")
    queue = _RecordingQueue()
    service = _make_service(tmp_path, task_queue=queue)
    _seed_overdue_daily(service, [("A", "journal.daily_draft"), ("B", "system.auto_backup"), ("C", "system.memory_health")])

    service._catch_up_missed()

    assert len(queue.enqueued) == 3, "三条逾期任务必须全部入队补跑"
    delays = [d for _tid, d in queue.postponed]
    assert delays == [17.0, 34.0], f"postpone 步长应为 idx*17s（首条 0s 跳过 postpone）: {queue.postponed}"
    assert len(set(delays)) == len(delays)
    assert delays == sorted(delays) and all(a < b for a, b in zip(delays, delays[1:])), "错峰必须严格递增"
    # 错峰推迟的是各自 enqueue 返回的 task_id，且不含首条
    assert queue.postponed[0][0] == queue.enqueued[1][0]
    assert queue.postponed[1][0] == queue.enqueued[2][0]


def test_catch_up_stagger_zero_restores_volley(tmp_path, monkeypatch):
    """stagger=0 → 全部 defer 0 → 零 postpone 调用（保留旧齐射行为的可回退开关）。"""
    monkeypatch.setenv("SIYI_CRON_CATCHUP_STAGGER", "0")
    queue = _RecordingQueue()
    service = _make_service(tmp_path, task_queue=queue)
    _seed_overdue_daily(service, [("A", "journal.daily_draft"), ("B", "system.auto_backup")])
    service._catch_up_missed()
    assert len(queue.enqueued) == 2
    assert queue.postponed == []


def test_catch_up_heavy_actions_sorted_last_with_bottom_priority(tmp_path, monkeypatch):
    """轻重分层：tech_watch.collect / subagent.spawn 排补跑序列尾部，
    且入队优先级垫底（PRIORITY_CATCHUP_HEAVY=-2 < PRIORITY_CATCHUP=-1）。"""
    monkeypatch.setenv("SIYI_CRON_CATCHUP_STAGGER", "10")
    queue = _RecordingQueue()
    service = _make_service(tmp_path, task_queue=queue)
    _seed_overdue_daily(
        service,
        [("瞭望", "tech_watch.collect"), ("轻", "journal.daily_draft"), ("早报", "subagent.spawn")],
    )
    service._catch_up_missed()

    order = [kw["capability_id"] for _tid, kw in queue.enqueued]
    assert order == ["journal.daily_draft", "tech_watch.collect", "subagent.spawn"]
    prios = {kw["capability_id"]: kw["priority"] for _tid, kw in queue.enqueued}
    assert prios["journal.daily_draft"] == PRIORITY_CATCHUP
    assert prios["tech_watch.collect"] == PRIORITY_CATCHUP_HEAVY
    assert prios["subagent.spawn"] == PRIORITY_CATCHUP_HEAVY
    # 序列尾部错峰最大 → 分钟级网络任务垫到最后，不再队头阻塞
    assert queue.postponed[-1][1] > queue.postponed[0][1]


# ── 3. tech_watch.collect 离线短路 ────────────────────────────────────


class _NetworkBomb:
    """任何属性访问即爆炸：证明离线模式下零网络触达。"""

    def __getattr__(self, name):
        raise AssertionError(f"SIYI_TEST_OFFLINE=1 下不得触碰网络服务: {name}")


def test_tech_watch_collect_skips_all_network_when_offline(tmp_path, monkeypatch):
    from services.tech_watch import TechWatchCollector, TechWatchStore

    monkeypatch.setenv("SIYI_TEST_OFFLINE", "1")
    store = TechWatchStore(db_path=str(tmp_path / "tech_watch.db"))
    try:
        collector = TechWatchCollector(store, web_search_service=_NetworkBomb(), web_fetch_service=_NetworkBomb())
        out = collector.collect(["技术趋势"], local_ai_client=_NetworkBomb())
        assert out["status"] == "skipped"
        assert out["reason"] == "offline test mode"
    finally:
        store.close()


# ── 4. promote 竞态（真实 PersistentTaskQueue 端到端） ────────────────


def _seed_catchup_with_postponed_heavy(tmp_path, monkeypatch):
    """补跑场景：轻任务 idx=0（defer 0）、重任务 idx=1（postpone 600s）。
    返回 (queue, service, heavy_job_id, heavy_task_id)。"""
    import time

    from services.task_queue import PersistentTaskQueue

    monkeypatch.setenv("SIYI_CRON_CATCHUP_STAGGER", "600")
    queue = PersistentTaskQueue(db_path=str(tmp_path / "tq.db"))
    service = _make_service(tmp_path, task_queue=queue)
    service.add_job("轻", "journal.daily_draft", {}, run_at="00:00", repeat="daily")
    heavy = service.add_job("瞭望", "tech_watch.collect", {}, run_at="00:00", repeat="daily")

    service._catch_up_missed()
    rows = dict(sqlite3.connect(str(tmp_path / "tq.db")).execute("SELECT capability_id, task_id FROM task_queue WHERE queue_name='cron'").fetchall())
    heavy_task_id = rows["tech_watch.collect"]
    conn = sqlite3.connect(str(tmp_path / "tq.db"))
    priority, next_retry = conn.execute("SELECT priority, next_retry_at FROM task_queue WHERE task_id=?", (heavy_task_id,)).fetchone()
    conn.close()
    assert priority == PRIORITY_CATCHUP_HEAVY
    assert next_retry > time.time() + 500, "重任务应被 postpone 600s，此刻不可调度"
    return queue, service, heavy["job_id"], heavy_task_id


def test_run_now_promotes_postponed_pending_task(tmp_path, monkeypatch):
    """补跑刚把任务 postpone 推后 → 不可调度；用户「立即执行」（priority=+1，
    enqueue 幂等复用旧 task_id）必须经 promote 拉回即时可调度。"""
    import time

    queue, service, heavy_job_id, heavy_task_id = _seed_catchup_with_postponed_heavy(tmp_path, monkeypatch)

    out = service.run_job_now(heavy_job_id, priority=PRIORITY_MANUAL_RUN)
    assert out["triggered"] is True and out["mode"] == "dispatched"

    conn = sqlite3.connect(str(tmp_path / "tq.db"))
    pending = conn.execute("SELECT task_id, priority, next_retry_at FROM task_queue WHERE queue_name='cron' AND status='pending'").fetchall()
    conn.close()
    assert len(pending) == 2, f"两条补跑任务都应仍 pending（去重不加行）: {pending}"
    promoted_id, promoted_priority, promoted_next = next(r for r in pending if r[0] == heavy_task_id)
    assert promoted_priority == PRIORITY_MANUAL_RUN
    assert promoted_next <= time.time(), "promote 应清掉 stagger 推后的 next_retry_at"

    # promote 后 priority=+1 → dequeue（priority DESC）必先取它
    task = queue.dequeue(queue_name="cron")
    assert task is not None and task.task_id == heavy_task_id, f"promote 后必须立即插队可调度: {task}"


def test_run_now_zero_priority_does_not_promote(tmp_path, monkeypatch):
    """对照组：priority=0（调度器/自动路径）不触发 promote——错峰语义保持。"""
    queue, service, heavy_job_id, heavy_task_id = _seed_catchup_with_postponed_heavy(tmp_path, monkeypatch)
    out = service.run_job_now(heavy_job_id)  # 默认 priority=0 → 不 promote
    assert out["triggered"] is True
    task = queue.dequeue(queue_name="cron")
    assert task is None or task.task_id != heavy_task_id, "未提权则重任务应保持错峰、不被取走"
