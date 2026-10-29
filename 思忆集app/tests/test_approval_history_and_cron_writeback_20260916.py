# -*- coding: utf-8 -*-
"""任务 #26（八轮实测 AJ-3 + AC-2）回归：审批留痕与 cron 队列回写忠实性。

AJ-3：rpc 类审批 approve 分支直接 pop 后重放、不经 ``mgr.approve()``，是唯一
"批准执行却无审计留痕"的路径（reject/timeout 都有）。修后 approve 成功记
``event="approved"``、重放失败/缺 registry 记 ``event="failed"``。

AC-2：cron dispatch 入队记的 ``{success:False, status:"dispatched"}`` 占位条目
此前永远停留（全仓无"队列完成→更新 job.history"路径）。修后 task_queue worker
到达真终态（completed/dead_letter）经 ``on_task_final`` 回调 → CronService
``on_queue_task_final`` 把**同一条目**翻转为真实结果。
"""

from __future__ import annotations

import asyncio

import pytest


# ── AJ-3：rpc approve 留痕 ────────────────────────────────────


class FakeRpcManager:
    """最小 manager：只实现 rpc approve 路径用到的成员。"""

    def __init__(self, entry: dict) -> None:
        self._entry = entry
        self._pending: dict[str, dict] = {}
        self.history: list[dict] = []
        self.approve_calls = 0

    def get(self, pid: str):
        return self._entry if self._entry.get("pending_id") == pid else None

    def approve(self, pid: str):  # rpc 路径不应触达，计数用于反证
        self.approve_calls += 1
        return {"success": True, "pending_id": pid}

    def record_history(self, event, entry, *, status="", error=""):
        self.history.append(
            {
                "event": event,
                "status": status,
                "error": error,
                "pending_id": entry.get("pending_id"),
                "action_id": entry.get("action_id"),
                "kind": entry.get("kind"),
            }
        )
        return self.history[-1]


class FakeRegistry:
    def __init__(self, result=None, exc=None):
        self._result = result
        self._exc = exc
        self.calls = []

    async def dispatch(self, action_id, args, *, skip_approval=False):
        self.calls.append((action_id, dict(args), skip_approval))
        if self._exc is not None:
            raise self._exc
        return self._result


def _rpc_entry(pid="p1", action="notes.delete"):
    return {
        "pending_id": pid,
        "action_id": action,
        "args": {"note_id": 1},
        "kind": "rpc",
        "status": "pending",
        "created_at": 1.0,
        "requester_conn": None,
    }


def test_rpc_approve_success_records_history(monkeypatch):
    mgr = FakeRpcManager(_rpc_entry())
    monkeypatch.setattr("backend.handlers._sandbox.get_sandbox_manager", lambda: mgr)
    from backend.handlers import _sandbox as sb

    reg = FakeRegistry(result={"ok": True})
    result = asyncio.run(sb._handle_sandbox_approve("p1", registry=reg))

    assert result["success"] is True
    assert reg.calls == [("notes.delete", {"note_id": 1}, True)]
    # 关键断言：批准执行必须留痕，且动作类型 event="approved"
    assert len(mgr.history) == 1
    assert mgr.history[0]["event"] == "approved"
    assert mgr.history[0]["pending_id"] == "p1"
    assert mgr.history[0]["kind"] == "rpc"
    # rpc 路径不应误调 mgr.approve（否则二次执行）
    assert mgr.approve_calls == 0


def test_rpc_approve_dispatch_failure_records_failed(monkeypatch):
    mgr = FakeRpcManager(_rpc_entry())
    monkeypatch.setattr("backend.handlers._sandbox.get_sandbox_manager", lambda: mgr)
    from backend.handlers import _sandbox as sb

    reg = FakeRegistry(exc=RuntimeError("replay exploded"))
    result = asyncio.run(sb._handle_sandbox_approve("p1", registry=reg))

    assert result["success"] is False
    assert "replay exploded" in result["error"]
    assert len(mgr.history) == 1
    assert mgr.history[0]["event"] == "failed"
    assert "replay exploded" in mgr.history[0]["error"]


def test_rpc_approve_missing_registry_records_failed(monkeypatch):
    mgr = FakeRpcManager(_rpc_entry())
    monkeypatch.setattr("backend.handlers._sandbox.get_sandbox_manager", lambda: mgr)
    from backend.handlers import _sandbox as sb

    result = asyncio.run(sb._handle_sandbox_approve("p1", registry=None))

    assert result["success"] is False
    assert "registry" in result["error"]
    # 缺 registry 也是"批准却无法重放"，必须留痕（此前静默丢失）
    assert len(mgr.history) == 1
    assert mgr.history[0]["event"] == "failed"


# ── AC-2：cron dispatched 历史回写（端到端：真队列 + worker + cron）────────


class FakeCronStore:
    """最小 cron kv：load/save_json，隔离真实磁盘。"""

    def __init__(self) -> None:
        self.data: dict[str, object] = {}

    def load_json(self, key, default):
        return self.data.get(key, default)

    def save_json(self, key, value):
        self.data[key] = value

    def update_json(self, key, default, fn):
        self.data[key] = fn(self.data.get(key, default))
        return self.data[key]


def _make_cron_with_queue(tmp_path):
    from services.cron_service import CronService
    from services.task_queue import PersistentTaskQueue

    store = FakeCronStore()
    queue = PersistentTaskQueue(db_path=str(tmp_path / "task_queue.db"))
    service = CronService(data_store=store, task_queue=queue)
    return service, queue


def test_cron_dispatch_then_worker_complete_flips_history(tmp_path):
    from services.task_queue import TaskQueueWorker

    service, queue = _make_cron_with_queue(tmp_path)
    service.add_job(
        name="巡检",
        action_id="info.collect",
        payload={"x": 1},
        repeat="interval",
        run_every_days=1,
        max_retries=0,
    )
    job = service._jobs[0]

    # 触发 dispatch：走 task_queue 路径，记 dispatched 占位 + 登记回写索引
    service._execute_job(job)
    hist = service.get_job_history(job.job_id)
    assert len(hist) == 1
    assert hist[0]["status"] == "dispatched"
    assert hist[0]["success"] is False
    assert hist[0]["output"] and "dispatched_to_task_queue:" in hist[0]["output"]

    # 出队（worker 视角）→ 执行成功 → complete → on_task_final → cron 回写
    task = queue.dequeue(queue_name="cron", lease_seconds=600.0)
    assert task is not None

    def _executor(_task):
        return {"success": True, "receipt_id": "rc-1"}

    captured: list[tuple] = []

    def _on_final(tid, status, error):
        captured.append((tid, status, error))
        service.on_queue_task_final(tid, final_status=status, error=error)

    worker = TaskQueueWorker(queue, executor=_executor, queue_name="cron", on_task_final=_on_final)
    asyncio.run(worker._run_task(task))

    assert captured == [(task.task_id, "completed", "")]
    flipped = service.get_job_history(job.job_id)
    assert len(flipped) == 1
    assert flipped[0]["success"] is True
    assert flipped[0]["status"] == "completed"
    assert flipped[0]["error"] is None
    # 持久化：翻转结果已落 kv（跨读取可见）
    saved = service._data_store.data["cron_jobs"]
    assert saved[0]["history"][0]["success"] is True


def test_cron_dispatch_then_dead_letter_flips_history_failed(tmp_path):
    from services.task_queue import TaskQueueWorker

    service, queue = _make_cron_with_queue(tmp_path)
    service.add_job(
        name="采集",
        action_id="info.collect",
        payload={"x": 2},
        repeat="interval",
        run_every_days=1,
        max_retries=0,
    )
    job = service._jobs[0]
    service._execute_job(job)

    task = queue.dequeue(queue_name="cron", lease_seconds=600.0)

    def _executor(_task):
        # blocked → worker 走 immediate_dead_letter，一次即真终态（无需重试耗尽）
        return {"success": False, "blocked": True, "error": "boom-hard"}

    def _on_final(tid, status, error):
        service.on_queue_task_final(tid, final_status=status, error=error)

    worker = TaskQueueWorker(queue, executor=_executor, queue_name="cron", on_task_final=_on_final)
    asyncio.run(worker._run_task(task))

    flipped = service.get_job_history(job.job_id)
    assert flipped[0]["success"] is False
    assert flipped[0]["status"] == "dead_letter"
    assert "boom-hard" in (flipped[0]["error"] or "")
    # dead_letter 计入连续失败
    assert job.consecutive_failures == 1


def test_on_queue_task_final_is_idempotent_and_ignores_unknown(tmp_path):
    service, _queue = _make_cron_with_queue(tmp_path)
    service.add_job(
        name="任务",
        action_id="a.b",
        payload={},
        repeat="interval",
        run_every_days=1,
        max_retries=0,
    )
    job = service._jobs[0]
    service._execute_job(job)
    tid = next(iter(service._dispatch_index))

    entry = service.on_queue_task_final(tid, final_status="completed")
    assert entry is not None and entry["success"] is True
    # 重复回调：索引已 pop → 返回 None，绝不二次覆盖
    assert service.on_queue_task_final(tid, final_status="dead_letter", error="late") is None
    assert service.get_job_history(job.job_id)[0]["success"] is True
    # 未知 task_id（非 cron 任务/重启后）→ None，不影响任何历史
    assert service.on_queue_task_final("nope", final_status="completed") is None


def test_on_queue_task_final_empty_task_id_returns_none(tmp_path):
    service, _queue = _make_cron_with_queue(tmp_path)
    assert service.on_queue_task_final("", final_status="completed") is None


# ── worker on_task_final 旁路：异常不得冒泡回主循环 ─────────────


def test_worker_on_task_final_exception_is_swallowed(tmp_path):
    from services.task_queue import PersistentTaskQueue, TaskQueueWorker

    queue = PersistentTaskQueue(db_path=str(tmp_path / "tq.db"))
    tid = queue.enqueue(capability_id="a.b", payload={}, queue_name="cron")
    task = queue.dequeue(queue_name="cron", lease_seconds=600.0)
    assert task is not None and task.task_id == tid

    def _bad(_tid, _status, _err):
        raise RuntimeError("callback boom")

    worker = TaskQueueWorker(
        queue,
        executor=lambda _t: {"success": True},
        queue_name="cron",
        on_task_final=_bad,
    )
    # 回调抛错不应让 _run_task 冒泡；任务照常置 completed
    asyncio.run(worker._run_task(task))
    assert queue.get(tid).status == "completed"
