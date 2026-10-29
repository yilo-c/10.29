"""工作台模块 - 使用惰性导入以避免 PyQt6 在无头模式下被加载。"""

from __future__ import annotations

import importlib
from typing import Any


def __getattr__(name: str) -> Any:
    """惰性导入子模块成员，避免无头模式加载 PyQt6。"""
    lazy_map = {
        # card_renderer (导入 PyQt6，仅在桌面模式需要)
        "apply_grid_placements": "workbench.card_renderer",
        "build_workbench_controls_panel": "workbench.card_renderer",
        "compute_grid_placements": "workbench.card_renderer",
        "create_activity_card": "workbench.card_renderer",
        "create_card_container": "workbench.card_renderer",
        "create_greeting_bar": "workbench.card_renderer",
        "create_header_card": "workbench.card_renderer",
        "create_list_card": "workbench.card_renderer",
        "create_metrics_card": "workbench.card_renderer",
        "create_metrics_row": "workbench.card_renderer",
        "create_recent_list": "workbench.card_renderer",
        "create_suggestions_card": "workbench.card_renderer",
        "get_card_config": "workbench.card_renderer",
        "get_card_label": "workbench.card_renderer",
        "get_card_scale_factor": "workbench.card_renderer",
        "get_density_profile": "workbench.card_renderer",
        # controller (可能导入 PyQt6)
        "WorkbenchController": "workbench.controller",
        # layout_config (纯 Python，安全)
        "_normalize_bool_map": "workbench.layout_config",
        "_normalize_card_order": "workbench.layout_config",
        "_normalize_card_sizes": "workbench.layout_config",
        "_normalize_card_spans": "workbench.layout_config",
        "_normalize_presets": "workbench.layout_config",
        "_safe_dict": "workbench.layout_config",
        "_safe_list": "workbench.layout_config",
        "build_layout_config_from_legacy": "workbench.layout_config",
        "default_workbench_layout_config": "workbench.layout_config",
        "delete_layout_preset": "workbench.layout_config",
        "export_layout_presets_to_json": "workbench.layout_config",
        "get_workbench_layout_config": "workbench.layout_config",
        "import_layout_presets_from_json": "workbench.layout_config",
        "migrate_workbench_layout_config": "workbench.layout_config",
        "normalize_workbench_layout_config": "workbench.layout_config",
        "rename_layout_preset": "workbench.layout_config",
        "save_workbench_layout_config": "workbench.layout_config",
        # service (纯 Python，安全)
        "PersonalWorkbenchService": "workbench.service",
        "WorkbenchLayoutService": "workbench.service",
        # viewmodel (可能导入 PyQt6)
        "WorkbenchViewModel": "workbench.viewmodel",
    }
    if name in lazy_map:
        module_path = lazy_map[name]
        module = importlib.import_module(module_path)
        return getattr(module, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "PersonalWorkbenchService",
    "WorkbenchLayoutService",
    "WorkbenchViewModel",
    # card_renderer exports
    "apply_grid_placements",
    "build_workbench_controls_panel",
    "compute_grid_placements",
    "create_activity_card",
    "create_card_container",
    "create_greeting_bar",
    "create_header_card",
    "create_list_card",
    "create_metrics_card",
    "create_metrics_row",
    "create_recent_list",
    "create_suggestions_card",
    "get_card_config",
    "get_card_label",
    "get_card_scale_factor",
    "get_density_profile",
    # layout_config exports
    "_normalize_bool_map",
    "_normalize_card_order",
    "_normalize_card_sizes",
    "_normalize_card_spans",
    "_normalize_presets",
    "_safe_dict",
    "_safe_list",
    "build_layout_config_from_legacy",
    "default_workbench_layout_config",
    "delete_layout_preset",
    "export_layout_presets_to_json",
    "get_workbench_layout_config",
    "import_layout_presets_from_json",
    "migrate_workbench_layout_config",
    "normalize_workbench_layout_config",
    "rename_layout_preset",
    "save_workbench_layout_config",
    # controller
    "WorkbenchController",
    # viewmodel
    "WorkbenchViewModel",
]
