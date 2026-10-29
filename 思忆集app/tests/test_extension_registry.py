from __future__ import annotations

import sys
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from core.extension_registry import (
    ExtensionAuditEntry,
    ExtensionPermission,
    ExtensionRegistry,
    ExtensionSource,
    ExtensionSourceType,
)


class _FakeDataStore:
    """简化版 data_store 用于测试。"""

    def __init__(self):
        self._storage: dict[str, object] = {}

    def load_json(self, key: str, default: object = None) -> object:
        return self._storage.get(key, default)

    def save_json(self, key: str, value: object) -> None:
        self._storage[key] = value

    def update_json(self, key: str, default: object, updater) -> object:
        current = self.load_json(key, default)
        new_value = updater(current)
        self.save_json(key, new_value)
        return new_value


class _FakeAuditTrail:
    """简化版审计轨迹用于测试。"""

    def __init__(self):
        self.intents = []

    def record_write_intent(self, *, actor="", action_id="", target_type="", target_id="", operation="write", before_snapshot=None, metadata=None):
        eid = f"audit_{len(self.intents)}"
        self.intents.append({"entry_id": eid, "actor": actor, "action_id": action_id, "status": "intent"})
        return eid

    def record_write_complete(self, entry_id, after_snapshot=None):
        for entry in self.intents:
            if entry["entry_id"] == entry_id:
                entry["status"] = "completed"
                return True
        return False

    def record_write_failed(self, entry_id, error=""):
        for entry in self.intents:
            if entry["entry_id"] == entry_id:
                entry["status"] = "failed"
                return True
        return False


# ── 注册与查询 ─────────────────────────────────────────────


def test_registry_creates_builtin_sources():
    registry = ExtensionRegistry()
    sources = registry.list_sources()
    source_ids = {s["source_id"] for s in sources}
    assert "builtin.web_search" in source_ids
    assert "builtin.web_fetch" in source_ids
    assert "builtin.browser_bridge" in source_ids
    assert "builtin.mcp_provider" in source_ids
    assert "builtin.news_provider" in source_ids


def test_register_custom_source():
    registry = ExtensionRegistry()
    source = ExtensionSource(
        source_id="custom.test_source",
        name="测试来源",
        source_type=ExtensionSourceType.CUSTOM.value,
        trust_level="community",
        tier="info-only",
        description="用于测试的自定义来源",
    )
    result = registry.register_source(source, auto_persist=False)
    assert result["registered"] is True

    fetched = registry.get_source("custom.test_source")
    assert fetched is not None
    assert fetched.name == "测试来源"
    assert fetched.tier == "info-only"
    assert fetched.permissions == {"read"}


def test_list_sources_enabled_only():
    registry = ExtensionRegistry()
    # 禁用其中一个内置来源
    registry.set_enabled("builtin.web_search", False, auto_persist=False)
    all_sources = registry.list_sources()
    enabled_sources = registry.list_sources(enabled_only=True)
    assert len(enabled_sources) < len(all_sources)
    for src in enabled_sources:
        assert src["enabled"] is True


def test_list_sources_by_type():
    registry = ExtensionRegistry()
    search_sources = registry.list_sources(source_type=ExtensionSourceType.SEARCH.value)
    assert all(s["source_type"] == ExtensionSourceType.SEARCH.value for s in search_sources)


def test_unregister_source():
    registry = ExtensionRegistry()
    registry.register_source(
        ExtensionSource(source_id="test.temp", name="临时来源", source_type=ExtensionSourceType.CUSTOM.value),
        auto_persist=False,
    )
    assert registry.get_source("test.temp") is not None

    result = registry.unregister_source("test.temp", auto_persist=False)
    assert result["unregistered"] is True
    assert registry.get_source("test.temp") is None


def test_unregister_nonexistent_source():
    registry = ExtensionRegistry()
    result = registry.unregister_source("test.nonexistent")
    assert result["unregistered"] is False
    assert result["reason"] == "not_found"


# ── 权限层级 ───────────────────────────────────────────────


def test_permission_tier_info_only():
    source = ExtensionSource(
        source_id="test.info",
        name="仅信息",
        source_type=ExtensionSourceType.CUSTOM.value,
        tier="info-only",
    )
    assert source.permissions == {"read"}
    assert not source.has_act_permission


def test_permission_tier_info_suggest():
    source = ExtensionSource(
        source_id="test.suggest",
        name="信息+建议",
        source_type=ExtensionSourceType.CUSTOM.value,
        tier="info+suggest",
    )
    assert source.permissions == {"read", "suggest"}
    assert not source.has_act_permission


def test_permission_tier_trigger_action():
    source = ExtensionSource(
        source_id="test.act",
        name="触发动作",
        source_type=ExtensionSourceType.CUSTOM.value,
        tier="trigger-action",
    )
    assert source.permissions == {"read", "suggest", "act"}
    assert source.has_act_permission


def test_set_tier():
    registry = ExtensionRegistry()
    source = ExtensionSource(
        source_id="test.tier_change",
        name="层级变更测试",
        source_type=ExtensionSourceType.CUSTOM.value,
        tier="info-only",
    )
    registry.register_source(source, auto_persist=False)

    result = registry.set_tier("test.tier_change", "trigger-action", auto_persist=False)
    assert result["success"] is True
    assert result["tier"] == "trigger-action"
    assert "act" in result["permissions"]

    updated = registry.get_source("test.tier_change")
    assert updated is not None
    assert updated.has_act_permission


def test_set_invalid_tier():
    registry = ExtensionRegistry()
    source = ExtensionSource(
        source_id="test.invalid_tier",
        name="无效层级",
        source_type=ExtensionSourceType.CUSTOM.value,
    )
    registry.register_source(source, auto_persist=False)
    result = registry.set_tier("test.invalid_tier", "super_admin", auto_persist=False)
    assert result["success"] is False
    assert "invalid_tier" in result["reason"]


def test_has_permission():
    registry = ExtensionRegistry()
    source = ExtensionSource(
        source_id="test.perm_check",
        name="权限检查",
        source_type=ExtensionSourceType.CUSTOM.value,
        tier="info+suggest",
    )
    registry.register_source(source, auto_persist=False)
    assert registry.has_permission("test.perm_check", "read") is True
    assert registry.has_permission("test.perm_check", "suggest") is True
    assert registry.has_permission("test.perm_check", "act") is False


def test_get_effective_permissions_returns_empty_when_disabled():
    registry = ExtensionRegistry()
    source = ExtensionSource(
        source_id="test.disabled_perm",
        name="禁用后无权限",
        source_type=ExtensionSourceType.CUSTOM.value,
    )
    registry.register_source(source, auto_persist=False)
    registry.set_enabled("test.disabled_perm", False, auto_persist=False)
    perms = registry.get_effective_permissions("test.disabled_perm")
    assert perms == set()


# ── 启停控制 ───────────────────────────────────────────────


def test_set_enabled():
    registry = ExtensionRegistry()
    result = registry.set_enabled("builtin.web_search", False, auto_persist=False)
    assert result["success"] is True
    assert result["enabled"] is False
    assert registry.is_enabled("builtin.web_search") is False

    result = registry.set_enabled("builtin.web_search", True, auto_persist=False)
    assert result["enabled"] is True
    assert registry.is_enabled("builtin.web_search") is True


def test_set_enabled_nonexistent_source():
    registry = ExtensionRegistry()
    result = registry.set_enabled("test.nonexistent", False)
    assert result["success"] is False
    assert result["reason"] == "not_found"


def test_is_enabled_returns_false_for_unknown_source():
    registry = ExtensionRegistry()
    assert registry.is_enabled("test.unknown") is False


# ── 健康追踪 ───────────────────────────────────────────────


def test_update_health():
    registry = ExtensionRegistry()
    result = registry.update_health("builtin.web_search", "healthy", auto_persist=False)
    assert result["success"] is True
    assert result["health_status"] == "healthy"

    source = registry.get_source("builtin.web_search")
    assert source is not None
    assert source.health_status == "healthy"
    assert source.last_health_check != ""


def test_update_health_nonexistent():
    registry = ExtensionRegistry()
    result = registry.update_health("test.nonexistent", "healthy")
    assert result["success"] is False


def test_list_healthy_sources():
    registry = ExtensionRegistry()
    # 禁用并标记一个来源为 down
    registry.set_enabled("builtin.web_search", True, auto_persist=False)
    registry.update_health("builtin.mcp_provider", "down", auto_persist=False)

    healthy = registry.list_healthy_sources()
    healthy_ids = {s["source_id"] for s in healthy}
    assert "builtin.web_search" in healthy_ids
    assert "builtin.mcp_provider" not in healthy_ids


def test_list_healthy_sources_with_min_permissions():
    registry = ExtensionRegistry()
    healthy = registry.list_healthy_sources(min_permissions={"read"})
    assert len(healthy) > 0


# ── Planner 集成 ───────────────────────────────────────────


def test_get_enabled_source_ids():
    registry = ExtensionRegistry()
    registry.set_enabled("builtin.web_search", False, auto_persist=False)
    enabled = registry.get_enabled_source_ids()
    assert "builtin.web_search" not in enabled
    assert "builtin.web_fetch" in enabled


def test_get_disabled_source_ids():
    registry = ExtensionRegistry()
    registry.set_enabled("builtin.web_search", False, auto_persist=False)
    disabled = registry.get_disabled_source_ids()
    assert "builtin.web_search" in disabled
    assert "builtin.web_fetch" not in disabled


def test_disabling_source_prevents_planner_selection():
    """验证禁用某来源后，planner 不再选择它（通过 get_disabled_source_ids 检测）。"""
    registry = ExtensionRegistry()
    # 初始所有来源都启用
    assert "builtin.web_search" not in registry.get_disabled_source_ids()

    # 禁用后应出现在禁用集合中
    registry.set_enabled("builtin.web_search", False, auto_persist=False)
    assert "builtin.web_search" in registry.get_disabled_source_ids()

    # 重新启用后应移出禁用集合
    registry.set_enabled("builtin.web_search", True, auto_persist=False)
    assert "builtin.web_search" not in registry.get_disabled_source_ids()


def test_is_source_available_for_action():
    registry = ExtensionRegistry()
    assert registry.is_source_available_for_action("builtin.web_search", "web.search") is True

    registry.set_enabled("builtin.web_search", False, auto_persist=False)
    assert registry.is_source_available_for_action("builtin.web_search", "web.search") is False

    registry.set_enabled("builtin.web_search", True, auto_persist=False)
    registry.update_health("builtin.web_search", "down", auto_persist=False)
    assert registry.is_source_available_for_action("builtin.web_search", "web.search") is False


# ── 审计追踪 ───────────────────────────────────────────────


def test_audit_log_tracks_operations():
    registry = ExtensionRegistry()
    registry.set_enabled("builtin.web_search", False, auto_persist=False)
    registry.set_enabled("builtin.web_search", True, auto_persist=False)
    registry.set_tier("builtin.web_search", "info-only", auto_persist=False)
    registry.update_health("builtin.web_search", "degraded", auto_persist=False)

    log = registry.get_audit_log()
    operations = [e["operation"] for e in log]
    assert "disable" in operations
    assert "enable" in operations
    assert "tier_change" in operations
    assert "health_update" in operations


def test_audit_log_filter_by_source():
    registry = ExtensionRegistry()
    registry.set_enabled("builtin.web_search", False, auto_persist=False)
    registry.set_enabled("builtin.web_fetch", False, auto_persist=False)

    web_search_log = registry.get_audit_log(source_id="builtin.web_search")
    assert all(e["source_id"] == "builtin.web_search" for e in web_search_log)
    assert len(web_search_log) == 1


def test_registration_creates_audit_entry():
    registry = ExtensionRegistry()
    source = ExtensionSource(
        source_id="test.audit_register",
        name="审计注册测试",
        source_type=ExtensionSourceType.CUSTOM.value,
    )
    registry.register_source(source, auto_persist=False)
    log = registry.get_audit_log()
    register_entries = [e for e in log if e["operation"] == "register"]
    assert any(e["source_id"] == "test.audit_register" for e in register_entries)


# ── 持久化 ─────────────────────────────────────────────────


def test_persistence_round_trip():
    data_store = _FakeDataStore()
    registry = ExtensionRegistry(data_store=data_store)
    registry.register_source(
        ExtensionSource(
            source_id="test.custom_persist",
            name="持久化测试",
            source_type=ExtensionSourceType.CUSTOM.value,
            tier="info+suggest",
        ),
        auto_persist=True,
    )
    registry.set_enabled("builtin.web_search", False, auto_persist=True)

    # 创建新实例，应该从 data_store 恢复状态
    registry2 = ExtensionRegistry(data_store=data_store)
    assert registry2.get_source("test.custom_persist") is not None
    assert registry2.is_enabled("builtin.web_search") is False


# ── 回滚快照 ───────────────────────────────────────────────


def test_rollback_snapshot_and_restore():
    data_store = _FakeDataStore()
    registry = ExtensionRegistry(data_store=data_store)

    # 做一些变更
    registry.set_enabled("builtin.web_search", False, auto_persist=True)
    registry.update_health("builtin.mcp_provider", "down", auto_persist=True)

    # 拍快照
    snapshot = registry.get_rollback_snapshot()
    assert "builtin.web_search" in snapshot
    assert snapshot["builtin.web_search"]["enabled"] is False

    # 改回来
    registry.set_enabled("builtin.web_search", True, auto_persist=True)
    registry.update_health("builtin.mcp_provider", "healthy", auto_persist=True)
    assert registry.is_enabled("builtin.web_search") is True

    # 从快照恢复
    registry.restore_from_snapshot(snapshot)
    assert registry.is_enabled("builtin.web_search") is False
    mcp = registry.get_source("builtin.mcp_provider")
    assert mcp is not None
    assert mcp.health_status == "down"


# ── 审计轨迹集成 ─────────────────────────────────────────


def test_audit_trail_integration_on_enable_disable():
    audit = _FakeAuditTrail()
    registry = ExtensionRegistry(audit_trail=audit)
    registry.set_enabled("builtin.web_search", False, auto_persist=False)
    assert len(audit.intents) >= 1
    assert any(e["action_id"] == "extension.disable" for e in audit.intents)

    registry.set_enabled("builtin.web_search", True, auto_persist=False)
    assert any(e["action_id"] == "extension.enable" for e in audit.intents)


# ── 权限模型 ────────────────────────────────────────────────


def test_high_risk_permissions_default_off():
    """高风险权限 (act) 默认关闭，需要用户显式启用。"""
    source = ExtensionSource(
        source_id="test.default_high_risk",
        name="默认高风险测试",
        source_type=ExtensionSourceType.CUSTOM.value,
        tier=_DEFAULT_TIER,
    )
    assert ExtensionPermission.ACT.value not in source.permissions


_DEFAULT_TIER = "info+suggest"
