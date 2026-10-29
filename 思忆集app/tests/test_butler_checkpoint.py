"""butler 挂起任务 checkpoint（M2 断点续跑）单元测试。"""
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

from assistant.butler_checkpoint import (
    all_confirmations_resolved,
    apply_pending_injections,
    find_by_confirmation,
    load_butler_checkpoint,
    record_confirmation_resolution,
    save_butler_checkpoint,
)
from assistant.checkpoint import CheckpointManager


@pytest.fixture
def data_store():
    """模拟 data_store（内存存储）。"""
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
    """带 CheckpointManager 的 mock AppContext（经 task_manager 注入）。"""
    cm = CheckpointManager(data_store, max_versions=5)
    tm = SimpleNamespace(checkpoint_manager=cm)
    aam = SimpleNamespace(task_manager=tm)
    automation = SimpleNamespace(autonomous_assistant_manager=aam)
    return SimpleNamespace(automation=automation, data_store=data_store)


def _messages():
    """构造含一条 pending tool-return 的消息历史（2.26 结构：调用在 Response，结果在 Request）。"""
    return [
        ModelRequest(parts=[UserPromptPart(content="删除桌面空文件")]),
        ModelResponse(parts=[
            ToolCallPart(tool_call_id="c1", tool_name="file.delete", args='{"path": "x"}'),
        ]),
        ModelRequest(parts=[
            ToolReturnPart(tool_call_id="c1", tool_name="file.delete", content='{"pending_confirmation": true, "confirmation_id": "abc"}'),
        ]),
    ]


def test_save_and_load_by_session(context):
    saved = save_butler_checkpoint(
        context,
        task_id="chat-123",
        session_id="sess-1",
        messages=_messages(),
        remaining_requests=9,
        confirmations=[{"id": "abc", "tool": "file.delete", "status": None}],
        title="删除桌面空文件",
    )
    assert saved is not None
    assert saved["context_snapshot"]["task_type"] == "pydantic_agent"
    assert saved["context_snapshot"]["remaining_requests"] == 9

    cp = load_butler_checkpoint(context, "sess-1")
    assert cp is not None
    assert cp["task_id"] == "chat-123"

    # 其他会话查不到
    assert load_butler_checkpoint(context, "sess-other") is None


def test_record_confirmation_resolution_and_resume_ready(context):
    save_butler_checkpoint(
        context,
        task_id="chat-456",
        session_id="sess-2",
        messages=_messages(),
        remaining_requests=9,
        confirmations=[{"id": "abc", "tool": "file.delete", "status": None}],
        title="t",
    )
    assert find_by_confirmation(context, "abc") is not None
    assert find_by_confirmation(context, "nope") is None

    # 未处理完 → 不可 resume
    cp = load_butler_checkpoint(context, "sess-2")
    assert all_confirmations_resolved(cp) is False

    # 批准 → 记录结果
    record_confirmation_resolution(
        context, "abc", approved=True, tool="file.delete",
        result={"success": True, "deleted": True},
    )
    cp = load_butler_checkpoint(context, "sess-2")
    assert all_confirmations_resolved(cp) is True
    cs = cp["context_snapshot"]
    assert cs["confirmations"][0]["status"] == "approved"
    assert cs["pending_injections"][0]["result"]["deleted"] is True


def test_record_rejection_also_resolves(context):
    save_butler_checkpoint(
        context,
        task_id="chat-789",
        session_id="sess-3",
        messages=_messages(),
        remaining_requests=10,
        confirmations=[{"id": "abc", "tool": "file.delete", "status": None}],
        title="t",
    )
    record_confirmation_resolution(
        context, "abc", approved=False, tool="file.delete",
        result={"rejected": True},
    )
    cp = load_butler_checkpoint(context, "sess-3")
    assert all_confirmations_resolved(cp) is True
    assert cp["context_snapshot"]["pending_injections"][0]["status"] == "rejected"


def test_apply_pending_injections_replaces_tool_return(context):
    save_butler_checkpoint(
        context,
        task_id="chat-101",
        session_id="sess-4",
        messages=_messages(),
        remaining_requests=8,
        confirmations=[{"id": "abc", "tool": "file.delete", "status": "approved"}],
        title="t",
        pending_injections=[{
            "tool": "file.delete", "status": "approved",
            "result": {"success": True, "path": "x", "deleted": True},
        }],
    )
    cp = load_butler_checkpoint(context, "sess-4")
    from assistant.butler_checkpoint import deserialize_messages
    history = deserialize_messages(cp["context_snapshot"]["message_history_b64"])
    assert '"pending_confirmation"' in history[-1].parts[-1].content

    history = apply_pending_injections(history, cp)
    last = history[-1].parts[-1]
    assert last.part_kind == "tool-return"
    assert '"pending_confirmation"' not in last.content
    assert "已确认工具 file.delete" in last.content
    assert "deleted" in last.content


def test_no_checkpoint_manager_degrades_gracefully():
    ctx = SimpleNamespace(automation=None, data_store=None)
    saved = save_butler_checkpoint(
        ctx, task_id="t", session_id="s", messages=[],
        remaining_requests=1, confirmations=[],
    )
    assert saved is None
    assert load_butler_checkpoint(ctx, "s") is None
