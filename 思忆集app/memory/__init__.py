"""Lazy-loaded re-exports for memory subpackage.

Modules are loaded on first access to avoid circular imports
(e.g., memory.context -> assistant.tag_service -> memory.entry_schema -> memory.__init__).
"""
import importlib as _importlib

_MODULE_NAMES = [
    "behavior_extractor",
    "context",
    "decision",
    "entry_schema",
    "extractor",
    "index",
    "layer",
    "local_index_engine",
    "linker",
    "linker_types",
    "migration",
    "scheduler",
    "search",
    "structured_store",
    "supertag_search",
    "types",
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
