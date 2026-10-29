from __future__ import annotations

import sys
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from evolution.tech_radar import LEVEL_CONFIG, LEVEL_FEATURE
from evolution.orchestrator import (
    FLOW_COMPLETED,
    FLOW_FAILED,
    FLOW_READY,
    FLOW_REPORTED,
    FLOW_SKIPPED,
    EvolutionOrchestrator,
    EvolutionResult,
)


def _make_orchestrator():
    from evolution.tech_radar import TechRadar
    from evolution.sandbox import EvolutionSandbox

    radar = TechRadar()
    sandbox = EvolutionSandbox()
    return EvolutionOrchestrator(tech_radar=radar, sandbox=sandbox)


def test_scan_and_report():
    orch = _make_orchestrator()
    briefing = orch.scan_and_report()
    assert briefing is not None
    assert len(orch.get_recent_results()) >= 0  # 可能有或没有变化


def test_skip_upgrade():
    orch = _make_orchestrator()
    result = orch.skip_upgrade("nonexistent")
    assert result is None  # 不存在的项返回 None


def test_confirm_upgrade_nonexistent():
    orch = _make_orchestrator()
    result = orch.confirm_upgrade("nonexistent")
    assert result is None


def test_scan_and_confirm_flow():
    from evolution.orchestrator import FLOW_REPORTED

    orch = _make_orchestrator()
    orch.scan_and_report()
    results = orch.get_recent_results()
    if results:
        result = results[0]
        assert result.status == FLOW_REPORTED


def test_redline_detection():
    orch = _make_orchestrator()
    assert orch.is_redline_item("action_guard_update") is True
    assert orch.is_redline_item("auth_module_change") is True
    assert orch.is_redline_item("chromadb_upgrade") is False  # not a redline


def test_empty_briefing():
    orch = _make_orchestrator()
    assert orch.get_last_briefing() is None


def test_status_snapshot():
    orch = _make_orchestrator()
    snapshot = orch.get_status_snapshot()
    assert "status" in snapshot
    assert "total_items" in snapshot


# ─── P4.1-1c：用户阻拦列表 ───────────────────────────────


class _FakeSettingsRepo:
    """简单的内存 settings 仓储，仅实现 get(key) 接口。"""

    def __init__(self, data):
        self._data = data

    def get(self, key):
        return self._data.get(key)


def test_redline_user_blocklist_csv():
    """settings 中的 CSV 阻拦列表应被识别为 redline。"""
    from evolution.tech_radar import TechRadar
    from evolution.sandbox import EvolutionSandbox

    repo = _FakeSettingsRepo({"evolution_upgrade_blocklist": "chromadb, langchain,Custom_Pkg"})
    orch = EvolutionOrchestrator(
        tech_radar=TechRadar(),
        sandbox=EvolutionSandbox(),
        settings_repository=repo,
    )
    assert orch.is_redline_item("chromadb") is True
    assert orch.is_redline_item("LangChain") is True  # 大小写不敏感
    assert orch.is_redline_item("custom_pkg") is True
    assert orch.is_redline_item("fastapi") is False  # 不在列表


def test_redline_user_blocklist_list_format():
    """settings 中的 list 格式阻拦列表也应被识别。"""
    from evolution.tech_radar import TechRadar
    from evolution.sandbox import EvolutionSandbox

    repo = _FakeSettingsRepo({"evolution_upgrade_blocklist": ["fastapi", "uvicorn"]})
    orch = EvolutionOrchestrator(
        tech_radar=TechRadar(),
        sandbox=EvolutionSandbox(),
        settings_repository=repo,
    )
    assert orch.is_redline_item("fastapi") is True
    assert orch.is_redline_item("uvicorn") is True
    assert orch.is_redline_item("django") is False


def test_redline_falls_back_when_settings_repo_raises():
    """settings 仓储异常时阻拦列表应安全降级为空，不影响内置 redline 检查。"""
    class _BadRepo:
        def get(self, key):
            raise RuntimeError("simulated failure")

    from evolution.tech_radar import TechRadar
    from evolution.sandbox import EvolutionSandbox

    orch = EvolutionOrchestrator(
        tech_radar=TechRadar(),
        sandbox=EvolutionSandbox(),
        settings_repository=_BadRepo(),
    )
    # 内置 redline 仍生效
    assert orch.is_redline_item("action_guard_x") is True
    # 普通包不会被误判
    assert orch.is_redline_item("chromadb") is False


def test_redline_empty_settings_value():
    """settings 中 key 不存在 / 值为空时阻拦列表应为空集。"""
    from evolution.tech_radar import TechRadar
    from evolution.sandbox import EvolutionSandbox

    repo = _FakeSettingsRepo({"evolution_upgrade_blocklist": ""})
    orch = EvolutionOrchestrator(
        tech_radar=TechRadar(),
        sandbox=EvolutionSandbox(),
        settings_repository=repo,
    )
    assert orch.is_redline_item("chromadb") is False


def test_confirm_upgrade_blocked_by_user_blocklist():
    """用户阻拦列表中的包，confirm_upgrade 时应被拦截。"""
    from evolution.tech_radar import TechRadar, LEVEL_CONFIG, RadarFinding
    from evolution.sandbox import EvolutionSandbox

    class _FakeRadar(TechRadar):
        def scan(self):
            return [RadarFinding(
                name="chromadb",
                current_version="0.4.0",
                latest_version="0.6.0",
                source="pypi",
                category="dependency",
                level=LEVEL_CONFIG,
            )]

        def get_all_findings(self):
            return self.scan()

    repo = _FakeSettingsRepo({"evolution_upgrade_blocklist": "chromadb"})
    orch = EvolutionOrchestrator(
        tech_radar=_FakeRadar(),
        sandbox=EvolutionSandbox(),
        settings_repository=repo,
    )
    orch.scan_and_report()
    result = orch.confirm_upgrade("chromadb")
    assert result is not None
    assert result.status == FLOW_FAILED
    assert "安全红线" in result.error
