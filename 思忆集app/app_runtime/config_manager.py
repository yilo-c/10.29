"""配置与工具域：管理 LLM provider 配置、桌面 doctor、插件注册。"""

from __future__ import annotations

import os

from ai.llm_provider_topology import build_llm_provider_topology


class ConfigManager:
    """管理 provider 拓扑、运行时配置的读取/更新。"""

    def __init__(self, settings_repository, desktop_doctor_service, plugin_registry) -> None:
        self._settings_repository = settings_repository
        self._desktop_doctor_service = desktop_doctor_service
        self._plugin_registry = plugin_registry

    def get_llm_provider_topology(self) -> list:
        return build_llm_provider_topology(
            settings_repository=self._settings_repository,
            environ=os.environ,
        )

    # ── 桌面 Doctor ──

    def run_desktop_doctor(self, deep: bool = False) -> dict:
        return self._desktop_doctor_service.run_diagnostics(deep=deep)

    def format_desktop_doctor_report(self, report: dict) -> str:
        return self._desktop_doctor_service.format_report(report)

    def build_desktop_doctor_debug_assets(self, report: dict) -> dict:
        return self._desktop_doctor_service.build_debug_assets(report)

    def get_desktop_doctor_debug_assets(self, deep: bool = False) -> dict:
        report = self.run_desktop_doctor(deep=deep)
        return self.build_desktop_doctor_debug_assets(report)

    def build_desktop_doctor_status_summary(self, debug_assets: dict | None = None) -> str:
        return self._desktop_doctor_service.build_status_summary(debug_assets)

    def build_desktop_doctor_continue_suggestion(
        self, debug_assets: dict | None = None
    ) -> str:
        return self._desktop_doctor_service.build_continue_suggestion(debug_assets)

    # ── 插件注册 ──

    def get_plugin_boundary_snapshot(self) -> dict:
        return self._plugin_registry.describe_contract()
