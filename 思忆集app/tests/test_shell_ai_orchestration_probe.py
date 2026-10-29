# -*- coding: utf-8 -*-
"""B-P0 壳内管家「编排」基线探针（进程内脚本化模型，离线确定性）。

目的：量化当前 butler 在高阶编排三个维度的**现状**，作为 B 工作线的基线（也作回归护栏）：
1. 深链：连续 ≥3 个工具调用能否迭代完成；
2. 失败自恢复：某工具返回 error 后，循环是否把错误回喂、允许模型重试（自我纠错的底层条件）；
3. 优雅终止：工具失败且模型不再重试时，是否正常收尾（不崩溃/不假成功/不无限循环）。

结论只反映「循环机制」，不反映真模型的规划质量（那需真模型 eval）。
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


class ScriptedRouter:
    """action 可在 fail_once 时首次返回 error（模拟工具失败），随后成功。"""

    def __init__(self, fail_once: set[str] | None = None) -> None:
        self.calls: list[tuple[str, dict]] = []
        self._failed = set()
        self._fail_once = fail_once or set()

    async def execute_direct(self, action_id: str, args: dict[str, Any], approved=False):
        self.calls.append((action_id, dict(args)))
        if action_id in self._fail_once and action_id not in self._failed:
            self._failed.add(action_id)
            return {"error": f"{action_id} 暂时失败", "retryable": True}
        return {"ok": True, "action": action_id, "result": "data"}


def _tool(call_id, name, args):
    return {"choices": [{"message": {"content": "", "tool_calls": [
        {"id": call_id, "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}]}}], "model": "s"}


def _text(c):
    return {"choices": [{"message": {"content": c, "tool_calls": None}}], "model": "s"}


class Script:
    def __init__(self, steps):
        self.steps = steps  # list of ("tool", id, name, args) | ("text", content)
        self.calls = []

    def chat_completion(self, messages, **kwargs):
        self.calls.append((list(messages), dict(kwargs)))
        i = len(self.calls) - 1
        step = self.steps[i] if i < len(self.steps) else ("text", "完成")
        if step[0] == "tool":
            return _tool(step[1], step[2], step[3])
        return _text(step[1])


def _mk(script, router):
    ctx = _Ctx()
    ctx.ai_client = script
    ctx.capability_router = router
    ctx.mcp_client_manager = None
    return ctx


async def test_deep_chain_three_tool_calls():
    script = Script([
        ("tool", "a", "notes_list", {"limit": 3}),
        ("tool", "b", "tasks_list", {"limit": 3}),
        ("tool", "c", "system_info", {}),
        ("text", "已汇总：笔记/待办/系统信息都查到了"),
    ])
    router = ScriptedRouter()
    ctx = _mk(script, router)
    out = await get_butler_agent(ctx).run("汇总笔记待办和系统信息", deps=ctx)
    assert [c[0] for c in router.calls] == ["notes.list", "tasks.list", "system.info"]
    assert len(script.calls) == 4  # 3 工具轮 + 1 汇总轮
    assert "汇总" in out.output or "查到了" in out.output


async def test_tool_failure_then_model_retry():
    script = Script([
        ("tool", "a", "notes_list", {"limit": 3}),          # 第一次工具失败
        ("tool", "b", "notes_list", {"limit": 3}),          # 模型看到 error 后重试
        ("text", "重试后拿到了笔记"),
    ])
    router = ScriptedRouter(fail_once={"notes.list"})
    ctx = _mk(script, router)
    await get_butler_agent(ctx).run("列出笔记", deps=ctx)
    notes_calls = [c for c in router.calls if c[0] == "notes.list"]
    assert len(notes_calls) == 2, f"期望重试导致 2 次 notes.list，实得 {router.calls}"
    # 第二轮请求里必须含第一轮的 role=tool error 反馈（错误被回喂=自我纠错的底层条件）
    round2 = script.calls[1][0]
    tool_err = [m for m in round2 if m.get("role") == "tool"]
    assert tool_err and ("error" in json.dumps(tool_err, ensure_ascii=False))


async def test_graceful_termination_no_fake_success():
    """工具失败且模型不再重试：应正常产出（承认可失败），不抛异常、不假成功、不死循环。"""
    script = Script([
        ("tool", "a", "notes_list", {"limit": 3}),   # 失败
        ("text", "笔记查询暂时失败了，稍后再试"),      # 模型不重试，直接说明
    ])
    router = ScriptedRouter(fail_once={"notes.list"})
    ctx = _mk(script, router)
    out = await get_butler_agent(ctx).run("列出笔记", deps=ctx)
    assert isinstance(out.output, str) and out.output.strip()
    assert "失败" in out.output


async def test_update_plan_tool_offered_and_runs():
    """B-P1a.2：update_plan 计划工具被提供给模型、能被调用、走的是内置工具而非 catalog 路由。"""
    script = Script([
        ("tool", "p1", "update_plan", {"goal": "整理笔记", "steps": [
            {"title": "查笔记", "status": "running"}, {"title": "写摘要", "status": "pending"}]}),
        ("text", "已按计划完成"),
    ])
    router = ScriptedRouter()
    ctx = _mk(script, router)
    out = await get_butler_agent(ctx).run("帮我整理笔记并写摘要", deps=ctx)

    # 首轮模型可见工具集里应包含 update_plan
    offered = {t["function"]["name"] for t in script.calls[0][1].get("tools", [])}
    assert "update_plan" in offered
    # update_plan 是内置工具，不经 capability_router（不污染 catalog 动作执行）
    assert router.calls == []
    assert out.output == "已按计划完成"


def test_plan_incompletion_note_gate():
    """B-P1b v1 纯函数：仅“用过计划且未完成”才给如实提示，其余一律 None（默认路径零影响）。"""
    from backend.handlers.chat_paths.pydantic_agent_path import _plan_incompletion_note

    assert _plan_incompletion_note(None) is None
    assert _plan_incompletion_note({}) is None
    assert _plan_incompletion_note({"total": 0, "done": 0}) is None
    assert _plan_incompletion_note({"total": 3, "done": 3}) is None  # 全部完成
    note = _plan_incompletion_note({"total": 3, "done": 1, "next": "写摘要"})
    assert note and "计划未完成" in note and "1/3" in note and "写摘要" in note
