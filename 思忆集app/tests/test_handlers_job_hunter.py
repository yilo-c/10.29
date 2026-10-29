"""job_hunter.scrape.run 任务化改造测试（P2 风险册，rpc-census-2026-08-31）。

改造前：scrape.run 内联 await svc.search(...)，长任务（可达 15s+）同步阻塞
RPC，普查必超时。
改造后：入队 task_queue（queue_name="cron"，capability_id="job_hunter.scrape"）
并立即返回 {"job_id", "status": "queued"}；实际抓取由 worker 经
services.cron_actions 的 "job_hunter.scrape" action 执行；
客户端用 job_hunter.scrape.status(job_id=...) 轮询。

本文件覆盖：
- scrape.run 入队后立即返回 job_id（svc.search 不被同步调用）
- 任务完成后 scrape_status 可查到 completed
- scrape_status 不带 job_id 时保留旧契约（返回调度器配置）
- task_queue 不可用时降级为旧同步行为
- worker 侧 cron action "job_hunter.scrape" 端到端执行 svc.search 并发事件
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

import pytest

from backend.method_registry import MethodRegistry
from services.cron_actions import execute_cron_action
from services.task_queue import PersistentTaskQueue

from backend.handlers import _job_hunter


class _FakeService:
    """记录 search 调用的假 JobHunterService。"""

    def __init__(self, result: dict | None = None, delay: float = 0.0):
        self.search_calls: list[dict] = []
        self._result = result or {
            "success": True,
            "total_fetched": 2,
            "added": 2,
            "added_ids": ["j1", "j2"],
            "duplicates": 0,
            "platform_counts": {"boss": 2},
            "errors": [],
        }
        self._delay = delay

    async def search(self, platforms, keywords, city="", page=1):
        self.search_calls.append({"platforms": platforms, "keywords": keywords, "city": city, "page": page})
        if self._delay:
            await asyncio.sleep(self._delay)
        return dict(self._result)


@pytest.fixture
def queue(tmp_path: Path) -> PersistentTaskQueue:
    q = PersistentTaskQueue(db_path=str(tmp_path / "task_queue.db"))
    yield q
    q.close_all()


def _make_registry(svc: _FakeService, q: PersistentTaskQueue | None) -> MethodRegistry:
    reg = MethodRegistry()
    ctx = SimpleNamespace(job_hunter_service=svc, task_queue=q)
    _job_hunter.register_job_hunter(reg, ctx)
    return reg


def test_scrape_run_enqueues_and_returns_immediately(queue: PersistentTaskQueue):
    """入队立即返回 job_id，且不等待 svc.search 完成。"""
    svc = _FakeService(delay=30.0)  # 若被同步等待，本测试将卡死/超时
    reg = _make_registry(svc, queue)

    result = asyncio.run(reg.dispatch("job_hunter.scrape.run", {"platforms": ["boss"], "keywords": ["python"]}))

    assert result["success"] is True
    assert result["status"] == "queued"
    assert result["job_id"] == result["task_id"]
    assert result["queue"] == "cron"
    # RPC 立即返回：抓取尚未被执行
    assert svc.search_calls == []
    # 任务已持久化在队列中
    rec = queue.get(result["job_id"])
    assert rec is not None
    assert rec.status == "pending"
    assert rec.capability_id == "job_hunter.scrape"
    assert rec.payload["platforms"] == ["boss"]
    assert rec.payload["page"] == 1


def test_scrape_run_and_worker_completes_status_queryable(queue: PersistentTaskQueue):
    """入队 → 状态 queued → worker 执行完成后状态可查为 completed。"""
    svc = _FakeService()
    reg = _make_registry(svc, queue)

    enq = asyncio.run(reg.dispatch("job_hunter.scrape.run", {}))
    task_id = enq["job_id"]

    st0 = asyncio.run(reg.dispatch("job_hunter.scrape.status", {"job_id": task_id}))
    assert st0["success"] is True
    assert st0["status"] == "queued"
    assert st0["task"]["capability_id"] == "job_hunter.scrape"

    # 模拟 cron worker 消费（dequeue → 经 cron action 执行 → complete）
    task = queue.dequeue(queue_name="cron")
    assert task is not None and task.task_id == task_id
    ctx = SimpleNamespace(job_hunter_service=svc)
    out = execute_cron_action(ctx, "job_hunter.scrape", dict(task.payload))
    assert out["success"] is True
    assert out["status"] == "completed"
    assert queue.complete(task.task_id)

    st1 = asyncio.run(reg.dispatch("job_hunter.scrape.status", {"job_id": task_id}))
    assert st1["success"] is True
    assert st1["status"] == "completed"
    # 抓取参数正确透传给 svc.search
    assert svc.search_calls and svc.search_calls[0]["page"] == 1

    # task_id 别名同样可查
    st2 = asyncio.run(reg.dispatch("job_hunter.scrape.status", {"task_id": task_id}))
    assert st2["status"] == "completed"


def test_scrape_status_unknown_task_id(queue: PersistentTaskQueue):
    reg = _make_registry(_FakeService(), queue)
    result = asyncio.run(reg.dispatch("job_hunter.scrape.status", {"job_id": "nonexistent"}))
    assert result["success"] is False
    assert result["error"] == "task not found"


def test_scrape_status_int_job_id_does_not_crash(queue: PersistentTaskQueue):
    """审查回归（2026-08-31）：客户端把 job_id 传成 int → 不得 AttributeError
    （表现为 result=null），应 str 归一化后走同一「task not found」路径。"""
    reg = _make_registry(_FakeService(), queue)
    result = asyncio.run(reg.dispatch("job_hunter.scrape.status", {"job_id": 999999}))
    assert result is not None, "int job_id 崩溃导致 handler 返回 None"
    assert result["success"] is False
    assert result["error"] == "task not found"


def test_scrape_status_without_job_id_keeps_scheduler_contract():
    """无 job_id：保留既有契约，返回调度器配置。"""
    reg = _make_registry(_FakeService(), None)
    result = asyncio.run(reg.dispatch("job_hunter.scrape.status", {}))
    assert result["success"] is True
    assert "scheduler" in result


def test_scrape_run_fallback_without_queue():
    """task_queue 不可用时降级为旧同步行为（直接返回抓取结果）。"""
    svc = _FakeService()
    reg = _make_registry(svc, None)

    result = asyncio.run(reg.dispatch("job_hunter.scrape.run", {"keywords": ["python"]}))
    assert result["success"] is True
    assert result["total_fetched"] == 2
    assert len(svc.search_calls) == 1


def test_cron_action_emits_completed_event(queue: PersistentTaskQueue):
    """worker 侧 action 执行成功后发出 job_hunter.scrape.completed 事件。"""
    from core.events import event_bus

    svc = _FakeService()
    received: list = []

    def _on_completed(event) -> None:
        received.append(event)

    event_bus.on("job_hunter.scrape.completed", _on_completed)
    try:
        ctx = SimpleNamespace(job_hunter_service=svc)
        out = execute_cron_action(ctx, "job_hunter.scrape", {"platforms": ["boss"], "keywords": ["python"], "city": "", "page": 1})
    finally:
        event_bus.off("job_hunter.scrape.completed", _on_completed)

    assert out["success"] is True
    assert len(received) == 1
    assert received[0].payload["success"] is True
    assert len(svc.search_calls) == 1


def test_cron_action_without_service_fails_cleanly():
    """job_hunter_service 缺失时返回干净的失败结果（不抛异常炸 worker）。"""
    ctx = SimpleNamespace(job_hunter_service=None)
    out = execute_cron_action(ctx, "job_hunter.scrape", {})
    assert out["success"] is False
    assert "job_hunter_service" in out["error"]
