"""PydanticAI 管家路径：挂起 checkpoint / chat.resume 恢复（M2）。"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

import pytest
from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)

from assistant.checkpoint import CheckpointManager


# ── fakes ─────────────────────────────────────────────


class FakeToolResultEvent:
    """真实 FunctionToolResultEvent 包装（isinstance 需匹配 pydantic_ai 类型）。"""

    def __init__(self, content: str, tool_name: str = "shell.run"):
        from pydantic_ai.messages import FunctionToolResultEvent, ToolReturnPart

        self.event = FunctionToolResultEvent(
            part=ToolReturnPart(
                tool_call_id="c1", tool_name=tool_name, content=content,
            ),
            content=None,
        )


class FakeRunEvents:
    """AgentRunEvents 模拟：迭代事件、暴露 all_messages / usage。"""

    def __init__(self, events, messages, requests=2):
        self._events = list(events)
        self._messages = list(messages)
        self._i = 0
        self.usage = SimpleNamespace(requests=requests)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self._i >= len(self._events):
            raise StopAsyncIteration
        ev = self._events[self._i]
        self._i += 1
        # 解包真实事件（FakeToolResultEvent.event）
        return getattr(ev, "event", ev)

    def all_messages(self):
        return list(self._messages)


class FakeAgent:
    """记录 run_stream_events 调用参数，返回可控事件流。"""

    def __init__(self, events, messages, requests=2):
        self.events = events
        self.messages = messages
        self.requests = requests
        self.calls = []

    def run_stream_events(self, **kwargs):
        self.calls.append(dict(kwargs))
        return FakeRunEvents(self.events, self.messages, self.requests)


def _messages():
    return [
        ModelRequest(parts=[UserPromptPart(content="删除桌面空文件")]),
        ModelResponse(parts=[ToolCallPart(tool_call_id="c1", tool_name="file.delete", args="{}")]),
        ModelRequest(parts=[ToolReturnPart(
            tool_call_id="c1", tool_name="file.delete",
            content='{"pending_confirmation": true, "confirmation_id": "abc"}',
        )]),
    ]


# ── fixtures ──────────────────────────────────────────


@pytest.fixture
def data_store():
    ds = MagicMock()
    store: list = []

    def load_json(key, default):
        return list(store)

    def update_json(key, default, updater):
        nonlocal store
        store = updater(list(store))
        return store

    ds.load_json.side_effect = load_json
    ds.update_json.side_effect = update_json
    return ds


@pytest.fixture
def context(data_store):
    cm = CheckpointManager(data_store, max_versions=5)
    tm = SimpleNamespace(checkpoint_manager=cm)
    aam = SimpleNamespace(task_manager=tm)
    automation = SimpleNamespace(autonomous_assistant_manager=aam)
    return SimpleNamespace(automation=automation, data_store=data_store, ai_client=object())


@pytest.fixture
def patch_butler(monkeypatch):
    import assistant.butler_agent as ba
    monkeypatch.setattr(ba, "get_butler_agent", lambda ctx: ctx.__dict__.get("_fake_agent"))


async def _collect(agen):
    out = []
    async for c in agen:
        out.append(c)
    return out


# ── 挂起：pending_confirmation → checkpoint + 事件 ────────


async def test_suspend_saves_checkpoint_and_emits_event(context, patch_butler, monkeypatch):
    from backend.handlers.chat_paths.pydantic_agent_path import run_pydantic_agent_path

    pending = '{"pending_confirmation": true, "confirmation_id": "abc", "message": "需要确认"}'
    agent = FakeAgent([FakeToolResultEvent(pending, "shell.run")], _messages(), requests=2)
    context._fake_agent = agent

    events = []
    handler = lambda ev: events.append(ev)  # noqa: E731
    from core.events import event_bus
    event_bus.on("task.waiting_approval", handler)
    try:
        chunks = await _collect(run_pydantic_agent_path(
            context, "删除桌面空文件", {"session_id": "sess-1"}, [], None,
        ))
    finally:
        event_bus.off("task.waiting_approval", handler)

    # 挂起 → 不产出最终文本 chunk
    assert chunks == []

    # 事件已发
    assert any(ev.payload.get("confirmation_id") == "abc" for ev in events), "task.waiting_approval 未发出"

    # checkpoint 已保存
    from assistant.butler_checkpoint import load_butler_checkpoint
    cp = load_butler_checkpoint(context, "sess-1")
    assert cp is not None
    cs = cp["context_snapshot"]
    assert cs["task_type"] == "pydantic_agent"
    assert cs["confirmations"][0]["id"] == "abc"
    assert cs["remaining_requests"] == 12 - 2  # MAX 12 - 已用 2


async def test_suspend_without_checkpoint_manager_still_emits_event(context, patch_butler, monkeypatch):
    from backend.handlers.chat_paths.pydantic_agent_path import run_pydantic_agent_path

    ctx = SimpleNamespace(automation=None, data_store=None, ai_client=object())
    ctx._fake_agent = FakeAgent(
        [FakeToolResultEvent('{"pending_confirmation": true, "confirmation_id": "x"}')],
        _messages(), requests=1,
    )
    events = []
    from core.events import event_bus
    handler = lambda ev: events.append(ev)  # noqa: E731
    event_bus.on("task.waiting_approval", handler)
    try:
        chunks = await _collect(run_pydantic_agent_path(ctx, "m", {"session_id": "s"}, [], None))
    finally:
        event_bus.off("task.waiting_approval", handler)

    assert chunks == []
    assert any(ev.payload.get("checkpoint_saved") is False for ev in events)


# ── 恢复：chat.resume ─────────────────────────────────


async def test_resume_restores_history_and_remaining_budget(context, patch_butler, monkeypatch):
    from backend.handlers.chat_paths.pydantic_agent_path import run_pydantic_agent_resume
    from assistant.butler_checkpoint import record_confirmation_resolution, save_butler_checkpoint

    save_butler_checkpoint(
        context,
        task_id="chat-sess-1",
        session_id="sess-1",
        messages=_messages(),
        remaining_requests=10,
        confirmations=[{"id": "abc", "tool": "file.delete", "status": None}],
        title="删除桌面空文件",
    )
    record_confirmation_resolution(
        context, "abc", approved=True, tool="file.delete",
        result={"success": True, "deleted": True},
    )

    # 恢复用的 agent：第一轮产出文本
    from pydantic_ai.messages import PartDeltaEvent, TextPartDelta
    agent = FakeAgent([], _messages(), requests=2)
    context._fake_agent = agent

    chunks = await _collect(run_pydantic_agent_resume(
        context, {"session_id": "sess-1"}, None,
    ))

    # 只发起一次恢复运行（挂起后再挂起则两次；本测试无二次挂起）
    assert len(agent.calls) == 1
    call = agent.calls[0]
    assert call["user_prompt"] is None                      # 无新消息，从断点继续
    assert len(call["message_history"]) == 3                # 完整历史
    assert call["usage_limits"].request_limit == 10         # 剩余预算续用
    # 已批准结果已注入（pending 占位文本被替换）
    last = call["message_history"][-1].parts[-1]
    assert last.part_kind == "tool-return"
    assert "pending_confirmation" not in last.content
    assert "已确认工具" in last.content
    assert chunks == []


async def test_resume_no_pending_task_sends_status(context, patch_butler):
    from backend.handlers.chat_paths.pydantic_agent_path import run_pydantic_agent_resume

    sent = []
    async def send_chunk(text, chunk_type=None):
        sent.append((text, chunk_type))

    agent = FakeAgent([], [], requests=0)
    context._fake_agent = agent
    await _collect(run_pydantic_agent_resume(context, {"session_id": "no-such"}, send_chunk))

    assert any("chat_resume_status" == ct for _t, ct in sent)
    assert "no_pending_task" in sent[0][0]


async def test_resume_waits_until_all_confirmations_resolved(context, patch_butler):
    from backend.handlers.chat_paths.pydantic_agent_path import run_pydantic_agent_resume
    from assistant.butler_checkpoint import save_butler_checkpoint

    # 两个确认都挂起，只批准一个 → 仍不可恢复
    save_butler_checkpoint(
        context,
        task_id="chat-sess-2",
        session_id="sess-2",
        messages=_messages(),
        remaining_requests=9,
        confirmations=[
            {"id": "a1", "tool": "file.delete", "status": None},
            {"id": "a2", "tool": "shell.run", "status": None},
        ],
        title="t",
    )
    from assistant.butler_checkpoint import record_confirmation_resolution
    record_confirmation_resolution(context, "a1", approved=True, tool="file.delete", result={"ok": True})

    sent = []
    async def send_chunk(text, chunk_type=None):
        sent.append((text, chunk_type))

    agent = FakeAgent([], [], requests=0)
    context._fake_agent = agent
    await _collect(run_pydantic_agent_resume(context, {"session_id": "sess-2"}, send_chunk))

    assert len(agent.calls) == 0  # 未发起恢复
    assert any("confirmation_pending" in t for t, _ct in sent)
