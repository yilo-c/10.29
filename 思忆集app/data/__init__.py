# data subpackage — files migrated from root are re-exported here.
# Root-level shims (importlib) redirect to these canonical locations.
#
# 导出策略（2026-09-11）：StorageContext 顶层 import 会回引 repositories
# （repositories._chat_session → data._atomic_json → data → data.app_storage_context
#  → repositories），在特定导入时序下把 repositories 注册成半成品包并抛
# "cannot import name from partially initialized module"（ReminderService 报错根因）。
# 改为 PEP 562 惰性导出：仅真正访问 data.LocalDataStore / data.StorageContext 时才
# import，打破 import 环。data.local_storage 等子模块路径不受影响。
from __future__ import annotations

import importlib
from typing import Any

# name -> 所属子模块
_LAZY_EXPORTS: dict[str, str] = {
    "LocalDataStore": "data.local_storage",
    "StorageContext": "data.app_storage_context",
}


def __getattr__(name: str) -> Any:
    module_name = _LAZY_EXPORTS.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module = importlib.import_module(module_name)
    value = getattr(module, name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(list(globals().keys()) + list(_LAZY_EXPORTS.keys()))
