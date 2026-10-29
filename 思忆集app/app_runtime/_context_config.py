"""ConfigContextMixin — config/tool delegation methods.

Extracted from AppContext to reduce file size.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from app_runtime.config_manager import ConfigManager


class ConfigHost(Protocol):
    """ConfigContextMixin 依赖的宿主契约 — AppContext 由 builder 注入 config_manager。"""

    config_manager: Any  # ConfigManager


class ConfigContextMixin:
    """Config and tool delegation — extracted from AppContext."""

    # 宿主契约：config_manager 由 AppContextBuilder 在运行时注入（见 _context_builder.py）
    if TYPE_CHECKING:
        config_manager: ConfigManager

    def get_llm_provider_topology(self):
        return self.config_manager.get_llm_provider_topology()

    def run_desktop_doctor(self, deep: bool = False):
        return self.config_manager.run_desktop_doctor(deep=deep)

    def format_desktop_doctor_report(self, report):
        return self.config_manager.format_desktop_doctor_report(report)

    def build_desktop_doctor_debug_assets(self, report):
        return self.config_manager.build_desktop_doctor_debug_assets(report)

    def get_desktop_doctor_debug_assets(self, deep: bool = False):
        return self.config_manager.get_desktop_doctor_debug_assets(deep=deep)

    def build_desktop_doctor_status_summary(self, debug_assets=None):
        return self.config_manager.build_desktop_doctor_status_summary(debug_assets)

    def build_desktop_doctor_continue_suggestion(self, debug_assets=None):
        return self.config_manager.build_desktop_doctor_continue_suggestion(debug_assets)

    def get_plugin_boundary_snapshot(self):
        return self.config_manager.get_plugin_boundary_snapshot()
