"""Plugin registry — discovery, registration, lifecycle management.

Implements the PluginRegistry interface defined in docs/plugin-sdk-spec.md
and integrates with the existing CapabilityRegistry.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from core.app_version import get_app_version

from .base import (
    PLUGIN_TYPE_MAP,
    BasePlugin,
    PluginHealth,
    PluginState,
)
from .manifest import (
    ManifestValidationError,
    PluginManifest,
    validate_manifest_dict,
)
from .permissions import PermissionManager

logger = logging.getLogger("siyi.plugin.registry")


class PluginRegistry:
    """Global plugin registry managing all installed plugins.

    Supports install, enable, disable, uninstall workflows and
    integrates with the capability registry for capability discovery.
    """

    def __init__(
        self,
        capability_registry: Any = None,
        permission_manager: PermissionManager | None = None,
    ) -> None:
        self._plugins: dict[str, BasePlugin] = {}
        self._manifests: dict[str, PluginManifest] = {}
        self._configs: dict[str, dict[str, Any]] = {}
        self._health_cache: dict[str, PluginHealth] = {}
        self._install_times: dict[str, str] = {}

        self.capability_registry = capability_registry
        self.permission_manager = permission_manager or PermissionManager()

    # ── Installation ──────────────────────────────────────────────────

    def install(
        self,
        manifest: PluginManifest,
        plugin_instance: BasePlugin | None = None,
    ) -> str:
        """Install a plugin from its manifest.

        If *plugin_instance* is provided, it is used directly; otherwise the
        registry instantiates the base class for the plugin type.

        Returns the plugin ID on success. Raises ManifestValidationError or
        ValueError on failure.
        """
        # Validate manifest
        errors = validate_manifest_dict(manifest.to_dict())
        if errors:
            raise ManifestValidationError(errors)

        plugin_id = manifest.id

        # Check for duplicate install
        if plugin_id in self._plugins:
            raise ValueError(f"plugin already installed: {plugin_id}")

        # Check min/max app version
        app_ver = get_app_version()
        min_ver = manifest.min_app_version
        if min_ver and not _check_version_gte(app_ver, min_ver):
            raise ValueError(f"plugin requires app version >= {min_ver}, but current is {app_ver}")

        # Check dependencies
        for dep_id in manifest.dependencies:
            if dep_id not in self._plugins:
                logger.warning(
                    "plugin %s depends on %s which is not installed",
                    plugin_id,
                    dep_id,
                )

        # Obtain plugin instance
        if plugin_instance is not None:
            plugin = plugin_instance
        else:
            plugin_cls = PLUGIN_TYPE_MAP.get(manifest.type)
            if plugin_cls is None:
                raise ValueError(f"unsupported plugin type: {manifest.type}")
            plugin = plugin_cls(manifest)

        # Register permissions
        self.permission_manager.declare_permissions(plugin_id, list(manifest.permissions))

        # Store
        self._plugins[plugin_id] = plugin
        self._manifests[plugin_id] = manifest
        self._configs[plugin_id] = {}
        self._install_times[plugin_id] = datetime.now(timezone.utc).isoformat()
        self._health_cache[plugin_id] = PluginHealth.healthy()

        # Lifecycle hook
        try:
            plugin.on_install()
        except Exception as exc:
            logger.error("plugin %s on_install failed: %s", plugin_id, exc)
            plugin._record_error(str(exc))
            self._plugins.pop(plugin_id, None)
            self._manifests.pop(plugin_id, None)
            self._configs.pop(plugin_id, None)
            self._install_times.pop(plugin_id, None)
            self._health_cache.pop(plugin_id, None)
            self.permission_manager.remove_plugin(plugin_id)
            raise RuntimeError(f"plugin {plugin_id} install failed: {exc}") from exc

        logger.info("plugin installed: %s v%s", plugin_id, manifest.version)
        return plugin_id

    # ── Lifecycle ────────────────────────────────────────────────────

    def enable(self, plugin_id: str) -> None:
        """Enable an installed plugin.

        Calls ``on_enable()`` and transitions state to ENABLED.
        Registers capabilities if a capability_registry is set.
        """
        plugin = self._get_plugin(plugin_id)
        if plugin.state in (PluginState.ENABLED, PluginState.ACTIVE):
            logger.debug("plugin already enabled: %s", plugin_id)
            return

        try:
            plugin.on_enable()
        except Exception as exc:
            plugin.state = PluginState.ERROR
            plugin._record_error(str(exc))
            logger.error("plugin %s on_enable failed: %s", plugin_id, exc)
            raise RuntimeError(f"plugin {plugin_id} enable failed: {exc}") from exc

        plugin.state = PluginState.ENABLED
        self._sync_capabilities(plugin_id, register=True)
        logger.info("plugin enabled: %s", plugin_id)

    def disable(self, plugin_id: str) -> None:
        """Disable an enabled plugin.

        Calls ``on_disable()`` and transitions state to DISABLED.
        Unregisters capabilities from the capability_registry.
        """
        plugin = self._get_plugin(plugin_id)
        if plugin.state == PluginState.DISABLED:
            return

        try:
            plugin.on_disable()
        except Exception as exc:
            logger.error("plugin %s on_disable failed: %s", plugin_id, exc)
            # Continue with disable even if hook fails

        plugin.state = PluginState.DISABLED
        self._sync_capabilities(plugin_id, register=False)
        logger.info("plugin disabled: %s", plugin_id)

    def uninstall(self, plugin_id: str, keep_data: bool = False) -> None:
        """Uninstall a plugin.

        Calls ``on_uninstall()``, then removes all state.
        If ``keep_data`` is True, persistent data is left on disk.
        """
        plugin = self._get_plugin(plugin_id)

        # Disable first if enabled
        if plugin.state in (PluginState.ENABLED, PluginState.ACTIVE):
            self.disable(plugin_id)

        try:
            plugin.on_uninstall()
        except Exception as exc:
            logger.error("plugin %s on_uninstall failed: %s", plugin_id, exc)

        # Clean up registry state
        self._plugins.pop(plugin_id, None)
        self._manifests.pop(plugin_id, None)
        self._configs.pop(plugin_id, None)
        self._install_times.pop(plugin_id, None)
        self._health_cache.pop(plugin_id, None)
        self.permission_manager.remove_plugin(plugin_id)

        logger.info("plugin uninstalled: %s (keep_data=%s)", plugin_id, keep_data)

    # ── Query ─────────────────────────────────────────────────────────

    def get(self, plugin_id: str) -> BasePlugin | None:
        """Get a plugin instance by ID."""
        return self._plugins.get(plugin_id)

    def get_manifest(self, plugin_id: str) -> PluginManifest | None:
        """Get the manifest for a plugin by ID."""
        return self._manifests.get(plugin_id)

    def list_plugins(
        self,
        include_disabled: bool = False,
    ) -> list[BasePlugin]:
        """List all installed plugins, optionally including disabled ones."""
        if include_disabled:
            return list(self._plugins.values())
        return [p for p in self._plugins.values() if p.state not in (PluginState.DISABLED, PluginState.ERROR)]

    def list_by_type(
        self,
        plugin_type: str,
        include_disabled: bool = False,
    ) -> list[BasePlugin]:
        """List plugins of a specific type."""
        return [p for p in self.list_plugins(include_disabled=include_disabled) if p.manifest.type == plugin_type]

    def list_by_capability(
        self,
        capability: str,
        include_disabled: bool = False,
    ) -> list[BasePlugin]:
        """List plugins that declare a specific capability."""
        return [p for p in self.list_plugins(include_disabled=include_disabled) if capability in p.manifest.capabilities]

    def count_by_type(self) -> dict[str, int]:
        """Return a dict mapping plugin type → count (only enabled)."""
        counts: dict[str, int] = {}
        for p in self.list_plugins():
            t = p.manifest.type
            counts[t] = counts.get(t, 0) + 1
        return counts

    # ── Configuration ─────────────────────────────────────────────────

    def get_config(self, plugin_id: str) -> dict[str, Any]:
        """Get the current configuration for a plugin."""
        config = self._configs.get(plugin_id)
        if config is None:
            config = {}
            self._configs[plugin_id] = config
        return dict(config)

    def set_config(self, plugin_id: str, config: dict[str, Any]) -> None:
        """Update the configuration for a plugin."""
        plugin = self._get_plugin(plugin_id)
        merged = dict(self._configs.get(plugin_id, {}))
        merged.update(config)
        self._configs[plugin_id] = merged
        plugin._config = merged

    # ── Health ────────────────────────────────────────────────────────

    def health_check(self, plugin_id: str) -> PluginHealth:
        """Perform a health check on a specific plugin."""
        plugin = self._get_plugin(plugin_id)
        try:
            health = plugin.health_check()
        except Exception as exc:
            logger.exception("插件 %s 健康检查失败", plugin_id)
            health = PluginHealth.down(str(exc), error_count=1)
        self._health_cache[plugin_id] = health
        return health

    def health_check_all(self) -> dict[str, PluginHealth]:
        """Perform health checks on all plugins."""
        results: dict[str, PluginHealth] = {}
        for plugin_id in list(self._plugins.keys()):
            results[plugin_id] = self.health_check(plugin_id)
        return results

    def get_health_summary(self) -> dict[str, Any]:
        """Get a summary of overall plugin health."""
        total = len(self._plugins)
        healthy = 0
        degraded = 0
        down = 0
        for pid in self._plugins:
            h = self._health_cache.get(pid)
            if h is None:
                healthy += 1
            elif h.status == "healthy":
                healthy += 1
            elif h.status == "degraded":
                degraded += 1
            else:
                down += 1
        return {
            "total": total,
            "healthy": healthy,
            "degraded": degraded,
            "down": down,
        }

    # ── Capability registry integration ───────────────────────────────

    def _sync_capabilities(self, plugin_id: str, register: bool) -> None:
        """Register or unregister plugin capabilities with the capability registry."""
        if self.capability_registry is None:
            return
        manifest = self._manifests.get(plugin_id)
        if manifest is None:
            return

        try:
            from services.capability_types import CapabilitySpec

            for cap in manifest.capabilities:
                cap_id = f"plugin.{plugin_id}.{cap}"
                if register:
                    spec = CapabilitySpec(
                        capability_id=cap_id,
                        description=f"Plugin {manifest.name}: {cap}",
                        risk_level="low",
                        group="plugin",
                        source=f"plugin.{plugin_id}",
                        enabled=True,
                    )
                    self.capability_registry.register_capability(spec)
                else:
                    existing = self.capability_registry.get_capability(cap_id)
                    if existing is not None:
                        self.capability_registry.enable_capability(cap_id, False)
        except ImportError:
            logger.debug("CapabilityRegistry not available, skipping cap sync")

    def sync_all_to_capability_registry(self) -> None:
        """Sync all enabled plugins' capabilities into the capability registry."""
        for plugin_id in self._plugins:
            plugin = self._plugins[plugin_id]
            if plugin.state == PluginState.ENABLED:
                self._sync_capabilities(plugin_id, register=True)

    # ── Registry summary ──────────────────────────────────────────────

    def describe(self) -> dict[str, Any]:
        """Return a full description of the registry state."""
        return {
            "plugins": [
                {
                    "id": pid,
                    "name": m.name,
                    "version": m.version,
                    "type": m.type,
                    "state": self._plugins[pid].state.value,
                    "capabilities": list(m.capabilities),
                    "permissions": list(m.permissions),
                    "installed_at": self._install_times.get(pid, ""),
                }
                for pid, m in self._manifests.items()
            ],
            "counts": self.count_by_type(),
            "health": self.get_health_summary(),
        }

    # ── Internal helpers ──────────────────────────────────────────────

    def _get_plugin(self, plugin_id: str) -> BasePlugin:
        """Get a plugin by ID or raise KeyError."""
        plugin = self._plugins.get(plugin_id)
        if plugin is None:
            raise KeyError(f"plugin not found: {plugin_id}")
        return plugin

    # ── Auto-discovery ────────────────────────────────────────────────

    def discover_plugins(self, plugins_dir: str | None = None) -> list[str]:
        """扫描插件目录，自动发现并安装插件。

        扫描 `plugins_dir`（默认为应用根目录下的 `plugins/installed/`）下的
        子目录，每个子目录应包含 `plugin.json` 清单文件。清单文件格式见
        PluginManifest。发现后自动调用 install() 安装。

        Args:
            plugins_dir: 插件目录路径，默认为 <app_root>/plugins/installed/

        Returns:
            成功安装的 plugin_id 列表
        """
        import importlib
        import os
        import sys

        from .manifest import parse_manifest_file

        if plugins_dir is None:
            # 默认路径：思忆集app/plugins/installed/
            app_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            plugins_dir = os.path.join(app_root, "plugins", "installed")

        if not os.path.isdir(plugins_dir):
            logger.debug("插件目录不存在: %s", plugins_dir)
            return []

        installed_ids: list[str] = []

        for entry in os.listdir(plugins_dir):
            entry_path = os.path.join(plugins_dir, entry)
            if not os.path.isdir(entry_path):
                continue

            manifest_path = os.path.join(entry_path, "plugin.json")
            if not os.path.isfile(manifest_path):
                continue

            try:
                manifest = parse_manifest_file(manifest_path)
            except Exception as exc:
                logger.warning("插件清单解析失败 %s: %s", entry, exc)
                continue

            # 已安装则跳过
            if manifest.id in self._plugins:
                continue

            # 尝试加载插件模块
            plugin_instance: BasePlugin | None = None
            module_attr = getattr(manifest, "entry_module", None) or f"{entry}.plugin"
            class_attr = getattr(manifest, "entry_class", None) or "Plugin"

            try:
                # 将插件目录加入 sys.path 以便 import
                if plugins_dir not in sys.path:
                    sys.path.insert(0, plugins_dir)
                module = importlib.import_module(module_attr)
                plugin_cls = getattr(module, class_attr, None)
                if plugin_cls and issubclass(plugin_cls, BasePlugin):
                    plugin_instance = plugin_cls()
            except Exception as exc:
                logger.warning("插件模块加载失败 %s: %s", entry, exc)
                # 模块加载失败时仍可安装（使用基类占位）
                plugin_instance = None

            try:
                plugin_id = self.install(manifest, plugin_instance)
                installed_ids.append(plugin_id)
                logger.info("自动发现并安装插件: %s", plugin_id)
            except Exception as exc:
                logger.warning("插件安装失败 %s: %s", entry, exc)

        return installed_ids


# ── 全局生命周期注册表单例 ─────────────────────────────────────────────

_lifecycle_registry: "PluginRegistry | None" = None


def get_lifecycle_registry() -> "PluginRegistry":
    """返回全局生命周期插件注册表单例。

    首次调用时扫描 ``plugins/installed/`` 并安装发现的插件，之后复用
    同一实例（幂等，避免 AppContext 装配与 CapabilityRouter 各扫描一遍
    导致重复 install / 重复 import）。测试如需隔离可直接 new PluginRegistry()。
    """
    global _lifecycle_registry
    if _lifecycle_registry is None:
        _lifecycle_registry = PluginRegistry()
        _lifecycle_registry.discover_plugins()
    return _lifecycle_registry


def _check_version_gte(current: str, minimum: str) -> bool:
    """Check that a semver version is >= minimum.

    Simple tuple-based comparison; does not handle pre-release labels.
    """
    try:
        cur_parts = [int(x) for x in current.split(".")[:3]]
        min_parts = [int(x) for x in minimum.split(".")[:3]]
    except (ValueError, IndexError):
        return False
    return tuple(cur_parts) >= tuple(min_parts)
