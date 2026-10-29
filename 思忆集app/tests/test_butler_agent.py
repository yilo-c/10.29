# -*- coding: utf-8 -*-
"""PydanticAI 管家执行引擎测试（P3 收敛）。

覆盖：
1. payload_schema 自由文本 → 类型/必填/描述 解析
2. 动态参数 Pydantic 模型生成
3. 工具名合法化
4. CONFIRM 分级拦截 / execute_direct 异常转结构化错误
5. 完整 agent 循环（脚本化 ai_client）：tools 携带、role=tool 回喂、工具执行
6. pydantic_agent_path 端到端：tool_progress chunk + 流式文本产出
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import os

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import services.confirmation_store as _cs_mod

from assistant.butler_agent import (  # noqa: E402
    build_args_model,
    execute_tool_action,
    get_butler_agent,
    parse_payload_field,
    sanitize_tool_name,
)


# ── 1/2/3 纯函数 ─────────────────────────────────────


@pytest.fixture(autouse=True)
def _clear_agent_cache():
    """避免测试间 context id 复用导致 agent 缓存串台。"""
    from assistant import butler_agent

    butler_agent._agent_cache.clear()
    yield
    butler_agent._agent_cache.clear()


def test_parse_payload_field_required_string():
    py_type, required, desc = parse_payload_field("string (必填) 笔记标题")
    assert py_type is str and required is True and desc == "笔记标题"


def test_parse_payload_field_optional_number():
    py_type, required, _ = parse_payload_field("number (可选, 默认 20) 返回数量上限")
    assert py_type is int and required is False


def test_parse_payload_field_compound_type_falls_back_to_str():
    py_type, required, _ = parse_payload_field("number/string (必填) 笔记 ID")
    assert py_type is str and required is True


def test_parse_payload_field_empty_spec():
    py_type, required, _ = parse_payload_field("")
    assert py_type is str and required is False


def test_build_args_model_required_and_optional():
    model = build_args_model(
        "notes.create",
        {"title": "string (必填) 标题", "limit": "number (可选) 数量"},
    )
    inst = model(title="x")
    assert inst.title == "x" and inst.limit is None
    with pytest.raises(Exception):
        model()  # title 必填


def test_build_args_model_no_params_returns_none():
    assert build_args_model("system.info", {}) is None


def test_sanitize_tool_name():
    assert sanitize_tool_name("notes.list") == "notes_list"
    name = sanitize_tool_name("mcp.my-server.do.thing")
    assert all(c.isalnum() or c in "_-" for c in name)


# ── 4 分级拦截与 execute_direct ──────────────────────────────


class _Ctx:
    pass


@pytest.fixture(autouse=True)
def _isolate_confirmation_store(tmp_path):
    os.environ["SIYI_DATA_DIR"] = str(tmp_path)
    _cs_mod._store_singleton = None
    _cs_mod.get_confirmation_store()
    from assistant import butler_agent
    butler_agent._pending_contexts.clear()
    yield
    _cs_mod._store_singleton = None


async def test_confirm_tool_requests_confirmation():
    """P3 升级：CONFIRM 集不再直接 blocked，改为发起用户确认请求。"""
    ctx = _Ctx()
    out = await execute_tool_action(ctx, "shell.run", {"command": "dir"})
    data = json.loads(out)
    assert data["pending_confirmation"] is True
    assert data["confirmation_id"]
    # 清理待确认队列，避免串测
    from assistant import butler_agent
    butler_agent.get_confirmation_store().clear()


async def test_tool_action_via_aam_router():
    """真实挂载点：context 无 capability_router，router 在
    autonomous_assistant_manager.router 上时仍应能执行工具。"""

    class AamCtx:
        pass

    class FakeManager:
        pass

    class Router:
        def __init__(self):
            self.calls = []

        async def execute_direct(self, action_id, args, approved=False):
            self.calls.append((action_id, dict(args or {})))
            return {"ok": True}

    ctx = AamCtx()
    manager = FakeManager()
    manager.router = Router()
    ctx.autonomous_assistant_manager = manager  # 无 capability_router 属性

    text = await execute_tool_action(ctx, "notes.list", {"limit": 3})
    data = json.loads(text)
    assert data.get("ok") is True
    assert manager.router.calls == [("notes.list", {"limit": 3})]


async def test_tool_action_without_router_returns_error():
    ctx = _Ctx()
    out = await execute_tool_action(ctx, "notes.list", {})
    data = json.loads(out)
    assert "error" in data


async def test_capability_router_execute_direct_structured_error():
    from assistant.capability_router import AssistantCapabilityRouter

    router = AssistantCapabilityRouter.__new__(AssistantCapabilityRouter)

    def _boom(step):
        raise RuntimeError("kaput")

    router.execute = _boom  # type: ignore[method-assign]
    result = await router.execute_direct("notes.list", {"limit": 1})
    assert result["success"] is False and "kaput" in result["error"]
    assert result["action_id"] == "notes.list"


async def test_capability_router_execute_direct_success():
    from assistant.capability_router import AssistantCapabilityRouter

    router = AssistantCapabilityRouter.__new__(AssistantCapabilityRouter)
    captured = {}

    def _ok(step):
        captured["action_id"] = step.action_id
        captured["payload"] = dict(step.payload or {})
        return {"ok": True}

    router.execute = _ok  # type: ignore[method-assign]
    result = await router.execute_direct("tasks.list", {"limit": 3})
    assert result == {"ok": True}
    assert captured == {"action_id": "tasks.list", "payload": {"limit": 3}}


# ── 5 完整 agent 循环（脚本化 ai_client）─────────────────────


class FakeRouter:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    async def execute_direct(self, action_id: str, args: dict[str, Any], approved=False):
        self.calls.append((action_id, dict(args)))
        return {"ok": True, "action": action_id, "args": args}


class ScriptedClient:
    """第一轮返回 tool_call，第二轮返回最终文本。"""

    def __init__(self) -> None:
        self.calls: list[tuple[list, dict]] = []

    def chat_completion(self, messages, **kwargs):
        self.calls.append((list(messages), dict(kwargs)))
        if len(self.calls) == 1:
            return {
                "choices": [{"message": {"content": "", "tool_calls": [
                    {"id": "call_1", "type": "function",
                     "function": {"name": "notes_list", "arguments": '{"limit": 5}'}}]}}],
                "model": "scripted",
            }
        return {
            "choices": [{"message": {"content": "已为你列出 5 条笔记", "tool_calls": None}}],
            "model": "scripted",
        }


def _make_ctx(client=None, router=None):
    ctx = _Ctx()
    ctx.ai_client = client or ScriptedClient()
    ctx.capability_router = router or FakeRouter()
    ctx.mcp_client_manager = None
    return ctx


async def test_agent_loop_tool_call_then_final_text():
    ctx = _make_ctx()
    agent = get_butler_agent(ctx)
    assert agent is not None

    result = await agent.run("列出我的笔记", deps=ctx)
    assert result.output == "已为你列出 5 条笔记"

    client = ctx.ai_client
    assert len(client.calls) == 2

    # 第一次请求必须携带 tools 定义（永远带工具，无关键词门槛）
    first_kwargs = client.calls[0][1]
    tool_names = {t["function"]["name"] for t in first_kwargs.get("tools", [])}
    assert "notes_list" in tool_names and "tasks_create" in tool_names
    assert first_kwargs.get("tool_choice") == "auto"

    # 第二次请求必须含标准 role=tool 回喂（修复旧 system 消息回填 bug）
    second_msgs = client.calls[1][0]
    tool_msgs = [m for m in second_msgs if m.get("role") == "tool"]
    assert tool_msgs, f"missing role=tool messages: {[m.get('role') for m in second_msgs]}"
    assert tool_msgs[0]["tool_call_id"] == "call_1"
    assert '"ok": true' in tool_msgs[0]["content"]

    # 工具真实执行（number→int 映射后 payload 合法）
    assert ctx.capability_router.calls == [("notes.list", {"limit": 5})]


async def test_agent_plain_chat_no_tool_call():
    class TextOnlyClient:
        def chat_completion(self, messages, **kwargs):
            assert kwargs.get("tools"), "纯聊天也必须携带工具定义"
            return {"choices": [{"message": {"content": "你好呀"}}], "model": "scripted"}

    ctx = _make_ctx(client=TextOnlyClient())
    agent = get_butler_agent(ctx)
    result = await agent.run("你好", deps=ctx)
    assert result.output == "你好呀"
    assert ctx.capability_router.calls == []


# ── 6 pydantic_agent_path 端到端 ─────────────────────────────


async def test_pydantic_agent_path_streams_chunks_and_progress():
    from backend.handlers.chat_paths.pydantic_agent_path import run_pydantic_agent_path

    ctx = _make_ctx()
    chunks: list[tuple[str, str]] = []

    async def send_chunk(text, chunk_type="chunk"):
        chunks.append((chunk_type, text))

    msgs = [
        {"role": "system", "content": "历史 system 上下文"},
        {"role": "user", "content": "列出我的笔记"},
    ]
    collected = []
    async for piece in run_pydantic_agent_path(ctx, "列出我的笔记", {}, msgs, send_chunk):
        collected.append(piece)

    assert "".join(collected) == "已为你列出 5 条笔记"

    progress = [json.loads(t) for ct, t in chunks if ct == "tool_progress"]
    assert any(p.get("status") == "running" and p.get("tool") == "notes_list" for p in progress)
    assert any(p.get("status") == "done" for p in progress)

    text_chunks = [t for ct, t in chunks if ct == "chunk"]
    assert "".join(text_chunks) == "已为你列出 5 条笔记"


async def test_pydantic_agent_path_silent_when_no_ai_client():
    from backend.handlers.chat_paths.pydantic_agent_path import run_pydantic_agent_path

    ctx = _Ctx()  # 无 ai_client
    ctx.capability_router = FakeRouter()
    ctx.mcp_client_manager = None

    async def send_chunk(text, chunk_type="chunk"):
        raise AssertionError("无 ai_client 时不应产出任何 chunk")

    out = []
    async for piece in run_pydantic_agent_path(ctx, "你好", {}, [], send_chunk):
        out.append(piece)
    assert out == []
