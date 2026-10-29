"""Tests for the Plugin SDK implementation.

Covers manifest parsing/validation, the 5 plugin types, registry lifecycle,
permission enforcement, and sandbox isolation.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# ── Helpers to locate app root ───────────────────────────────────────────────

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))


# ── Manifest tests ───────────────────────────────────────────────────────────


class TestManifestParsing:
    """PluginManifest parsing and validation."""

    def test_parse_valid_manifest(self):
        from plugins.manifest import PluginManifest, validate_manifest_dict

        data = {
            "manifest_version": "1.0",
            "id": "com.example.weather",
            "name": "天气卡片",
            "version": "1.0.0",
            "type": "card",
            "entry_point": "weather/plugin.py:WeatherCardPlugin",
            "permissions": ["network:api.openweathermap.org", "storage:local:weather_cache"],
            "capabilities": ["render_card", "background_refresh"],
            "config_schema": {
                "type": "object",
                "properties": {
                    "city": {"type": "string", "default": "auto"},
                },
            },
            "description": "天气卡片插件",
            "author": "思忆集团队",
        }
        errors = validate_manifest_dict(data)
        assert errors == [], f"validation errors: {errors}"

        manifest = PluginManifest.from_dict(data)
        assert manifest.id == "com.example.weather"
        assert manifest.type == "card"
        assert manifest.version == "1.0.0"
        assert manifest.entry_point == "weather/plugin.py:WeatherCardPlugin"
        assert "network:api.openweathermap.org" in manifest.permissions
        assert "render_card" in manifest.capabilities

    def test_parse_from_json(self):
        from plugins.manifest import PluginManifest

        json_str = """{
            "id": "com.example.test",
            "type": "tool",
            "entry_point": "test.py:TestPlugin",
            "permissions": [],
            "capabilities": ["run"],
            "config_schema": {},
            "version": "0.1.0",
            "name": "Test"
        }"""
        manifest = PluginManifest.from_json(json_str)
        assert manifest.id == "com.example.test"
        assert manifest.type == "tool"

    def test_manifest_immutable(self):
        from plugins.manifest import PluginManifest

        manifest = PluginManifest(
            manifest_version="1.0",
            id="com.example.test",
            name="Test",
            version="1.0.0",
            type="tool",
            entry_point="test.py:TestPlugin",
            permissions=["network:*"],
            capabilities=["run"],
            config_schema={},
        )
        with pytest.raises(AttributeError):
            manifest.id = "changed"  # type: ignore[misc]

    def test_manifest_to_dict_roundtrip(self):
        from plugins.manifest import PluginManifest

        manifest = PluginManifest(
            manifest_version="1.0",
            id="com.example.roundtrip",
            name="Round Trip",
            version="1.0.0",
            type="card",
            entry_point="card.py:MyCard",
            permissions=["network:*", "storage:local:cache"],
            capabilities=["render_card"],
            config_schema={"type": "object"},
            description="A round-trip test",
            author="Test",
            min_app_version="0.2.0",
            max_app_version="1.0.0",
        )
        d = manifest.to_dict()
        manifest2 = PluginManifest.from_dict(d)
        assert manifest2.id == manifest.id
        assert manifest2.type == manifest.type
        assert list(manifest2.permissions) == list(manifest.permissions)
        assert manifest2.min_app_version == manifest.min_app_version
        assert manifest2.max_app_version == manifest.max_app_version


class TestManifestValidation:
    """Manifest validation edge cases."""

    def test_missing_required_fields(self):
        from plugins.manifest import validate_manifest_dict

        errors = validate_manifest_dict({})
        required = {"id", "type", "entry_point", "permissions", "capabilities", "config_schema"}
        found = {e.split(":")[0].strip() for e in errors}
        for field in required:
            assert any(field in e for e in errors), f"expected error for {field}: {errors}"

    def test_invalid_type(self):
        from plugins.manifest import validate_manifest_dict

        errors = validate_manifest_dict({
            "id": "com.example.bad",
            "type": "invalid_type",
            "entry_point": "x.py:X",
            "permissions": [],
            "capabilities": [],
            "config_schema": {},
        })
        assert any("invalid type" in e for e in errors)

    def test_all_valid_types(self):
        from plugins.manifest import VALID_PLUGIN_TYPES, validate_manifest_dict

        for pt in VALID_PLUGIN_TYPES:
            errors = validate_manifest_dict({
                "id": f"com.example.{pt}",
                "type": pt,
                "entry_point": "x.py:X",
                "permissions": [],
                "capabilities": [],
                "config_schema": {},
            })
            assert errors == [], f"type '{pt}' should be valid: {errors}"

    def test_invalid_entry_point(self):
        from plugins.manifest import validate_manifest_dict

        errors = validate_manifest_dict({
            "id": "com.example.bad",
            "type": "tool",
            "entry_point": "NoClassName",  # Missing ':'
            "permissions": [],
            "capabilities": [],
            "config_schema": {},
        })
        assert any("entry_point" in e for e in errors)

    def test_invalid_permission_format(self):
        from plugins.manifest import validate_manifest_dict

        errors = validate_manifest_dict({
            "id": "com.example.bad",
            "type": "tool",
            "entry_point": "x.py:X",
            "permissions": ["badformat"],
            "capabilities": [],
            "config_schema": {},
        })
        assert any("invalid permission format" in e for e in errors)

    def test_unknown_permission_category(self):
        from plugins.manifest import validate_manifest_dict

        errors = validate_manifest_dict({
            "id": "com.example.bad",
            "type": "tool",
            "entry_point": "x.py:X",
            "permissions": ["unknown:something"],
            "capabilities": [],
            "config_schema": {},
        })
        assert any("unknown category" in e for e in errors)

    def test_invalid_semver(self):
        from plugins.manifest import validate_manifest_dict

        errors = validate_manifest_dict({
            "id": "com.example.bad",
            "type": "tool",
            "entry_point": "x.py:X",
            "version": "not-a-semver",
            "permissions": [],
            "capabilities": [],
            "config_schema": {},
        })
        assert any("version" in e for e in errors)

    def test_valid_semver_with_prerelease(self):
        from plugins.manifest import validate_manifest_dict

        errors = validate_manifest_dict({
            "id": "com.example.good",
            "type": "tool",
            "entry_point": "x.py:X",
            "version": "1.0.0-alpha",
            "permissions": [],
            "capabilities": [],
            "config_schema": {},
        })
        assert errors == [], f"validation errors: {errors}"


# ── Plugin type tests ────────────────────────────────────────────────────────


class TestPluginBase:
    """Base plugin class and 5 plugin type implementations."""

    def test_create_tool_plugin(self):
        from plugins.manifest import PluginManifest
        from plugins.base import ToolPlugin

        manifest = PluginManifest(
            manifest_version="1.0",
            id="com.example.translator",
            name="Translator",
            version="1.0.0",
            type="tool",
            entry_point="translator.py:TranslatePlugin",
            permissions=["network:api.translate.com"],
            capabilities=["translate"],
            config_schema={"type": "object"},
        )

        class TranslatePlugin(ToolPlugin):
            def execute(self, params):
                return {"translated": "hello", "source": params.get("text", "")}

        plugin = TranslatePlugin(manifest)
        assert plugin.manifest.id == "com.example.translator"
        assert plugin.tool_id == "com.example.translator"
        assert plugin.state.value == "installed"
        assert plugin.health.status == "healthy"

        result = plugin.execute({"text": "你好"})
        assert result["translated"] == "hello"

    def test_create_datasource_plugin(self):
        from plugins.manifest import PluginManifest
        from plugins.base import DataSourcePlugin

        manifest = PluginManifest(
            manifest_version="1.0",
            id="com.example.rss",
            name="RSS Feed",
            version="1.0.0",
            type="datasource",
            entry_point="rss.py:RSSPlugin",
            permissions=["network:*"],
            capabilities=["fetch_data", "sync"],
            config_schema={},
        )

        class RSSPlugin(DataSourcePlugin):
            def fetch(self, query):
                return [{"title": "Test Article", "url": "http://example.com"}]

        plugin = RSSPlugin(manifest)
        items = plugin.fetch({"limit": 10})
        assert len(items) == 1
        assert items[0]["title"] == "Test Article"

        # Default sync
        result = plugin.sync()
        assert "items" in result

    def test_create_card_plugin(self):
        from plugins.manifest import PluginManifest
        from plugins.base import CardPlugin

        manifest = PluginManifest(
            manifest_version="1.0",
            id="com.example.weather",
            name="Weather",
            version="1.0.0",
            type="card",
            entry_point="weather.py:WeatherCard",
            permissions=["network:api.weather.com"],
            capabilities=["render_card"],
            config_schema={},
        )

        class WeatherCard(CardPlugin):
            def render(self, state):
                return f"Weather card: {state.get('temp', 'N/A')}掳C"

        plugin = WeatherCard(manifest)
        rendered = plugin.render({"temp": 22})
        assert "22" in rendered
        assert plugin.preferred_size == (300, 200)

    def test_create_action_plugin(self):
        from plugins.manifest import PluginManifest
        from plugins.base import ActionPlugin

        manifest = PluginManifest(
            manifest_version="1.0",
            id="com.example.backup",
            name="Auto Backup",
            version="1.0.0",
            type="action",
            entry_point="backup.py:BackupPlugin",
            permissions=["fs:write:/backup"],
            capabilities=["scheduled_action"],
            config_schema={},
        )

        class BackupPlugin(ActionPlugin):
            def condition(self, context):
                return context.get("hour") == 2

            def perform(self, context):
                return {"backed_up": True, "at": str(context.get("hour"))}

        plugin = BackupPlugin(manifest)
        assert plugin.condition({"hour": 2}) is True
        assert plugin.condition({"hour": 10}) is False
        result = plugin.perform({"hour": 2})
        assert result["backed_up"] is True

    def test_create_companion_plugin(self):
        from plugins.manifest import PluginManifest
        from plugins.base import CompanionPlugin

        manifest = PluginManifest(
            manifest_version="1.0",
            id="com.example.clipboard",
            name="Clipboard Monitor",
            version="1.0.0",
            type="companion",
            entry_point="clipboard.py:ClipboardPlugin",
            permissions=["ui:companion"],
            capabilities=["show_status"],
            config_schema={},
        )

        class ClipboardPlugin(CompanionPlugin):
            def on_hover(self):
                return "Clipboard: 3 items"

            def on_click(self):
                pass

        plugin = ClipboardPlugin(manifest)
        assert plugin.on_hover() == "Clipboard: 3 items"
        assert plugin.companion_id == "com.example.clipboard"
        assert plugin.status_text == ""


# ── Registry lifecycle tests ─────────────────────────────────────────────────


class TestPluginRegistry:
    """PluginRegistry lifecycle: install, enable, disable, uninstall."""

    def test_install_plugin(self):
        from plugins.manifest import PluginManifest
        from plugins.registry import PluginRegistry
        from plugins.base import ToolPlugin

        manifest = PluginManifest(
            manifest_version="1.0",
            id="com.test.hello",
            name="Hello",
            version="1.0.0",
            type="tool",
            entry_point="hello.py:HelloPlugin",
            permissions=["ui:companion"],
            capabilities=["greet"],
            config_schema={},
        )

        class HelloPlugin(ToolPlugin):
            def execute(self, params):
                return {"message": f"Hello {params.get('name', 'world')}!"}

        registry = PluginRegistry()
        plugin_id = registry.install(manifest, plugin_instance=HelloPlugin(manifest))
        assert plugin_id == "com.test.hello"

        plugin = registry.get("com.test.hello")
        assert plugin is not None
        assert plugin.state.value == "installed"

    def test_install_duplicate_raises(self):
        from plugins.manifest import PluginManifest
        from plugins.registry import PluginRegistry
        from plugins.base import ToolPlugin

        manifest = PluginManifest(
            manifest_version="1.0",
            id="com.test.dup",
            name="Dup",
            version="1.0.0",
            type="tool",
            entry_point="dup.py:DupPlugin",
            permissions=[],
            capabilities=["x"],
            config_schema={},
        )

        class DupPlugin(ToolPlugin):
            def execute(self, p): return {}

        registry = PluginRegistry()
        registry.install(manifest, plugin_instance=DupPlugin(manifest))
        with pytest.raises(ValueError, match="already installed"):
            registry.install(manifest, plugin_instance=DupPlugin(manifest))

    def test_enable_disable_lifecycle(self):
        from plugins.manifest import PluginManifest
        from plugins.registry import PluginRegistry
        from plugins.base import ToolPlugin, PluginState

        manifest = PluginManifest(
            manifest_version="1.0",
            id="com.test.lifecycle",
            name="Lifecycle",
            version="1.0.0",
            type="tool",
            entry_point="lifecycle.py:LCCheck",
            permissions=[],
            capabilities=["test"],
            config_schema={},
        )

        class LCCheck(ToolPlugin):
            def __init__(self, manifest):
                super().__init__(manifest)
                self.enable_called = False
                self.disable_called = False

            def execute(self, params):
                return {"ok": True}

            def on_enable(self):
                self.enable_called = True

            def on_disable(self):
                self.disable_called = True

        registry = PluginRegistry()
        lc_plugin = LCCheck(manifest)
        registry.install(manifest, plugin_instance=lc_plugin)

        registry.enable("com.test.lifecycle")
        assert lc_plugin.state == PluginState.ENABLED
        assert lc_plugin.enable_called is True

        registry.disable("com.test.lifecycle")
        assert lc_plugin.state == PluginState.DISABLED
        assert lc_plugin.disable_called is True

    def test_uninstall_removes_plugin(self):
        from plugins.manifest import PluginManifest
        from plugins.registry import PluginRegistry
        from plugins.base import ToolPlugin

        manifest = PluginManifest(
            manifest_version="1.0",
            id="com.test.removeme",
            name="Remove",
            version="1.0.0",
            type="tool",
            entry_point="remove.py:RemovePlugin",
            permissions=[],
            capabilities=["x"],
            config_schema={},
        )

        class RemovePlugin(ToolPlugin):
            def execute(self, params):
                return {}

        registry = PluginRegistry()
        registry.install(manifest, plugin_instance=RemovePlugin(manifest))
        assert registry.get("com.test.removeme") is not None

        registry.uninstall("com.test.removeme")
        assert registry.get("com.test.removeme") is None

    def test_list_by_type(self):
        from plugins.manifest import PluginManifest
        from plugins.registry import PluginRegistry
        from plugins.base import ToolPlugin, CardPlugin

        registry = PluginRegistry()

        tool_manifest = PluginManifest(
            manifest_version="1.0", id="com.test.t1", name="T1", version="1.0.0",
            type="tool", entry_point="t.py:T", permissions=[], capabilities=[], config_schema={},
        )
        card_manifest = PluginManifest(
            manifest_version="1.0", id="com.test.c1", name="C1", version="1.0.0",
            type="card", entry_point="c.py:C", permissions=[], capabilities=[], config_schema={},
        )
        tool2_manifest = PluginManifest(
            manifest_version="1.0", id="com.test.t2", name="T2", version="1.0.0",
            type="tool", entry_point="t2.py:T2", permissions=[], capabilities=[], config_schema={},
        )

        class T(ToolPlugin):
            def execute(self, p): return {}
        class C(CardPlugin):
            def render(self, s): return "card"

        registry.install(tool_manifest, plugin_instance=T(tool_manifest))
        registry.install(card_manifest, plugin_instance=C(card_manifest))
        registry.install(tool2_manifest, plugin_instance=T(tool2_manifest))

        tools = registry.list_by_type("tool")
        cards = registry.list_by_type("card")
        assert len(tools) == 2
        assert len(cards) == 1

    def test_list_by_capability(self):
        from plugins.manifest import PluginManifest
        from plugins.registry import PluginRegistry
        from plugins.base import ToolPlugin

        a_manifest = PluginManifest(
            manifest_version="1.0", id="com.test.a", name="A", version="1.0.0",
            type="tool", entry_point="a.py:A", permissions=[], capabilities=["render_card", "sync"],
            config_schema={},
        )
        b_manifest = PluginManifest(
            manifest_version="1.0", id="com.test.b", name="B", version="1.0.0",
            type="tool", entry_point="b.py:B", permissions=[], capabilities=["sync"],
            config_schema={},
        )

        class A(ToolPlugin):
            def execute(self, p): return {}
        class B(ToolPlugin):
            def execute(self, p): return {}

        registry = PluginRegistry()
        registry.install(a_manifest, plugin_instance=A(a_manifest))
        registry.install(b_manifest, plugin_instance=B(b_manifest))

        assert len(registry.list_by_capability("render_card")) == 1
        assert len(registry.list_by_capability("sync")) == 2
        assert len(registry.list_by_capability("nonexistent")) == 0

    def test_health_check(self):
        from plugins.manifest import PluginManifest
        from plugins.registry import PluginRegistry
        from plugins.base import ToolPlugin

        manifest = PluginManifest(
            manifest_version="1.0", id="com.test.health", name="Health", version="1.0.0",
            type="tool", entry_point="h.py:H", permissions=[], capabilities=["x"],
            config_schema={},
        )

        class H(ToolPlugin):
            def execute(self, p): return {}

        registry = PluginRegistry()
        registry.install(manifest, plugin_instance=H(manifest))
        health = registry.health_check("com.test.health")
        assert health.status == "healthy"

        summary = registry.get_health_summary()
        assert summary["total"] == 1
        assert summary["healthy"] == 1

    def test_config_management(self):
        from plugins.manifest import PluginManifest
        from plugins.registry import PluginRegistry
        from plugins.base import ToolPlugin

        manifest = PluginManifest(
            manifest_version="1.0", id="com.test.cfg", name="Config", version="1.0.0",
            type="tool", entry_point="cfg.py:C", permissions=[], capabilities=["x"],
            config_schema={"type": "object", "properties": {"key": {"type": "string"}}},
        )

        class CfgPlugin(ToolPlugin):
            def execute(self, p): return {}

        registry = PluginRegistry()
        registry.install(manifest, plugin_instance=CfgPlugin(manifest))
        registry.set_config("com.test.cfg", {"key": "value", "number": 42})
        config = registry.get_config("com.test.cfg")
        assert config["key"] == "value"
        assert config["number"] == 42

    def test_capability_registry_sync(self):
        from plugins.manifest import PluginManifest
        from plugins.registry import PluginRegistry
        from plugins.base import ToolPlugin

        manifest = PluginManifest(
            manifest_version="1.0", id="com.test.capsync", name="CapSync", version="1.0.0",
            type="tool", entry_point="cs.py:CS", permissions=[], capabilities=["greet", "wave"],
            config_schema={},
        )

        class CS(ToolPlugin):
            def execute(self, p): return {}

        mock_cap_registry = MagicMock()
        mock_cap_registry.get_capability.return_value = None

        registry = PluginRegistry(capability_registry=mock_cap_registry)
        registry.install(manifest, plugin_instance=CS(manifest))
        registry.enable("com.test.capsync")

        # Should have registered capabilities
        assert mock_cap_registry.register_capability.call_count >= 2

    def test_describe(self):
        from plugins.manifest import PluginManifest
        from plugins.registry import PluginRegistry
        from plugins.base import ToolPlugin

        manifest = PluginManifest(
            manifest_version="1.0", id="com.test.desc", name="Desc", version="1.0.0",
            type="tool", entry_point="d.py:D", permissions=["network:*"],
            capabilities=["x"], config_schema={},
        )

        class D(ToolPlugin):
            def execute(self, p): return {}

        registry = PluginRegistry()
        registry.install(manifest, plugin_instance=D(manifest))
        desc = registry.describe()
        assert "plugins" in desc
        assert "counts" in desc
        assert "health" in desc
        assert len(desc["plugins"]) == 1


# ── Permission tests ─────────────────────────────────────────────────────────


class TestPermissionModel:
    """PermissionManager: declaration, checking, high-risk detection."""

    def test_declare_and_check(self):
        from plugins.permissions import PermissionManager

        pm = PermissionManager()
        pm.declare_permissions("my-plugin", [
            "network:api.example.com",
            "storage:local:cache",
        ])
        assert pm.check_permission("my-plugin", "network:api.example.com") is True
        assert pm.check_permission("my-plugin", "storage:local:cache") is True

    def test_missing_permission(self):
        from plugins.permissions import PermissionManager

        pm = PermissionManager()
        pm.declare_permissions("my-plugin", ["network:api.example.com"])
        assert pm.check_permission("my-plugin", "network:other.com") is False

    def test_unknown_plugin_denied(self):
        from plugins.permissions import PermissionManager

        pm = PermissionManager()
        assert pm.check_permission("unknown", "anything") is False

    def test_wildcard_permission(self):
        from plugins.permissions import PermissionManager

        pm = PermissionManager()
        pm.declare_permissions("my-plugin", ["network:*"])
        assert pm.check_permission("my-plugin", "network:api.example.com") is True
        assert pm.check_permission("my-plugin", "network:google.com") is True

    def test_revoke_permission(self):
        from plugins.permissions import PermissionManager

        pm = PermissionManager()
        pm.declare_permissions("my-plugin", ["network:api.example.com"])
        assert pm.check_permission("my-plugin", "network:api.example.com") is True

        pm.revoke_permission("my-plugin", "network:api.example.com")
        assert pm.check_permission("my-plugin", "network:api.example.com") is False

    def test_grant_after_revoke(self):
        from plugins.permissions import PermissionManager

        pm = PermissionManager()
        pm.declare_permissions("my-plugin", ["network:api.example.com"])
        pm.revoke_permission("my-plugin", "network:api.example.com")
        assert pm.check_permission("my-plugin", "network:api.example.com") is False

        pm.grant_permission("my-plugin", "network:api.example.com")
        assert pm.check_permission("my-plugin", "network:api.example.com") is True

    def test_require_permission_raises(self):
        from plugins.permissions import PermissionManager, PermissionDeniedError

        pm = PermissionManager()
        pm.declare_permissions("my-plugin", ["network:example.com"])

        with pytest.raises(PermissionDeniedError):
            pm.require_permission("my-plugin", "network:other.com")

        # Should not raise
        pm.require_permission("my-plugin", "network:example.com")

    def test_high_risk_detection(self):
        from plugins.permissions import PermissionManager

        pm = PaymentBuilder = PermissionManager()
        pm.declare_permissions("risky", ["network:*", "fs:*"])
        pm.declare_permissions("safe", ["storage:local:cache"])

        assert pm.is_high_risk_plugin("risky") is True
        assert pm.is_high_risk_plugin("safe") is False

        high_risk = pm.get_high_risk_permissions("risky")
        assert "network:*" in high_risk
        assert "fs:*" in high_risk

    def test_permission_matches_glob(self):
        from plugins.permissions import permission_matches

        assert permission_matches("network:*", "network:api.example.com") is True
        assert permission_matches("fs:read:/data/*", "fs:read:/data/notes/") is True
        assert permission_matches("storage:local:cache", "network:other") is False
        assert permission_matches("*", "anything:here") is True

    def test_parse_permission(self):
        from plugins.permissions import parse_permission

        cat, res = parse_permission("network:api.example.com")
        assert cat == "network"
        assert res == "api.example.com"

        with pytest.raises(ValueError):
            parse_permission("invalid")

    def test_require_any_permission(self):
        from plugins.permissions import PermissionManager

        pm = PermissionManager()
        pm.declare_permissions("my-plugin", ["network:a.com", "storage:local:x"])
        match = pm.require_any_permission("my-plugin", ["network:unknown.com", "network:a.com"])
        assert match == "network:a.com"

        no_match = pm.require_any_permission("my-plugin", ["network:unknown.com"])
        assert no_match is None

    def test_list_plugins_with_permission(self):
        from plugins.permissions import PermissionManager

        pm = PermissionManager()
        pm.declare_permissions("p1", ["network:a.com"])
        pm.declare_permissions("p2", ["network:a.com", "storage:local:x"])
        pm.declare_permissions("p3", ["network:b.com"])

        result = pm.list_plugins_with_permission("network:a.com")
        assert "p1" in result
        assert "p2" in result
        assert "p3" not in result


# ── Sandbox tests ────────────────────────────────────────────────────────────


class TestSandbox:
    """Plugin isolation/sandbox: error boundary, timeout, resource limits."""

    def test_error_boundary_catches_exceptions(self):
        from plugins.sandbox import error_boundary, PluginSandboxError

        with pytest.raises(PluginSandboxError, match="test-plugin"):
            with error_boundary("test-plugin", "test_operation"):
                raise ValueError("something went wrong")

    def test_error_boundary_raises_sandbox_error(self):
        from plugins.sandbox import error_boundary, PluginSandboxError

        with pytest.raises(PluginSandboxError, match="test-plugin"):
            with error_boundary("test-plugin", "fail"):
                raise RuntimeError("plugin crashed")

    def test_error_boundary_does_not_catch_sandbox_errors(self):
        from plugins.sandbox import error_boundary, PluginSandboxError

        with pytest.raises(PluginSandboxError, match="already_sandboxed"):
            with error_boundary("test-plugin", "nested"):
                raise PluginSandboxError("nested already_sandboxed error")

    def test_execute_with_timeout_success(self):
        from plugins.sandbox import execute_with_timeout

        result = execute_with_timeout(lambda x: x + 1, 5, 41)
        assert result == 42

    def test_execute_with_timeout_exceeds(self):
        from plugins.sandbox import execute_with_timeout, ExecutionTimeoutError

        def slow():
            time.sleep(10)
            return "done"

        with pytest.raises(ExecutionTimeoutError):
            execute_with_timeout(slow, timeout=1)

    @staticmethod
    def _noop_memory_check(limit_mb: int = 256) -> None:
        pass

    def test_sandboxed_execute_success(self):
        from plugins.sandbox import sandboxed_execute, ResourceMonitor

        with patch.object(ResourceMonitor, "check_memory_limit", self._noop_memory_check):
            result = sandboxed_execute("test", lambda a, b: a + b, 2, 3, timeout=5)
        assert result == 5

    def test_sandboxed_execute_failure(self):
        from plugins.sandbox import sandboxed_execute, PluginSandboxError, ResourceMonitor

        def crash():
            raise ValueError("boom")

        with patch.object(ResourceMonitor, "check_memory_limit", self._noop_memory_check):
            with pytest.raises(PluginSandboxError, match="test"):
                sandboxed_execute("test", crash)

    def test_resource_monitor(self):
        from plugins.sandbox import ResourceMonitor

        mem = ResourceMonitor.current_memory_mb()
        assert isinstance(mem, float)
        assert mem >= 0

    def test_internal_modules_blocked_list(self):
        from plugins.sandbox import INTERNAL_MODULES_BLOCKED

        assert "ui_main_window" in INTERNAL_MODULES_BLOCKED
        assert "DatabaseHandler" in INTERNAL_MODULES_BLOCKED
        assert "app_context" in INTERNAL_MODULES_BLOCKED


# ── Integration tests ────────────────────────────────────────────────────────


class TestIntegration:
    """End-to-end plugin lifecycle: install →enable →disable →uninstall."""

    def test_full_lifecycle(self):
        from plugins.manifest import PluginManifest
        from plugins.registry import PluginRegistry
        from plugins.base import ToolPlugin, PluginState
        from plugins.permissions import PermissionDeniedError

        manifest = PluginManifest(
            manifest_version="1.0",
            id="com.test.full",
            name="Full Lifecycle",
            version="1.0.0",
            type="tool",
            entry_point="full.py:FullPlugin",
            permissions=["network:api.test.com", "storage:local:test"],
            capabilities=["test_full"],
            config_schema={
                "type": "object",
                "properties": {"mode": {"type": "string", "default": "auto"}},
            },
            description="A full lifecycle test plugin",
            author="Test",
        )

        class FullPlugin(ToolPlugin):
            def __init__(self, manifest):
                super().__init__(manifest)
                self.hooks = []

            def execute(self, params):
                return {"executed": True, "mode": self.get_config_value("mode", "default")}

            def on_install(self):
                self.hooks.append("install")

            def on_enable(self):
                self.hooks.append("enable")

            def on_disable(self):
                self.hooks.append("disable")

            def on_uninstall(self):
                self.hooks.append("uninstall")

        registry = PluginRegistry()

        # Install
        full_plugin = FullPlugin(manifest)
        plugin_id = registry.install(manifest, plugin_instance=full_plugin)
        plugin = registry.get(plugin_id)
        assert plugin_id == "com.test.full"
        assert plugin.hooks == ["install"]

        # Enable
        registry.enable(plugin_id)
        assert plugin.state == PluginState.ENABLED
        assert "enable" in plugin.hooks

        # Config
        registry.set_config(plugin_id, {"mode": "turbo"})
        result = plugin.execute({})
        assert result["mode"] == "turbo"

        # Permission check
        assert registry.permission_manager.check_permission(
            plugin_id, "network:api.test.com"
        ) is True

        # Disable
        registry.disable(plugin_id)
        assert plugin.state == PluginState.DISABLED
        assert "disable" in plugin.hooks

        # Uninstall
        registry.uninstall(plugin_id)
        assert registry.get(plugin_id) is None
        assert "uninstall" in plugin.hooks

    def test_plugin_crash_does_not_crash_main(self):
        """Verify that a crashing plugin doesn't crash the test/app."""
        from plugins.sandbox import sandboxed_execute, PluginSandboxError

        def crash_plugin():
            raise RuntimeError("plugin internal error")

        try:
            sandboxed_execute("crashy", crash_plugin)
            pytest.fail("expected PluginSandboxError")
        except PluginSandboxError:
            pass  # Expected —crash contained

        # App continues running after the crash
        assert 1 + 1 == 2

    def test_permission_enforcement_prevents_unauthorized_access(self):
        """Verify that permission model blocks unauthorized operations."""
        from plugins.permissions import PermissionManager, PermissionDeniedError

        pm = PermissionManager()
        pm.declare_permissions("my-plugin", ["storage:local:cache"])

        # Access permitted
        pm.require_permission("my-plugin", "storage:local:cache")

        # Access denied
        with pytest.raises(PermissionDeniedError):
            pm.require_permission("my-plugin", "network:api.secret.com")

    def test_manifest_validation_rejects_invalid_plugin(self):
        from plugins.manifest import validate_manifest_dict

        errors = validate_manifest_dict({})
        assert len(errors) > 0


# ── End-to-end CLI simulation ────────────────────────────────────────────────


class TestPluginCLISimulation:
    """Simulate the CLI-oriented install workflow."""

    def test_install_from_manifest_dict(self):
        from plugins.manifest import PluginManifest, validate_manifest_dict
        from plugins.registry import PluginRegistry
        from plugins.base import ToolPlugin

        manifest_dict = {
            "manifest_version": "1.0",
            "id": "com.example.weather-card",
            "name": "天气卡片",
            "version": "1.0.0",
            "type": "card",
            "entry_point": "weather_card/plugin.py:WeatherCardPlugin",
            "permissions": ["network:api.openweathermap.org", "storage:local:weather_cache"],
            "capabilities": ["render_card", "background_refresh"],
            "config_schema": {
                "type": "object",
                "properties": {
                    "city": {"type": "string", "default": "auto"},
                    "refresh_interval_min": {"type": "integer", "default": 30},
                },
            },
            "min_app_version": "0.1.0",
            "description": "在工作台显示实时天气信息",
        }

        errors = validate_manifest_dict(manifest_dict)
        assert errors == [], f"validation errors: {errors}"

        manifest = PluginManifest.from_dict(manifest_dict)
        assert manifest.id == "com.example.weather-card"
        assert manifest.type == "card"

        # Can't actually create a CardPlugin without a real render() for testing
        # but we can verify the manifest is valid and the registry accepts it
        from plugins.base import CardPlugin

        class WeatherCardPlugin(CardPlugin):
            def render(self, state):
                return f"Weather: {state.get('city', 'auto')}"

        registry = PluginRegistry()
        weather_plugin = WeatherCardPlugin(manifest)
        plugin_id = registry.install(manifest, plugin_instance=weather_plugin)
        assert plugin_id == "com.example.weather-card"

        plugin = registry.get(plugin_id)
        assert plugin is not None

        # Verify health
        health = registry.health_check(plugin_id)
        assert health.status == "healthy"

