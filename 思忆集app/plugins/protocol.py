from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

PLUGIN_EXTENSION_CARD = "workbench_card"
PLUGIN_EXTENSION_QUICK_ACTION = "quick_action"
PLUGIN_EXTENSION_FLOATING_ENTRY = "floating_assistant"
PLUGIN_EXTENSION_DATA_SOURCE = "data_source"


class ExternalCapabilityProvider(Protocol):
    """外部能力接入标准接口。"""

    name: str
    version: str
    source_type: str = "api_provider"
    trust_level: str = "unknown"

    def health(self) -> dict[str, Any]:
        ...

    def invoke(self, action: str, payload: dict[str, Any]) -> dict[str, Any]:
        ...


@dataclass
class CardPluginSpec:
    plugin_id: str
    name: str
    version: str
    namespace: str
    description: str = ""
    default_visible: bool = True
    render_factory: Callable[..., Any] | None = None
    actions: list[str] = field(default_factory=list)


@dataclass
class QuickActionPluginSpec:
    plugin_id: str
    name: str
    version: str
    action_id: str
    description: str = ""
    enabled_by_default: bool = True
    invoke: Callable[..., Any] | None = None


@dataclass
class FloatingAssistantPluginSpec:
    plugin_id: str
    name: str
    version: str
    entry_id: str
    description: str = ""
    priority: int = 100
    render_factory: Callable[..., Any] | None = None


@dataclass
class DataSourcePluginSpec:
    plugin_id: str
    name: str
    version: str
    source_id: str
    description: str = ""
    sync_modes: list[str] = field(default_factory=list)
    pull_handler: Callable[..., Any] | None = None


@dataclass(frozen=True)
class PluginExtensionPoint:
    extension_type: str
    display_name: str
    description: str


class ExtensionPointRegistry:
    """扩展点注册中心：统一约束内部插件扩展点。

    命名说明：此类原名为 PluginRegistry，与 plugins/registry.py 中的
    PluginRegistry（管理 BasePlugin 生命周期）重名。为避免混淆，重命名为
    ExtensionPointRegistry，更准确反映其职责（注册 Card/QuickAction/
    FloatingAssistant/DataSource 等扩展点 Spec）。

    向后兼容：保留 PluginRegistry 作为别名，现有代码无需修改。
    """

    def __init__(self):
        self._card_plugins: dict[str, CardPluginSpec] = {}
        self._quick_action_plugins: dict[str, QuickActionPluginSpec] = {}
        self._floating_plugins: dict[str, FloatingAssistantPluginSpec] = {}
        self._data_source_plugins: dict[str, DataSourcePluginSpec] = {}
        self._capability_providers: dict[str, ExternalCapabilityProvider] = {}

    @staticmethod
    def _validate_spec(spec, required_fields: list[str]) -> None:
        """Validate that a plugin spec has all required non-empty fields.

        Raises ValueError if validation fails.
        """
        for field in required_fields:
            value = getattr(spec, field, None)
            if not value:
                raise ValueError(f"Plugin spec missing required field: {field}")
        plugin_id = getattr(spec, "plugin_id", None) or getattr(spec, "source_id", None)
        if plugin_id and len(str(plugin_id)) < 2:
            raise ValueError(f"Plugin id must be at least 2 characters: {plugin_id}")

    def register_card_plugin(self, spec: CardPluginSpec):
        self._validate_spec(spec, ["plugin_id", "name", "version", "namespace"])
        self._card_plugins[spec.plugin_id] = spec

    def list_card_plugins(self) -> list[CardPluginSpec]:
        return list(self._card_plugins.values())

    def register_quick_action_plugin(self, spec: QuickActionPluginSpec):
        self._validate_spec(spec, ["plugin_id", "name", "version", "action_id"])
        self._quick_action_plugins[spec.plugin_id] = spec

    def list_quick_action_plugins(self) -> list[QuickActionPluginSpec]:
        return list(self._quick_action_plugins.values())

    def register_floating_assistant_plugin(self, spec: FloatingAssistantPluginSpec):
        self._validate_spec(spec, ["plugin_id", "name", "version", "entry_id"])
        self._floating_plugins[spec.plugin_id] = spec

    def list_floating_assistant_plugins(self) -> list[FloatingAssistantPluginSpec]:
        return list(self._floating_plugins.values())

    def register_data_source_plugin(self, spec: DataSourcePluginSpec):
        self._validate_spec(spec, ["plugin_id", "name", "version", "source_id"])
        self._data_source_plugins[spec.plugin_id] = spec

    def list_data_source_plugins(self) -> list[DataSourcePluginSpec]:
        return list(self._data_source_plugins.values())

    def register_capability_provider(self, provider: ExternalCapabilityProvider):
        key = f"{provider.name}:{provider.version}"
        self._capability_providers[key] = provider

    def list_capability_providers(self) -> list[ExternalCapabilityProvider]:
        return list(self._capability_providers.values())

    def register_lifecycle_plugin(self, plugin: Any) -> str | None:
        """把生命周期插件（plugins.registry.BasePlugin 实例）桥接为扩展点 spec。

        根据插件的 manifest.type 映射到对应扩展点：
        card → CardPluginSpec / datasource → DataSourcePluginSpec /
        action → QuickActionPluginSpec / companion → FloatingAssistantPluginSpec。
        tool 类型没有 UI 扩展点，返回 None（由工具桥单独注册）。

        Args:
            plugin: plugins.registry.PluginRegistry 管理的插件实例。

        Returns:
            注册成功的 spec 键（plugin_id / source_id），无匹配扩展点时返回 None。
        """
        manifest = getattr(plugin, "manifest", None)
        if manifest is None:
            return None
        plugin_id = str(manifest.id)
        name = str(manifest.name or plugin_id)
        version = str(manifest.version or "0.1.0")
        description = str(manifest.description or "")
        ptype = str(manifest.type)

        if ptype == "card":
            card_spec = CardPluginSpec(
                plugin_id=plugin_id,
                name=name,
                version=version,
                namespace=f"card.{plugin_id}",
                description=description,
            )
            self.register_card_plugin(card_spec)
            return plugin_id
        if ptype == "datasource":
            source_id = str(getattr(plugin, "source_id", None) or plugin_id)
            ds_spec = DataSourcePluginSpec(
                plugin_id=plugin_id,
                name=name,
                version=version,
                source_id=source_id,
                description=description,
            )
            self.register_data_source_plugin(ds_spec)
            return plugin_id
        if ptype == "action":
            action_id = str(getattr(plugin, "action_id", None) or plugin_id)
            qa_spec = QuickActionPluginSpec(
                plugin_id=plugin_id,
                name=name,
                version=version,
                action_id=action_id,
                description=description,
            )
            self.register_quick_action_plugin(qa_spec)
            return plugin_id
        if ptype == "companion":
            entry_id = str(getattr(plugin, "companion_id", None) or plugin_id)
            fa_spec = FloatingAssistantPluginSpec(
                plugin_id=plugin_id,
                name=name,
                version=version,
                entry_id=entry_id,
                description=description,
            )
            self.register_floating_assistant_plugin(fa_spec)
            return plugin_id
        return None

    def sync_to_extension_registry(self, extension_registry) -> list[dict[str, Any]]:
        """把当前注册的 capability provider 同步到 ExtensionRegistry。"""
        from core.extension_registry import ExtensionSource, ExtensionSourceType

        results = []
        for provider in self.list_capability_providers():
            source_id = f"provider.{provider.name}"
            existing = extension_registry.get_source(source_id)
            if existing is not None:
                continue
            source_type = getattr(provider, "source_type", ExtensionSourceType.API_PROVIDER.value)
            trust_level = getattr(provider, "trust_level", "unknown")
            source = ExtensionSource(
                source_id=source_id,
                name=provider.name,
                source_type=source_type,
                trust_level=trust_level,
                tier="info+suggest",
                description=f"外部能力提供者: {provider.name} v{provider.version}",
                version=provider.version,
            )
            result = extension_registry.register_source(source)
            results.append(result)
        return results

    def describe_extension_points(self) -> list[PluginExtensionPoint]:
        return [
            PluginExtensionPoint(
                extension_type=PLUGIN_EXTENSION_CARD,
                display_name="首页卡片",
                description="工作台卡片的显示、默认可见性和交互入口。",
            ),
            PluginExtensionPoint(
                extension_type=PLUGIN_EXTENSION_QUICK_ACTION,
                display_name="快捷动作",
                description="主窗口和助手区域的高频命令入口。",
            ),
            PluginExtensionPoint(
                extension_type=PLUGIN_EXTENSION_FLOATING_ENTRY,
                display_name="悬浮助手扩展",
                description="悬浮助手的额外入口、提示或微面板。",
            ),
            PluginExtensionPoint(
                extension_type=PLUGIN_EXTENSION_DATA_SOURCE,
                display_name="数据源接入",
                description="外部内容源的拉取、解析和同步适配。",
            ),
        ]

    def describe_contract(self) -> dict[str, Any]:
        extension_points = self.describe_extension_points()
        return {
            "extension_points": [
                {
                    "type": item.extension_type,
                    "display_name": item.display_name,
                    "description": item.description,
                }
                for item in extension_points
            ],
            "counts": {
                "cards": len(self._card_plugins),
                "quick_actions": len(self._quick_action_plugins),
                "floating_entries": len(self._floating_plugins),
                "data_sources": len(self._data_source_plugins),
                "capability_providers": len(self._capability_providers),
            },
        }


# 向后兼容别名：原 PluginRegistry 已重命名为 ExtensionPointRegistry
# （与 plugins/registry.py 中的 PluginRegistry 区分）
PluginRegistry = ExtensionPointRegistry


# ── Re-exports from new plugin SDK modules ───────────────────────────────────
# These provide backward compatibility; new code should import from the
# individual modules directly.

from .manifest import (  # noqa: E402
    ManifestValidationError as ManifestValidationError,
    PluginManifest as PluginManifest,
    PluginType as PluginType,
    parse_manifest_file as parse_manifest_file,
    validate_manifest_dict as validate_manifest_dict,
)
from .base import (  # noqa: E402
    ActionPlugin as ActionPlugin,
    BasePlugin as BasePlugin,
    CardPlugin as CardPlugin,
    CompanionPlugin as CompanionPlugin,
    DataSourcePlugin as DataSourcePlugin,
    PluginHealth as PluginHealth,
    PluginState as PluginState,
    ToolPlugin as ToolPlugin,
)
from .permissions import (  # noqa: E402
    PermissionCategory as PermissionCategory,
    PermissionDeniedError as PermissionDeniedError,
    PermissionManager as PermissionManager,
)
from .registry import (  # noqa: E402
    PluginRegistry as PluginRegistryV2,
)
from .sandbox import (  # noqa: E402
    PluginSandboxError as PluginSandboxError,
    error_boundary as error_boundary,
    execute_with_timeout as execute_with_timeout,
    sandboxed_execute as sandboxed_execute,
)
