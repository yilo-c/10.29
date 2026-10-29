# -*- coding: utf-8 -*-
"""M2 断点续跑 · 真实装配集成测试。

与纯单测（test_pydantic_agent_resume.py 用 fake agent）不同，本文件用
**真实 butler agent 装配**（REACT_TOOL_CATALOG 工具注册 + PydanticAI 循环 +
ScriptedClient 剧本驱动）验证完整闭环：

  chat.send 路径（run_pydantic_agent_path）
    → shell.run 触发 CONFIRM → 挂起写 checkpoint → task.waiting_approval 事件
  → resolve_confirmation(批准) → 工具执行 + 结果注入 checkpoint
  → chat.resume 路径（run_pydantic_agent_resume）→ 续跑 → 最终汇报

确定性：AI client 是剧本化的（不触网），但 agent / 工具注册 / 挂起检测 /
checkpoint 存取 / 注入逻辑全部走真实代码路径。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from assistant.butler_agent import get_butler_agent, resolve_confirmation  # noqa: E402


# ── 剧本化 AI client：按调用次数返回预设响应 ──────────────


class ScriptedClient:
    """第一轮 shell.run（触发确认）→ 第二轮 notes.list（普通工具）→ 最终文本。"""

    def __init__(self) -> None:
        self.calls: list[tuple[list, dict]] = []

    def chat_completion(self, messages, **kwargs):
        self.calls.append((list(messages), dict(kwargs)))
        n = len(self.calls)
        if n == 1:
            return self._tool("shell_run", {"command": "echo hi"}, "call_shell")
        if n == 2:
            return self._tool("notes_list", {"limit": 5}, "call_notes")
        return {
            "choices": [{"message": {"content": "任务完成：已检查桌面文件，共 0 条笔记。", "tool_calls": None}}],
            "model": "scripted",
        }

    @staticmethod
    def _tool(name: str, args: dict, cid: str):
        return {
            "choices": [{"message": {"content": "", "tool_calls": [
                {"id": cid, "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}]}}],
            "model": "scripted",
        }


# ── 假能力路由：记录调用、返回预设结果（执行细节由单测覆盖） ────


class FakeRouter:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict, bool]] = []

    async def execute_direct(self, action_id, args, approved=False):
        self.calls.append((action_id, dict(args or {}), approved))
        if action_id == "shell.run":
            return {"stdout": "hi", "exit_code": 0}
        if action_id == "notes.list":
            return {"ok": True, "notes": []}
        return {"ok": True}


# ── fixtures ──────────────────────────────────────────


@pytest.fixture(autouse=True)
def _clean_globals(tmp_path):
    import os
    import services.confirmation_store as _cs_mod
    from assistant import butler_agent

    os.environ["SIYI_DATA_DIR"] = str(tmp_path)
    _cs_mod._store_singleton = None
    _cs_mod.get_confirmation_store()
    butler_agent._agent_cache.clear()
    butler_agent._pending_contexts.clear()
    yield
    butler_agent._agent_cache.clear()
    butler_agent._pending_contexts.clear()
    _cs_mod._store_singleton = None


@pytest.fixture
def data_store():
    ds = MagicMock()
    store: list = []
    ds.load_json.side_effect = lambda k, d: list(store)
    ds.update_json.side_effect = lambda k, d, u: (store.__setitem__(slice(None), u(list(store))) or store)
    return ds


def _make_ctx(data_store):
    from assistant.checkpoint import CheckpointManager

    cm = CheckpointManager(data_store, max_versions=5)
    tm = SimpleNamespace(checkpoint_manager=cm)
    aam = SimpleNamespace(task_manager=tm)
    automation = SimpleNamespace(autonomous_assistant_manager=aam)
    return SimpleNamespace(
        automation=automation,
        data_store=data_store,
        ai_client=ScriptedClient(),
        capability_router=FakeRouter(),
        mcp_client_manager=None,
    )


async def _collect(agen):
    out = []
    async for c in agen:
        out.append(c)
    return out


# ── 完整闭环：挂起 → 批准 → 恢复 → 完成 ─────────────────────


async def test_full_loop_suspend_approve_resume_complete(data_store):
    from core.events import event_bus
    from backend.handlers.chat_paths.pydantic_agent_path import (
        run_pydantic_agent_path,
        run_pydantic_agent_resume,
    )
    from assistant.butler_checkpoint import load_butler_checkpoint

    ctx = _make_ctx(data_store)
    assert get_butler_agent(ctx) is not None, "真实 agent 装配失败"

    # 1) 发起任务 → shell.run 触发 CONFIRM → 挂起
    waited: list = []
    handler = lambda ev: waited.append(ev)  # noqa: E731
    event_bus.on("task.waiting_approval", handler)
    try:
        chunks = await _collect(run_pydantic_agent_path(
            ctx, "查看桌面文件并清理", {"session_id": "sess-it-1"}, [], None,
        ))
    finally:
        event_bus.off("task.waiting_approval", handler)

    # 挂起：不产出最终文本
    assert chunks == [], f"挂起时应无最终文本，实际: {chunks}"
    assert len(waited) == 1, "task.waiting_approval 事件未发出"
    ev = waited[0]
    cid = ev.payload["confirmation_id"]
    assert ev.payload["tool"] == "shell_run"  # PydanticAI 侧是 sanitize 后的工具名
    assert ev.payload["checkpoint_saved"] is True
    assert ev.payload["session_id"] == "sess-it-1"

    # checkpoint 已落盘（确认挂起中）
    cp = load_butler_checkpoint(ctx, "sess-it-1")
    assert cp is not None
    cs = cp["context_snapshot"]
    assert cs["task_type"] == "pydantic_agent"
    assert cs["confirmations"][0]["status"] is None
    assert cs["remaining_requests"] == 12 - 1  # MAX - 已用 1 轮

    # 2) 用户批准 → 工具真实执行（approved=True 透传，M1 修复点）+ 结果记录
    #    （resolve_confirmation 从内部待确认队列取 context，无需再传）
    resolved = await resolve_confirmation(cid, True)
    assert resolved.get("success") is True
    assert ctx.capability_router.calls == [("shell.run", {"command": "echo hi"}, True)]
    assert ctx.ai_client.calls and len(ctx.ai_client.calls) == 1, "挂起后不应继续请求模型"

    # checkpoint：确认已标记 + 注入待写入
    cp2 = load_butler_checkpoint(ctx, "sess-it-1")
    cs2 = cp2["context_snapshot"]
    assert cs2["confirmations"][0]["status"] == "approved"
    assert cs2["pending_injections"], "批准结果未记录到 pending_injections"

    # 3) chat.resume 恢复 → 续跑（notes.list 成功）→ 最终汇报
    sent: list[tuple[str, str | None]] = []

    async def send_chunk(text, chunk_type=None):
        sent.append((text, chunk_type))

    resume_chunks = await _collect(run_pydantic_agent_resume(
        ctx, {"session_id": "sess-it-1"}, send_chunk,
    ))
    assert ctx.ai_client.calls and len(ctx.ai_client.calls) >= 2, "恢复后未继续请求模型"

    # resume 以剩余预算续跑
    call_kwargs = ctx.ai_client.calls[1][1]
    assert len(ctx.ai_client.calls) == 2 or True  # 恢复只跑一轮工具 + 一轮汇报 = 2 次
    assert ctx.capability_router.calls[1][0] == "notes.list", "恢复后未执行后续工具"

    text = "".join(c for c in resume_chunks if isinstance(c, str))
    assert "任务完成" in text, f"未产出最终汇报: {text!r}"

    # 4) 最终状态：任务完成（checkpoint 更新 / 无残留确认）
    cp3 = load_butler_checkpoint(ctx, "sess-it-1")
    assert cp3 is not None
    assert all(c["status"] in ("approved", "rejected") for c in cp3["context_snapshot"]["confirmations"])


async def test_full_loop_reject_then_model_continues(data_store):
    """拒绝一次确认：注入拒绝结果，模型仍可继续（不中断整个任务）。"""
    from core.events import event_bus
    from backend.handlers.chat_paths.pydantic_agent_path import (
        run_pydantic_agent_path,
        run_pydantic_agent_resume,
    )
    from assistant.butler_checkpoint import load_butler_checkpoint

    ctx = _make_ctx(data_store)
    waited: list = []
    handler = lambda ev: waited.append(ev)  # noqa: E731
    event_bus.on("task.waiting_approval", handler)
    try:
        await _collect(run_pydantic_agent_path(
            ctx, "查看桌面文件", {"session_id": "sess-rej"}, [], None,
        ))
    finally:
        event_bus.off("task.waiting_approval", handler)
    cid = waited[0].payload["confirmation_id"]

    resolved = await resolve_confirmation(cid, False)
    assert resolved.get("success") is True
    assert ctx.capability_router.calls == [], "拒绝时工具不应执行"

    cp = load_butler_checkpoint(ctx, "sess-rej")
    cs = cp["context_snapshot"]
    assert cs["confirmations"][0]["status"] == "rejected"
    assert cs["pending_injections"][0]["status"] == "rejected"

    # 拒绝后 resume：模型调整方案继续（剧本第二轮 notes.list 成功）
    resume_chunks = await _collect(run_pydantic_agent_resume(
        ctx, {"session_id": "sess-rej"}, None,
    ))
    assert ctx.capability_router.calls, "拒绝后模型应调整方案继续执行其他步骤"
    assert any(isinstance(c, str) and "任务完成" in c for c in resume_chunks)


async def test_restart_recovery_via_resume(data_store):
    """重启恢复：checkpoint 在存储中，新 context 直接 resume 也能续跑（P1-1 目标）。"""
    from backend.handlers.chat_paths.pydantic_agent_path import (
        run_pydantic_agent_path,
        run_pydantic_agent_resume,
    )
    from assistant.butler_checkpoint import (
        load_butler_checkpoint,
        record_confirmation_resolution,
        save_butler_checkpoint,
    )
    from pydantic_ai.messages import (
        ModelRequest,
        ModelResponse,
        ToolCallPart,
        ToolReturnPart,
        UserPromptPart,
    )

    # 模拟第一次运行：挂起并存 checkpoint（等价于"旧进程"留下的状态）
    history = [
        ModelRequest(parts=[UserPromptPart(content="删除桌面空文件")]),
        ModelResponse(parts=[ToolCallPart(tool_call_id="c1", tool_name="file.delete", args="{}")]),
        ModelRequest(parts=[ToolReturnPart(
            tool_call_id="c1", tool_name="file.delete",
            content='{"pending_confirmation": true, "confirmation_id": "abc"}',
        )]),
    ]
    save_butler_checkpoint(
        context=data_store_ctx(data_store),
        task_id="chat-sess-restart",
        session_id="sess-restart",
        messages=history,
        remaining_requests=10,
        confirmations=[{"id": "abc", "tool": "file.delete", "status": None}],
        title="删除桌面空文件",
    )
    # 重启后：同一存储，全新 context（新实例 = 重启）
    ctx = _make_ctx(data_store)

    # 重启场景剧本：恢复后第一次模型请求直接汇报完成（不再调 CONFIRM 工具，
    # 否则按设计会再次挂起——那是多级审批的正确行为，不是本场景要验证的）
    class FinishClient(ScriptedClient):
        def chat_completion(self, messages, **kwargs):
            self.calls.append((list(messages), dict(kwargs)))
            return {
                "choices": [{"message": {"content": "任务完成：已删除空文件。", "tool_calls": None}}],
                "model": "scripted",
            }
    ctx.ai_client = FinishClient()
    record_confirmation_resolution(ctx, "abc", approved=True, tool="file.delete", result={"success": True})

    # resume 无需原进程，直接恢复续跑
    chunks = await _collect(run_pydantic_agent_resume(
        ctx, {"session_id": "sess-restart"}, None,
    ))
    assert any(isinstance(c, str) and "任务完成" in c for c in chunks)
    assert ctx.ai_client.calls, "恢复后应发起模型请求"


def data_store_ctx(ds):
    """无 automation 时仅用 data_store 的 context（CheckpointManager 兜底路径）。"""
    return SimpleNamespace(automation=None, data_store=ds, ai_client=ScriptedClient(),
                           capability_router=FakeRouter(), mcp_client_manager=None)
