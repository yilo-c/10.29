# -*- coding: utf-8 -*-
"""S3-2 异步 RPC 骨架端到端契约：queued_rpc → async_rpc_worker → poll_job_result。

agent-I 交付的 6 处慢同步 RPC 流式化全部依赖 `_async_rpc` 这条共享骨架，但
交付时**没有任何一条测试坐实"入队秒回 job_id → worker 执行 → 轮询取回真结果"
的闭环**（其自称新增的 personal +3 例根本没落盘）。本测试用真
PersistentTaskQueue 跑完整闭环，是"信任但核实"的第二层验证。

覆盖契约：
1. queued_rpc 队列可用 → {success, job_id, status:"queued"}，且真入队一条 pending。
2. async_rpc_worker 正常返回 → 记结果 + 落 completed；业务失败（result 含
   success=False）仍按任务 completed（不让业务失败污染任务终态，见骨架 docstring）。
3. runner 抛异常 → 任务真失败（success=False + error），交回 worker 走 dead_letter。
4. poll_job_result：completed 取回 stored 结果；queued 返回未完成。
5. 队列不可用（context.task_queue=None）→ queued_rpc 返回 None，调用方降级同步。
"""

from __future__ import annotations

import asyncio
import sys
import types
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from backend.handlers._async_rpc import async_rpc_worker, poll_job_result, queued_rpc
from services.task_queue import PersistentTaskQueue


def _ctx_with_queue(q):
    ctx = types.SimpleNamespace()
    ctx.task_queue = q
    return ctx


def test_queued_rpc_enqueues_and_returns_job_id(tmp_path):
    q = PersistentTaskQueue(db_path=str(tmp_path / "tq.db"))
    ctx = _ctx_with_queue(q)

    async def _drive():
        enq = await queued_rpc(ctx, capability_id="evolution.scan_and_report", payload={})
        assert enq is not None
        assert enq["success"] is True
        assert enq["status"] == "queued"
        job_id = enq["job_id"]
        assert job_id
        # 队列里确有对应 pending 记录
        rec = q.get(job_id)
        assert rec is not None and rec.status == "pending"
        assert rec.capability_id == "evolution.scan_and_report"
        # 预生成 job_id 已塞进 payload._job_id（worker 侧据此发匹配事件）
        assert rec.payload.get("_job_id") == job_id

    asyncio.run(_drive())


def test_queued_rpc_none_when_queue_unavailable():
    """context 无 task_queue（测试/精简启动）→ 返回 None，调用方走同步降级。"""
    ctx = types.SimpleNamespace()  # 无 task_queue 属性
    assert asyncio.run(queued_rpc(ctx, capability_id="x", payload={})) is None


def test_worker_completed_records_result_and_poll_retrieves(tmp_path):
    q = PersistentTaskQueue(db_path=str(tmp_path / "tq.db"))
    ctx = _ctx_with_queue(q)
    seen = {}

    def runner(_context, payload):
        seen["got"] = payload
        return {"items": ["chromadb"], "generated_at": "2026-09-16T08:00:00"}

    handler = async_rpc_worker("evolution.scan_and_report", runner=runner)

    async def _drive():
        enq = await queued_rpc(ctx, capability_id="evolution.scan_and_report", payload={})
        job_id = enq["job_id"]
        task = q.dequeue(queue_name="cron")
        assert task is not None and task.task_id == job_id
        # worker 执行：payload 含 _job_id，handler 会 pop 掉再交给 runner
        result = handler(ctx, dict(task.payload))
        assert result["success"] is True
        assert result["status"] == "completed"
        # runner 不应看到内部键 _job_id
        assert "_job_id" not in seen["got"]
        q.complete(task_id=job_id)
        # 轮询取回真结果（stored 融合进返回）
        poll = await poll_job_result(ctx, job_id=job_id, capability_id="evolution.scan_and_report")
        assert poll["success"] is True
        assert poll["status"] == "completed"
        assert poll["items"] == ["chromadb"]
        assert poll["generated_at"] == "2026-09-16T08:00:00"

    asyncio.run(_drive())


def test_business_failure_still_completes_task(tmp_path):
    """runner 返回业务 success=False → 任务仍 completed（答案原样交客户端），非 dead_letter。"""
    q = PersistentTaskQueue(db_path=str(tmp_path / "tq.db"))
    ctx = _ctx_with_queue(q)
    handler = async_rpc_worker(
        "translate.text",
        runner=lambda c, p: {"success": False, "error": "ai returned non-json"},
    )

    async def _drive():
        enq = await queued_rpc(ctx, capability_id="translate.text", payload={"text": "hi"})
        job_id = enq["job_id"]
        task = q.dequeue(queue_name="cron")
        result = handler(ctx, dict(task.payload))
        # 任务层成功（success True），业务错误在 result 内
        assert result["success"] is True
        assert result["result"]["success"] is False
        q.complete(task_id=job_id)
        poll = await poll_job_result(ctx, job_id=job_id, capability_id="translate.text")
        assert poll["status"] == "completed"
        assert poll["success"] is False and "non-json" in poll["error"]

    asyncio.run(_drive())


def test_runner_exception_marks_task_failed(tmp_path):
    """runner 抛异常 → 任务 success=False，交回 worker 走 fail()/dead_letter。"""
    q = PersistentTaskQueue(db_path=str(tmp_path / "tq.db"))
    ctx = _ctx_with_queue(q)

    def boom(_c, _p):
        raise RuntimeError("scan exploded")

    handler = async_rpc_worker("evolution.scan_and_report", runner=boom)

    async def _drive():
        enq = await queued_rpc(ctx, capability_id="evolution.scan_and_report", payload={})
        job_id = enq["job_id"]
        task = q.dequeue(queue_name="cron")
        result = handler(ctx, dict(task.payload))
        assert result["success"] is False
        assert "scan exploded" in result["error"]
        # 模拟 worker 依 success=False 走 fail 路径
        q.fail(task_id=job_id, error=result["error"], immediate_dead_letter=True)
        poll = await poll_job_result(ctx, job_id=job_id, capability_id="evolution.scan_and_report")
        assert poll["success"] is False
        assert poll["status"] == "failed"
        assert "scan exploded" in poll["error"]

    asyncio.run(_drive())


def test_poll_queued_when_not_done(tmp_path):
    q = PersistentTaskQueue(db_path=str(tmp_path / "tq.db"))
    ctx = _ctx_with_queue(q)

    async def _drive():
        enq = await queued_rpc(ctx, capability_id="personal.review", payload={})
        job_id = enq["job_id"]
        poll = await poll_job_result(ctx, job_id=job_id, capability_id="personal.review")
        assert poll["success"] is True
        assert poll["status"] == "queued"

    asyncio.run(_drive())
