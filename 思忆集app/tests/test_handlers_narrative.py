"""narrative handlers 行为测试 — 叙事 RPC + xiaowen.quote（T7.3 验证 + T4.1 补覆盖）。

覆盖：memory.narrative.latest / types / generate_monthly、agent.state.summary、
xiaowen.quote 各 action 分支、注册齐全。
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from backend.handlers._narrative import register_narrative


class _Registry:
    def __init__(self):
        self.registered: dict[str, object] = {}
        self.schemas: dict[str, object] = {}

    def register(self, name: str, fn) -> None:
        self.registered[name] = fn

    def register_with_schema(self, name: str, fn, schema=None, **kw) -> None:
        # S2 批次1 契约同步（2026-09-16）：memory.narrative.* 已升级为带 schema
        # 注册（register_with_schema），桩须支持同一调用面，schema 顺带留档。
        self.registered[name] = fn
        self.schemas[name] = schema


class _FakeDataStore:
    def __init__(self, base_dir: str = "/tmp"):
        self.base_dir = base_dir
        self._store: dict = {}

    def save_json(self, key, data):
        self._store[key] = data

    def load_json(self, key, default=None):
        return self._store.get(key, default)


class _FakeAgentStateManager:
    """模拟 AgentStateManager（构造含文件 I/O，测试中 mock 掉）。"""

    def __init__(self, data_dir=None):
        self.state = SimpleNamespace(memory_density=42, unresolved=[])

    def get_summary(self):
        return {"completeness": 0.8, "alignment": 0.9, "memory_density": 42}

    def get_self_context(self):
        return "我是小问，一名华山剑客风格的 AI 助手。"

    def should_remind(self):
        return False


def _make_context():
    return SimpleNamespace(
        local_data_store=_FakeDataStore(),
        personal_inbox_repository=SimpleNamespace(entries=[], add_entry=lambda e: e and len(e) or 1),
    )


def _make_ctx_with_mock_agent(reg=None, context=None):
    reg = reg or _Registry()
    ctx = context or _make_context()
    # 注意：register_narrative 的闭包在【调用时】才 `from services.agent_state import`，
    # 因此 mock 必须包裹调用，而非注册。
    register_narrative(reg, ctx)
    return reg, ctx


def _patch_agent():
    return patch("services.agent_state.AgentStateManager", new=lambda *a, **k: _FakeAgentStateManager())


# ── memory.narrative.types ──


def test_narrative_types():
    reg, _ = _make_ctx_with_mock_agent()
    result = asyncio.run(reg.registered["memory.narrative.types"]())
    assert result == ["weekly", "milestone", "emotion", "monthly"]


# ── memory.narrative.generate_monthly ──


def test_generate_monthly_registered_and_failure_graceful():
    """generate_monthly 无数据时应返回 success=False 而非崩溃（T7.1）。"""
    reg, _ = _make_ctx_with_mock_agent()
    with patch("backend.narrative_scheduler.generate_monthly_now", new=AsyncMock(return_value=None)):
        result = asyncio.run(reg.registered["memory.narrative.generate_monthly"]())
    assert result["success"] is False
    assert "error" in result


def test_generate_monthly_success():
    reg, _ = _make_ctx_with_mock_agent()
    with patch(
        "backend.narrative_scheduler.generate_monthly_now",
        new=AsyncMock(return_value={"narrative_id": "nar_1", "title": "2026-08 · 你的一月回顾", "month_label": "2026-08"}),
    ):
        result = asyncio.run(reg.registered["memory.narrative.generate_monthly"]())
    assert result["success"] is True
    assert result["narrative_id"] == "nar_1"
    assert result["month_label"] == "2026-08"


# ── agent.state.summary ──


def test_agent_state_summary():
    reg, _ = _make_ctx_with_mock_agent()
    with _patch_agent():
        result = asyncio.run(reg.registered["agent.state.summary"]())
    assert result["completeness"] == 0.8
    assert result["memory_density"] == 42


def test_agent_state_summary_failure_fallback():
    """AgentStateManager 抛异常时返回全零兜底（不崩溃）。"""
    reg = _Registry()
    ctx = _make_context()
    register_narrative(reg, ctx)
    with patch("services.agent_state.AgentStateManager", side_effect=RuntimeError("boom")):
        result = asyncio.run(reg.registered["agent.state.summary"]())
    assert result == {"completeness": 0.0, "alignment": 0.0, "memory_density": 0}


# ── xiaowen.quote（T7.3 后端行为）──


@pytest.mark.parametrize("action", ["idle", "happy", "thinking", "alert", "unknown"])
def test_xiaowen_quote_all_actions(action):
    reg, _ = _make_ctx_with_mock_agent()
    with _patch_agent():
        result = asyncio.run(reg.registered["xiaowen.quote"](action=action))
    assert isinstance(result, dict)
    assert "quote" in result
    assert "mood" in result
    # 记忆驱动：idle 应返回非空台词（台词池含 memory_density 变体，随机取）
    if action == "idle":
        assert result["quote"]
        assert result["mood"] == "idle"


def test_xiaowen_quote_no_data_dir():
    """无 data_dir 时返回空兜底（不崩溃）。"""
    reg = _Registry()
    ctx = SimpleNamespace(local_data_store=_FakeDataStore(base_dir=""), personal_inbox_repository=None)
    register_narrative(reg, ctx)
    result = asyncio.run(reg.registered["xiaowen.quote"](action="idle"))
    assert result == {"quote": "", "mood": "neutral"}


# ── 注册齐全 ──


def test_register_all_narrative_methods():
    reg = _Registry()
    ctx = _make_context()
    register_narrative(reg, ctx)
    for name in [
        "memory.narrative.latest",
        "memory.narrative.types",
        "memory.narrative.generate_monthly",
        "agent.state.summary",
        "agent.state.context",
        "xiaowen.quote",
    ]:
        assert name in reg.registered, name
