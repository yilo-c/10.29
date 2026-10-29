"""Lazy-loaded re-exports for sync subpackage.

Modules are loaded on first access to avoid circular imports
(e.g., sync.__init__ -> sync.note_sync -> core -> workbench -> ui -> automation_hub_client -> settings_schema -> core.settings_migration).

波次④包 C（2026-09-24）：死岛 channels.py / file_sync_service.py 已整删
（零生产/零测试引用，见 deliverables/wave4-sync-ssot-blueprint-2026-09-24.md §1c）。
"""
import importlib as _importlib

_MODULE_NAMES = [
    "directory_manager",
    "inbox_sync",
    "memory_sync",
    "news_sync",
    "note_sync",
    "settings_sync",
    "summary_view",
    "task_sync",
    "translation_sync",
    "ui_support",
    "vocabulary_sync",
]


def __getattr__(name):
    for mod_name in _MODULE_NAMES:
        try:
            mod = _importlib.import_module(f".{mod_name}", __package__)
        except ImportError:
            continue
        if hasattr(mod, name):
            return getattr(mod, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
