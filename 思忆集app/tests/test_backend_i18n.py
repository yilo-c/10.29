"""Tests for backend.i18n — locale resolution and message templating."""

from __future__ import annotations

import pytest

from backend import i18n


@pytest.fixture(autouse=True)
def _reset_provider():
    """Reset locale provider after each test to avoid leaking state."""
    original = i18n._locale_provider
    yield
    i18n._locale_provider = original


def test_default_locale_is_zh_cn():
    assert i18n.DEFAULT_LOCALE == "zh-CN"
    # When no provider registered, fall back to default
    assert i18n.get_current_locale() == "zh-CN"


def test_translate_zh_cn_with_param():
    i18n.register_locale_provider(lambda: "zh-CN")
    assert i18n.t("error.missing_param", name="agent_id") == "缺少 agent_id 参数"


def test_translate_en_with_param():
    i18n.register_locale_provider(lambda: "en")
    assert i18n.t("error.missing_param", name="agent_id") == "Missing parameter: agent_id"


def test_unknown_key_returns_key_itself():
    i18n.register_locale_provider(lambda: "zh-CN")
    assert i18n.t("totally.unknown.key") == "totally.unknown.key"


def test_unsupported_locale_falls_back_to_zh_cn():
    i18n.register_locale_provider(lambda: "fr-FR")
    assert i18n.get_current_locale() == "zh-CN"
    assert i18n.t("agent.directory_unavailable") == "AgentDirectory 未初始化"


def test_short_locale_prefix_accepted():
    i18n.register_locale_provider(lambda: "en-US")
    assert i18n.get_current_locale() == "en"
    assert i18n.t("agent.directory_unavailable") == "AgentDirectory not initialized"


def test_provider_exception_falls_back_to_default():
    def _bad():
        raise RuntimeError("boom")

    i18n.register_locale_provider(_bad)
    assert i18n.get_current_locale() == "zh-CN"


def test_missing_locale_entry_falls_back_to_zh_cn(monkeypatch):
    # Inject a key that only has zh-CN, request en → should get zh-CN
    monkeypatch.setitem(i18n._TABLE, "test.only_zh", {"zh-CN": "仅中文"})
    i18n.register_locale_provider(lambda: "en")
    assert i18n.t("test.only_zh") == "仅中文"


def test_unfilled_placeholder_preserved():
    i18n.register_locale_provider(lambda: "zh-CN")
    # Missing 'name' param → format raises KeyError → template returned verbatim
    assert i18n.t("error.missing_param") == "缺少 {name} 参数"


def test_agent_messages_have_both_locales():
    keys = ["agent.directory_unavailable", "agent.cron_unavailable",
            "agent.not_registered", "agent.unavailable",
            "remote.desktop_domain_unavailable", "remote.shell_domain_unavailable",
            "provider.added", "provider.removed", "provider.updated",
            "provider.not_found", "kiro.not_running", "error.missing_param"]
    for key in keys:
        entry = i18n.all_keys().get(key)
        assert entry is not None, f"Missing translation entry: {key}"
        assert "zh-CN" in entry, f"{key} missing zh-CN"
        assert "en" in entry, f"{key} missing en"


def test_handler_uses_i18n_when_locale_changes():
    """Smoke test: an agent_tasks handler returns english error under en locale."""
    # Verify the format placeholders in templates work with handler params
    i18n.register_locale_provider(lambda: "en")
    assert i18n.t("agent.not_registered", agent_id="claude-code") == "Agent 'claude-code' is not registered"
    i18n.register_locale_provider(lambda: "zh-CN")
    assert i18n.t("agent.not_registered", agent_id="claude-code") == "Agent 'claude-code' 未注册"
