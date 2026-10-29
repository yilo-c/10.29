from __future__ import annotations

import sys
from pathlib import Path

import pytest

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from desktop.doctor_service import DesktopDoctorService
from data.local_storage import LocalDataStore
from plugins.protocol import PluginRegistry
from repositories import LocalSettingsRepository


# 隔离 LLM / automation-hub 环境变量源：系统真实环境（如 DEEPSEEK_API_KEY）是
# ai.client.config._PRIMARY_SPEC.env_api_key 的回退源，若不清掉，"未配置"场景会被
# 误判为已配置（can_recover=True / 归因缺失）。诊断测试只应看显式传入的 settings。
@pytest.fixture(autouse=True)
def _isolate_llm_hub_env(monkeypatch):
    for key in (
        "SIYI_LLM_API_KEY",
        "OPENAI_API_KEY",
        "DEEPSEEK_API_KEY",
        "SIYI_LLM_BASE_URL",
        "OPENAI_BASE_URL",
        "SIYI_LLM_MODEL",
        "OPENAI_MODEL",
        "SIYI_LLM_FALLBACK_API_KEY",
        "OPENAI_FALLBACK_API_KEY",
        "SIYI_LLM_FALLBACK_BASE_URL",
        "OPENAI_FALLBACK_BASE_URL",
        "SIYI_LLM_FALLBACK_MODEL",
        "OPENAI_FALLBACK_MODEL",
        "SIYI_LLM_LOCAL_API_KEY",
        "OPENAI_LOCAL_API_KEY",
        "SIYI_LLM_LOCAL_BASE_URL",
        "OPENAI_LOCAL_BASE_URL",
        "SIYI_LLM_LOCAL_MODEL",
        "OPENAI_LOCAL_MODEL",
        "SIYI_AUTOMATION_HUB_API_BASE",
        "AUTOMATION_HUB_API_BASE",
        "SIYI_AUTOMATION_HUB_API_TOKEN",
        "AUTOMATION_HUB_API_TOKEN",
    ):
        monkeypatch.delenv(key, raising=False)


def test_desktop_doctor_reports_key_sections(tmp_path):
    data_store = LocalDataStore(base_dir=str(tmp_path))
    settings_repo = LocalSettingsRepository(data_store)
    settings_repo.set_setting("llm_api_base", "https://api.example.com/v1")
    settings_repo.set_setting("llm_api_key", "sk-demo")
    settings_repo.set_setting("llm_model", "gpt-5.4")
    settings_repo.set_setting("automation_hub_api_base", "http://127.0.0.1:8010")
    settings_repo.set_setting("automation_hub_api_token", "test-token")
    settings_repo.set_setting(
        "assistant_command_whitelist",
        [
            "detect_nana_pet_process",
            "launch_nana_pet",
            "docker_version_check",
            "start_local_agent_backend",
            "start_docker_stack",
            "open_external_url",
        ],
    )

    registry = PluginRegistry()
    service = DesktopDoctorService(
        data_store=data_store,
        settings_repository=settings_repo,
        app_context=None,
        plugin_registry=registry,
    )

    report = service.run_diagnostics(deep=False)
    text = service.format_report(report)

    assert "[桌面入口]" in text
    assert "[LLM Provider 链路]" in text
    assert "[automation-hub]" in text
    assert report["llm"]["connection_state"] == "configured"
    assert report["automation_hub"]["state"] == "configured"
    assert report["command_whitelist"]["missing_required"] == []


def _make_service(tmp_path, **settings_overrides):
    data_store = LocalDataStore(base_dir=str(tmp_path))
    settings_repo = LocalSettingsRepository(data_store)
    defaults = {
        "llm_api_base": "https://api.example.com/v1",
        "llm_api_key": "sk-demo",
        "llm_model": "gpt-5.4",
        "automation_hub_api_base": "http://127.0.0.1:8010",
        "automation_hub_api_token": "test-token",
        "assistant_command_whitelist": [
            "detect_nana_pet_process",
            "launch_nana_pet",
            "docker_version_check",
            "start_local_agent_backend",
            "start_docker_stack",
            "open_external_url",
        ],
    }
    defaults.update(settings_overrides)
    for k, v in defaults.items():
        settings_repo.set_setting(k, v)
    registry = PluginRegistry()
    return DesktopDoctorService(
        data_store=data_store,
        settings_repository=settings_repo,
        app_context=None,
        plugin_registry=registry,
    )


# ══ 结论 ══


def test_derive_conclusions_healthy(tmp_path):
    service = _make_service(tmp_path)
    report = service.run_diagnostics(deep=False)
    conclusions = service.derive_conclusions(report)
    assert conclusions["can_start"] is True
    assert conclusions["can_recover"] is True


def test_derive_conclusions_no_llm_no_hub(tmp_path, monkeypatch):
    monkeypatch.delenv("SIYI_LLM_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    monkeypatch.delenv("SIYI_AUTOMATION_HUB_API_BASE", raising=False)
    monkeypatch.delenv("AUTOMATION_HUB_API_BASE", raising=False)
    service = _make_service(tmp_path, llm_api_base="", llm_api_key="", automation_hub_api_base="")
    report = service.run_diagnostics(deep=False)
    conclusions = service.derive_conclusions(report)
    assert conclusions["can_recover"] is False


# ══ 归因 ══


def test_derive_attributions_llm_unconfigured(tmp_path, monkeypatch):
    monkeypatch.delenv("SIYI_LLM_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    service = _make_service(tmp_path, llm_api_base="", llm_api_key="")
    report = service.run_diagnostics(deep=False)
    atts = service.derive_attributions(report)
    llm_atts = [a for a in atts if "LLM" in a["message"]]
    assert len(llm_atts) >= 1
    assert llm_atts[0]["category"] == "配置缺失"


def test_derive_attributions_clean_system(tmp_path):
    service = _make_service(tmp_path)
    report = service.run_diagnostics(deep=False)
    atts = service.derive_attributions(report)
    # 健康系统可能只有 info 级别归因（如 sync 关闭、插件未注册）
    critical = [a for a in atts if a["severity"] == "critical"]
    assert len(critical) == 0


# ══ 建议 ══


def test_derive_suggestions_for_missing_config(tmp_path, monkeypatch):
    monkeypatch.delenv("SIYI_LLM_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    monkeypatch.delenv("SIYI_AUTOMATION_HUB_API_BASE", raising=False)
    monkeypatch.delenv("AUTOMATION_HUB_API_BASE", raising=False)
    service = _make_service(tmp_path, llm_api_base="", llm_api_key="", automation_hub_api_base="")
    report = service.run_diagnostics(deep=False)
    atts = service.derive_attributions(report)
    suggestions = service.derive_suggestions(atts)
    assert any("LLM" in s for s in suggestions)
    assert any("automation-hub" in s for s in suggestions)


def test_desktop_doctor_reports_missing_browser_extension_whitelist_on_stable(tmp_path):
    service = _make_service(tmp_path, browser_assistant_trusted_extension_ids=[], assistant_upgrade_channel="stable")

    report = service.run_diagnostics(deep=False)
    atts = service.derive_attributions(report)

    assert any("扩展白名单" in item["message"] for item in atts)
    assert any("扩展 ID" in suggestion for suggestion in service.derive_suggestions(atts))


# ══ format_report 含新段落 ══


def test_derive_attributions_hub_configured_no_token(tmp_path, monkeypatch):
    monkeypatch.delenv("SIYI_AUTOMATION_HUB_API_BASE", raising=False)
    monkeypatch.delenv("AUTOMATION_HUB_API_BASE", raising=False)
    monkeypatch.delenv("SIYI_AUTOMATION_HUB_API_TOKEN", raising=False)
    monkeypatch.delenv("AUTOMATION_HUB_API_TOKEN", raising=False)
    service = _make_service(tmp_path, automation_hub_api_token="")
    report = service.run_diagnostics(deep=False)
    atts = service.derive_attributions(report)
    hub_atts = [a for a in atts if "token" in a["message"]]
    assert len(hub_atts) >= 1
    assert hub_atts[0]["category"] == "配置不完整"
    suggestions = service.derive_suggestions(atts)
    assert any("token" in s for s in suggestions)


def test_format_report_contains_conclusion_sections(tmp_path):
    service = _make_service(tmp_path)
    report = service.run_diagnostics(deep=False)
    text = service.format_report(report)
    assert "[诊断结论]" in text
    assert "能否启动" in text
    assert "能否同步" in text


# ══ P1: 诊断准确性 - 同步维度 ══


def test_derive_conclusions_sync_one_enabled(tmp_path):
    """只要启用一个同步域，can_sync 就为 True。"""
    service = _make_service(tmp_path)
    settings_repo = service.settings_repository
    settings_repo.set_setting("notes_auto_sync_enabled", True)
    settings_repo.set_setting("news_auto_sync_enabled", False)
    settings_repo.set_setting("tasks_auto_sync_enabled", False)
    settings_repo.set_setting("translations_auto_sync_enabled", False)
    settings_repo.set_setting("vocabulary_auto_sync_enabled", False)

    report = service.run_diagnostics(deep=False)
    conclusions = service.derive_conclusions(report)
    assert conclusions["can_sync"] is True


def test_derive_conclusions_sync_all_disabled(tmp_path):
    """所有同步关闭时 can_sync 为 False，且归因中有同步关闭提示。"""
    service = _make_service(tmp_path)
    for key in (
        "notes_auto_sync_enabled",
        "news_auto_sync_enabled",
        "tasks_auto_sync_enabled",
        "translations_auto_sync_enabled",
        "vocabulary_auto_sync_enabled",
    ):
        service.settings_repository.set_setting(key, False)

    report = service.run_diagnostics(deep=False)
    conclusions = service.derive_conclusions(report)
    assert conclusions["can_sync"] is False

    atts = service.derive_attributions(report)
    assert any("同步" in a["message"] for a in atts)


# ══ P1: 浏览器桥端口冲突 ══


def test_derive_attributions_bridge_port_conflict(tmp_path):
    """桥端口被占用时，归因应包含端口冲突。"""
    service = _make_service(tmp_path)
    report = service.run_diagnostics(deep=False)
    # 模拟桥端口冲突的状态
    report["browser_bridge"]["enabled"] = True
    report["browser_bridge"]["status"] = {
        "running": False,
        "last_error": "OSError: [WinError 10048] address already in use",
    }

    atts = service.derive_attributions(report)
    port_atts = [a for a in atts if a["category"] == "端口冲突"]
    assert len(port_atts) >= 1
    assert "10048" in port_atts[0]["message"] or "占用" in port_atts[0]["message"]

    suggestions = service.derive_suggestions(atts)
    assert any("端口" in s for s in suggestions)


# 鈹€鈹€ P1: 多重配置缺失组合 鈹€鈹€


def test_derive_suggestions_covers_all_missing_items(tmp_path, monkeypatch):
    """LLM、hub、命令白名单、扩展白名单、同步全部缺失时，每项都有对应建议。"""
    monkeypatch.delenv("SIYI_LLM_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    monkeypatch.delenv("SIYI_AUTOMATION_HUB_API_BASE", raising=False)
    monkeypatch.delenv("AUTOMATION_HUB_API_BASE", raising=False)
    monkeypatch.delenv("SIYI_AUTOMATION_HUB_API_TOKEN", raising=False)
    monkeypatch.delenv("AUTOMATION_HUB_API_TOKEN", raising=False)
    service = _make_service(
        tmp_path,
        llm_api_base="",
        llm_api_key="",
        automation_hub_api_base="",
        automation_hub_api_token="",
        assistant_command_whitelist=[],
        browser_assistant_trusted_extension_ids=[],
        assistant_upgrade_channel="stable",
    )
    for key in (
        "notes_auto_sync_enabled",
        "news_auto_sync_enabled",
        "tasks_auto_sync_enabled",
        "translations_auto_sync_enabled",
        "vocabulary_auto_sync_enabled",
    ):
        service.settings_repository.set_setting(key, False)

    report = service.run_diagnostics(deep=False)
    atts = service.derive_attributions(report)
    suggestions = service.derive_suggestions(atts)

    assert any("LLM" in s for s in suggestions)
    assert any("automation-hub" in s for s in suggestions)
    assert any("白名单" in s or "命令" in s for s in suggestions)
    assert any("同步" in s for s in suggestions)


# ══ P1: 结论→归因→建议完整管道 ══


def test_full_pipeline_healthy_system_no_critical(tmp_path):
    """健康系统不应产生 critical 归因。"""
    service = _make_service(tmp_path)
    report = service.run_diagnostics(deep=False)
    conclusions = service.derive_conclusions(report)
    atts = service.derive_attributions(report)

    assert conclusions["can_start"] is True
    assert conclusions["can_recover"] is True
    critical = [a for a in atts if a["severity"] == "critical"]
    assert len(critical) == 0


def test_full_pipeline_degraded_system(tmp_path, monkeypatch):
    """LLM 和 hub 都未配时，can_recover 应为 False。"""
    monkeypatch.delenv("SIYI_LLM_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    monkeypatch.delenv("SIYI_AUTOMATION_HUB_API_BASE", raising=False)
    monkeypatch.delenv("AUTOMATION_HUB_API_BASE", raising=False)
    service = _make_service(
        tmp_path,
        llm_api_base="",
        llm_api_key="",
        automation_hub_api_base="",
    )
    report = service.run_diagnostics(deep=False)
    conclusions = service.derive_conclusions(report)
    atts = service.derive_attributions(report)
    suggestions = service.derive_suggestions(atts)

    assert conclusions["can_recover"] is False
    assert len(atts) >= 2
    assert len(suggestions) >= 2


def test_build_debug_assets_contains_expected_sections(tmp_path):
    service = _make_service(tmp_path)

    report = service.run_diagnostics(deep=False)
    debug_assets = service.build_debug_assets(report)

    assert set(debug_assets.keys()) == {"hero", "issues", "suggestions", "detail_cards"}
    assert [item["key"] for item in debug_assets["hero"]] == [
        "can_start",
        "can_sync",
        "can_bridge",
        "can_recover",
    ]
    assert [item["key"] for item in debug_assets["detail_cards"]] == [
        "llm",
        "automation_hub",
        "browser_bridge",
        "sync",
        "command_whitelist",
        "plugins",
    ]


def test_build_debug_assets_orders_issues_by_severity(tmp_path):
    service = _make_service(tmp_path)
    report = service.run_diagnostics(deep=False)
    report["backend_entry"]["exists"] = False
    report["browser_bridge"]["enabled"] = True
    report["browser_bridge"]["status"] = {
        "running": False,
        "last_error": "OSError: [WinError 10048] address already in use",
    }

    debug_assets = service.build_debug_assets(report)

    severities = [item["severity"] for item in debug_assets["issues"]]
    assert severities == sorted(
        severities,
        key=lambda value: {"critical": 0, "warning": 1, "info": 2}[value],
    )


def test_build_status_summary_healthy():
    service = DesktopDoctorService(app_context=None, plugin_registry=PluginRegistry())
    assets = {
        "hero": [
            {"key": "can_start", "value": True},
            {"key": "can_sync", "value": True},
            {"key": "can_bridge", "value": True},
            {"key": "can_recover", "value": True},
        ],
        "issues": [],
    }
    assert service.build_status_summary(assets) == "Doctor: system healthy"


def test_build_status_summary_critical():
    service = DesktopDoctorService(app_context=None, plugin_registry=PluginRegistry())
    assets = {
        "hero": [{"key": "can_start", "value": False}],
        "issues": [
            {"severity": "critical", "message": "LLM not configured"},
            {"severity": "warning", "message": "Bridge port conflict"},
        ],
    }
    result = service.build_status_summary(assets)
    assert "1 critical" in result
    assert "1 warning" in result


def test_build_status_summary_warning_only():
    service = DesktopDoctorService(app_context=None, plugin_registry=PluginRegistry())
    assets = {
        "hero": [{"key": "can_start", "value": True}],
        "issues": [{"severity": "warning", "message": "Bridge port conflict"}],
    }
    result = service.build_status_summary(assets)
    assert "1 warning" in result
    assert "critical" not in result


def test_build_status_summary_startup_blocked():
    service = DesktopDoctorService(app_context=None, plugin_registry=PluginRegistry())
    assets = {
        "hero": [{"key": "can_start", "value": False}],
        "issues": [],
    }
    assert service.build_status_summary(assets) == "Doctor: startup blocked"


def test_build_status_summary_no_assets():
    service = DesktopDoctorService(app_context=None, plugin_registry=PluginRegistry())
    assert service.build_status_summary(None) == "Doctor: no diagnostics available"
    assert service.build_status_summary({}) == "Doctor: no diagnostics available"


def test_build_continue_suggestion_returns_top_critical():
    service = DesktopDoctorService(app_context=None, plugin_registry=PluginRegistry())
    assets = {
        "issues": [
            {"severity": "critical", "message": "API key missing"},
            {"severity": "warning", "message": "Cache directory not found"},
        ],
    }
    suggestion = service.build_continue_suggestion(assets)
    assert suggestion is not None
    assert suggestion["action"] == "open_doctor"
    assert "API key missing" in suggestion["text"]


def test_build_continue_suggestion_returns_none_when_no_issues():
    service = DesktopDoctorService(app_context=None, plugin_registry=PluginRegistry())
    assert service.build_continue_suggestion({"issues": []}) is None
    assert service.build_continue_suggestion({}) is None
    assert service.build_continue_suggestion(None) is None
