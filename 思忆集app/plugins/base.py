"""Base plugin classes for all 5 plugin types.

Defines the abstract interfaces that each plugin type must implement,
as specified in docs/plugin-sdk-spec.md.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from .manifest import PluginManifest


class PluginState(str, Enum):
    """Lifecycle state of a plugin."""

    INSTALLED = "installed"
    ENABLED = "enabled"
    ACTIVE = "active"
    DISABLED = "disabled"
    ERROR = "error"


@dataclass
class PluginHealth:
    """Health status of a plugin at a point in time."""

    status: str  # "healthy", "degraded", "down"
    last_check: str
    error_count: int = 0
    last_error: str | None = None
    execution_count: int = 0

    @staticmethod
    def healthy() -> PluginHealth:
        return PluginHealth(
            status="healthy",
            last_check=datetime.now(timezone.utc).isoformat(),
        )

    @staticmethod
    def degraded(reason: str, error_count: int = 1) -> PluginHealth:
        return PluginHealth(
            status="degraded",
            last_check=datetime.now(timezone.utc).isoformat(),
            error_count=error_count,
            last_error=reason,
        )

    @staticmethod
    def down(reason: str, error_count: int = 1) -> PluginHealth:
        return PluginHealth(
            status="down",
            last_check=datetime.now(timezone.utc).isoformat(),
            error_count=error_count,
            last_error=reason,
        )


class BasePlugin(ABC):
    """Abstract base class for all plugin types.

    Subclasses must set ``manifest`` and implement the abstract methods
    for their specific plugin type.
    """

    def __init__(self, manifest: PluginManifest) -> None:
        self.manifest = manifest
        self.state = PluginState.INSTALLED
        self._config: dict[str, Any] = {}
        self._health = PluginHealth.healthy()

    # ── Lifecycle hooks ───────────────────────────────────────────────

    def on_install(self) -> None:
        """Called after the plugin is installed.

        Override to perform one-time setup (create directories, etc.).
        """

    def on_enable(self) -> None:
        """Called when the plugin is enabled.

        Override to start timers, open connections, or register listeners.
        """

    def on_disable(self) -> None:
        """Called when the plugin is disabled.

        Override to clean up timers, close connections, save state.
        """

    def on_uninstall(self) -> None:
        """Called when the plugin is uninstalled.

        Override to remove persistent data created by the plugin.
        """

    # ── Configuration ─────────────────────────────────────────────────

    @property
    def config(self) -> dict[str, Any]:
        """Current plugin configuration."""
        return dict(self._config)

    @config.setter
    def config(self, value: dict[str, Any]) -> None:
        self._config = dict(value)

    def get_config_value(self, key: str, default: Any = None) -> Any:
        return self._config.get(key, default)

    # ── Health ────────────────────────────────────────────────────────

    @property
    def health(self) -> PluginHealth:
        return self._health

    def health_check(self) -> PluginHealth:
        """Perform a health check on this plugin.

        Override to add custom health logic. Default returns the current health.
        """
        return self._health

    def _record_error(self, error: str) -> None:
        self._health = PluginHealth(
            status="degraded",
            last_check=datetime.now(timezone.utc).isoformat(),
            error_count=self._health.error_count + 1,
            last_error=error,
            execution_count=self._health.execution_count,
        )

    def _record_execution(self) -> None:
        self._health = PluginHealth(
            status=self._health.status,
            last_check=self._health.last_check,
            error_count=self._health.error_count,
            last_error=self._health.last_error,
            execution_count=self._health.execution_count + 1,
        )


class ToolPlugin(BasePlugin):
    """A tool plugin that registers callable tools for the AI.

    Interface: ``execute(params) → result``
    """

    @abstractmethod
    def execute(self, params: dict[str, Any]) -> dict[str, Any]:
        """Execute the tool with the given parameters."""

    @property
    def tool_id(self) -> str:
        return self.manifest.id

    @property
    def parameters_schema(self) -> dict[str, Any]:
        return dict(self.manifest.config_schema)


class DataSourcePlugin(BasePlugin):
    """A data source plugin that provides external data.

    Interface: ``fetch(query) → items``, ``sync(cursor) → delta``
    """

    @abstractmethod
    def fetch(self, query: dict[str, Any]) -> list[dict[str, Any]]:
        """Fetch data items matching the query."""

    def sync(self, cursor: str | None = None) -> dict[str, Any]:
        """Perform incremental sync from an optional cursor.

        Returns a dict with ``items``, ``new_cursor``, and ``has_more``.
        The default implementation calls ``fetch`` with an empty query.
        """
        items = self.fetch({})
        return {"items": items, "new_cursor": None, "has_more": False}

    @property
    def source_id(self) -> str:
        return self.manifest.id


class CardPlugin(BasePlugin):
    """A UI card plugin that renders widgets on the workbench.

    Interface: ``render(state) → widget``
    """

    @abstractmethod
    def render(self, state: dict[str, Any]) -> Any:
        """Render the card with the given state.

        Returns a widget / markup object depending on the UI framework.
        """

    @property
    def preferred_size(self) -> tuple[int, int]:
        """Preferred (width, height) in logical pixels."""
        return (300, 200)

    @property
    def card_id(self) -> str:
        return self.manifest.id


class ActionPlugin(BasePlugin):
    """A condition-triggered action plugin.

    Interface: ``condition(context) → bool``, ``perform(context) → result``
    """

    @abstractmethod
    def condition(self, context: dict[str, Any]) -> bool:
        """Return True if this action should trigger in the given context."""

    @abstractmethod
    def perform(self, context: dict[str, Any]) -> dict[str, Any]:
        """Execute the action with the given context."""

    @property
    def action_id(self) -> str:
        return self.manifest.id


class CompanionPlugin(BasePlugin):
    """A floating-assistant companion plugin.

    Interface: ``on_hover() → str``, ``on_click() → None``, ``status_text``
    """

    @abstractmethod
    def on_hover(self) -> str:
        """Return a status/hint string shown on hover."""

    @abstractmethod
    def on_click(self) -> None:
        """Handle a click on the companion entry."""

    @property
    def status_text(self) -> str:
        """Current status text shown in the floating assistant."""
        return ""

    @property
    def companion_id(self) -> str:
        return self.manifest.id


# ── Plugin type registry ─────────────────────────────────────────────────────

PLUGIN_TYPE_MAP: dict[str, type[BasePlugin]] = {
    "tool": ToolPlugin,
    "datasource": DataSourcePlugin,
    "card": CardPlugin,
    "action": ActionPlugin,
    "companion": CompanionPlugin,
}
