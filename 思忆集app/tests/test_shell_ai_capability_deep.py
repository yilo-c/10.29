# -*- coding: utf-8 -*-
"""壳内 AI 能力深度测试（butler / pydantic-ai 单循环，脚本化 ai_client，离线确定性）

DSH 内核已冻结，本文件专测「壳内管家」这条缺省路径的能力上限，补齐现有簇未覆盖的两点：
1. 多步工具链：模型在同一轮对话里**连续调用两个工具**（notes.list → tasks.list → 汇总文本），
   验证 agent 循环正确迭代、role=tool 逐轮回喂、参数经 build_args_model 归一（number→int）。
2. 审批 pending 信封如实回喂：工具返回 R4/CONFIRM 的 {pending:True,...} 时，
   butler 必须把它**原样喂回模型**（受控执行铁律），而非伪造成成功。
判据全部走进程内假模型，不依赖真实 LLM / 网络 / DSH 内核。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from assistant.butler_agent import get_butler_agent  # noqa: E402


@pytest.fixture(autouse=True)
def _clear_agent_cache():
    from assistant import butler_agent

    butler_agent._agent_cache.clear()
    yield
    butler_agent._agent_cache.clear()


class _Ctx:
    pass


class RecordingRouter:
    def __init__(self, responses: dict[str, dict] | None = None) -> None:
        self.calls: list[tuple[str, dict]] = []
        self._responses = responses or {}

    async def execute_direct(self, action_id: str, args: dict[str, Any], approved=False):
        self.calls.append((action_id, dict(args)))
        if action_id in self._responses:
            return self._responses[action_id]
        return {"ok": True, "action": action_id}


def _tool_call(call_id: str, name: str, arguments: str):
    return {
        "choices": [{"message": {"content": "", "tool_calls": [
            {"id": call_id, "type": "function", "function": {"name": name, "arguments": arguments}}]}}],
        "model": "scripted",
    }


def _text(content: str):
    return {"choices": [{"message": {"content": content, "tool_calls": None}}], "model": "scripted"}


class MultiStepClient:
    """第 1 轮 notes_list，第 2 轮 tasks_list，第 3 轮汇总文本。"""

    def __init__(self) -> None:
        self.calls: list[tuple[list, dict]] = []

    def chat_completion(self, messages, **kwargs):
        self.calls.append((list(messages), dict(kwargs)))
        n = len(self.calls)
        if n == 1:
            return _tool_call("c1", "notes_list", '{"limit": 5}')
        if n == 2:
            return _tool_call("c2", "tasks_list", '{"limit": 3}')
        return _text("查完了：5 条笔记、3 个待办")


async def test_shell_ai_multi_step_tool_chain():
    ctx = _Ctx()
    client = MultiStepClient()
    router = RecordingRouter()
    ctx.ai_client = client
    ctx.capability_router = router
    ctx.mcp_client_manager = None

    agent = get_butler_agent(ctx)
    result = await agent.run("看看我的笔记和待办", deps=ctx)

    # 两个工具都被真实按序执行，参数 number→int 归一
    assert router.calls == [("notes.list", {"limit": 5}), ("tasks.list", {"limit": 3})]
    # 循环迭代 3 次（两工具轮 + 汇总轮）
    assert len(client.calls) == 3
    # 第 2、3 次请求里都必须带上前一轮的 role=tool 结果
    round2_tool_msgs = [m for m in client.calls[1][0] if m.get("role") == "tool"]
    round3_tool_msgs = [m for m in client.calls[2][0] if m.get("role") == "tool"]
    assert any(m.get("tool_call_id") == "c1" for m in round2_tool_msgs)
    assert {m.get("tool_call_id") for m in round3_tool_msgs} >= {"c1", "c2"}
    # 最终答案由模型汇总产出
    assert result.output == "查完了：5 条笔记、3 个待办"


async def test_shell_ai_relays_pending_approval_envelope():
    """R4/CONFIRM 工具返回 pending 信封时，butler 必须原样回喂模型（不伪造 ok）。"""
    ctx = _Ctx()
    client = MultiStepClient()
    pending = {"pending": True, "requires_approval": True, "pending_id": "p-42", "action_id": "notes.list"}
    router = RecordingRouter(responses={"notes.list": pending})
    ctx.ai_client = client
    ctx.capability_router = router
    ctx.mcp_client_manager = None

    agent = get_butler_agent(ctx)
    await agent.run("看看我的笔记和待办", deps=ctx)

    # 第二轮喂回的 role=tool 内容里应含 pending 信封（受控执行铁律：审批未过不得伪装成功）
    round2_tool_msgs = [m for m in client.calls[1][0] if m.get("role") == "tool"]
    assert round2_tool_msgs, "第二轮应带回喂的 tool 结果"
    fed = round2_tool_msgs[0]["content"]
    assert json.loads(fed).get("pending") is True, f"pending 信封未如实回喂：{fed}"
