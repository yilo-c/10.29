"""evolution.* RPC handler 单元测试 — 不触网，直接 mock orchestrator/sandbox。"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from backend.handlers._evolution import register_evolution
from evolution.orchestrator import (
    EvolutionOrchestrator,
)
from evolution.sandbox import EvolutionSandbox
from evolution.tech_radar import LEVEL_CONFIG, RadarFinding, TechRadar


class _FakeRegistry:
    def __init__(self):
        self.handlers: dict[str, object] = {}

    def register(self, name, fn):
        self.handlers[name] = fn

    def register_with_schema(self, name, fn, schema=None, **kwargs):
        # U2/U3：evolution.scan_and_report.status 等改用 register_with_schema，
        # 桩必须同样支持（schema 在本测试不校验参数，仅需登记 handler）。
        self.handlers[name] = fn


class _FakeRadar(TechRadar):
    def __init__(self, findings):
        super().__init__()
        self._fake = findings

    def scan(self):
        return list(self._fake)

    def get_all_findings(self):
        return list(self._fake)


def _make_finding(name="chromadb"):
    return RadarFinding(
        name=name,
        current_version="0.4.0",
        latest_version="0.6.0",
        source="pypi",
        category="dependency",
        level=LEVEL_CONFIG,
    )


def _make_context_with_orch(orch):
    ctx = MagicMock()
    ctx.evolution_orchestrator = orch
    # S3-2 回退（2026-09-16 架构复审 audit §1）：scan_and_report 入口已恢复同步
    # 直调真实现，不再读 task_queue。此处显式置 None 仅作防御（万一将来重新
    # 启用队列路径，MagicMock 的 task_queue 属性会返回 mock 而非 None → 误入队
    # 分支、破坏本组「scan 后立刻 last_briefing/get_result」的同步契约）。
    ctx.task_queue = None
    return ctx


def _make_context_without_orch():
    class _Ctx:
        pass

    ctx = _Ctx()
    ctx.evolution_orchestrator = None
    return ctx


@pytest.fixture
def registry_with_orch():
    finding = _make_finding()
    radar = _FakeRadar([finding])
    orch = EvolutionOrchestrator(tech_radar=radar, sandbox=EvolutionSandbox())
    ctx = _make_context_with_orch(orch)
    reg = _FakeRegistry()
    register_evolution(reg, ctx)
    return reg, orch


@pytest.fixture
def registry_without_orch():
    ctx = _make_context_without_orch()
    reg = _FakeRegistry()
    register_evolution(reg, ctx)
    return reg


@pytest.mark.asyncio
async def test_all_methods_registered(registry_with_orch):
    reg, _ = registry_with_orch
    expected = {
        "evolution.scan_and_report",
        "evolution.last_briefing",
        "evolution.confirm_upgrade",
        "evolution.final_apply",
        "evolution.skip_upgrade",
        "evolution.list_results",
        "evolution.get_result",
        "evolution.status",
        "evolution.is_redline",
        "evolution.sandbox_recent",
    }
    assert expected.issubset(reg.handlers.keys())


@pytest.mark.asyncio
async def test_scan_and_report(registry_with_orch):
    reg, _ = registry_with_orch
    result = await reg.handlers["evolution.scan_and_report"]()
    assert "items" in result
    assert "generated_at" in result


@pytest.mark.asyncio
async def test_last_briefing_after_scan(registry_with_orch):
    reg, _ = registry_with_orch
    await reg.handlers["evolution.scan_and_report"]()
    result = await reg.handlers["evolution.last_briefing"]()
    assert result.get("briefing") is not None


@pytest.mark.asyncio
async def test_confirm_upgrade_missing_name(registry_with_orch):
    reg, _ = registry_with_orch
    result = await reg.handlers["evolution.confirm_upgrade"]()
    assert "error" in result


@pytest.mark.asyncio
async def test_confirm_upgrade_unknown_item(registry_with_orch):
    reg, _ = registry_with_orch
    result = await reg.handlers["evolution.confirm_upgrade"](name="nonexistent_pkg")
    assert "error" in result


@pytest.mark.asyncio
async def test_confirm_upgrade_full_flow(registry_with_orch):
    reg, _ = registry_with_orch
    # 先扫描产生结果
    await reg.handlers["evolution.scan_and_report"]()
    result = await reg.handlers["evolution.confirm_upgrade"](name="chromadb")
    assert result.get("name") == "chromadb"
    # confirm 之后应进入 sandbox/ready 之一（沙箱必然跑通，因为 chromadb 已装）
    assert result.get("status") in ("ready", "failed", "sandbox")


@pytest.mark.asyncio
async def test_skip_upgrade(registry_with_orch):
    reg, _ = registry_with_orch
    await reg.handlers["evolution.scan_and_report"]()
    result = await reg.handlers["evolution.skip_upgrade"](name="chromadb")
    assert result.get("status") == "skipped"


@pytest.mark.asyncio
async def test_status_snapshot(registry_with_orch):
    reg, _ = registry_with_orch
    result = await reg.handlers["evolution.status"]()
    assert result.get("available") is True
    assert "sandbox" in result


@pytest.mark.asyncio
async def test_is_redline_match(registry_with_orch):
    reg, _ = registry_with_orch
    result = await reg.handlers["evolution.is_redline"](name="action_guard")
    assert result.get("redline") is True


@pytest.mark.asyncio
async def test_is_redline_no_match(registry_with_orch):
    reg, _ = registry_with_orch
    result = await reg.handlers["evolution.is_redline"](name="chromadb")
    assert result.get("redline") is False


@pytest.mark.asyncio
async def test_list_results_empty(registry_with_orch):
    reg, _ = registry_with_orch
    result = await reg.handlers["evolution.list_results"]()
    assert isinstance(result, list)


@pytest.mark.asyncio
async def test_sandbox_recent_empty(registry_with_orch):
    reg, _ = registry_with_orch
    result = await reg.handlers["evolution.sandbox_recent"]()
    assert isinstance(result, list)


@pytest.mark.asyncio
async def test_handlers_unavailable_without_orchestrator(registry_without_orch):
    reg = registry_without_orch
    # scan
    r = await reg.handlers["evolution.scan_and_report"]()
    assert "error" in r
    # status
    r = await reg.handlers["evolution.status"]()
    assert r.get("available") is False
    # list_results returns empty list
    r = await reg.handlers["evolution.list_results"]()
    assert r == []
    # is_redline without orchestrator returns False
    r = await reg.handlers["evolution.is_redline"](name="foo")
    assert r.get("redline") is False


@pytest.mark.asyncio
async def test_final_apply_wrong_state(registry_with_orch):
    """未经过 confirm 直接 final_apply 应返回 failed/error。"""
    reg, _ = registry_with_orch
    await reg.handlers["evolution.scan_and_report"]()
    result = await reg.handlers["evolution.final_apply"](name="chromadb")
    # 此时状态为 reported，不是 ready
    assert result.get("status") in ("failed",)


@pytest.mark.asyncio
async def test_get_result(registry_with_orch):
    reg, _ = registry_with_orch
    await reg.handlers["evolution.scan_and_report"]()
    result = await reg.handlers["evolution.get_result"](name="chromadb")
    assert result.get("name") == "chromadb"
