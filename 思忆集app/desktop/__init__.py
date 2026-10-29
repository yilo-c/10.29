# auto-generated re-exports for desktop subpackage (惰性)
#
# V2.1 批次1#4：headless 后端契约是「无头不依赖 PyQt6」。之前这里在模块顶层
# 一次性 import pet_controller / selection_monitor / web_bridge，而这些模块
# 顶层 import PyQt6 —— 于是 AppContextBuilder.build_desktop_services 里
# `from desktop.doctor_service import DesktopDoctorService` 触发 desktop/__init__.py
# 时，即便只是要一个纯 Python 的 DoctorService，也会因缺 PyQt6 崩掉无头后端。
# 改为 PEP 562 __getattr__ 惰性 re-export：访问具体名字时才去 import 对应子模块，
# 达到"按需加载"，任何 `desktop.<Name>` / `from desktop import <Name>` 语义不变。

from __future__ import annotations

import importlib
from typing import Any

# name -> 所属子模块；仅名字需在包名空间暴露时才惰性加载对应模块
_LAZY_EXPORTS: dict[str, str] = {
    # companion_controller
    "DesktopCompanionController": "desktop.companion_controller",
    # doctor_service （纯 Python，headless 可用）
    "DesktopDoctorService": "desktop.doctor_service",
    # pet_controller / selection_monitor / web_bridge （依赖 PyQt6，按需加载）
    "DesktopPetController": "desktop.pet_controller",
    "SelectionMonitor": "desktop.selection_monitor",
    "AppBridge": "desktop.web_bridge",
    "AssistantService": "desktop.web_bridge",
    "BaseService": "desktop.web_bridge",
    "DatabaseService": "desktop.web_bridge",
    "DoctorService": "desktop.web_bridge",
    "InboxService": "desktop.web_bridge",
    "NotesService": "desktop.web_bridge",
    "PetService": "desktop.web_bridge",
    "SettingsService": "desktop.web_bridge",
    "SyncService": "desktop.web_bridge",
    "TasksService": "desktop.web_bridge",
    "_RateLimiter": "desktop.web_bridge",
    "_default_services": "desktop.web_bridge",
    "_err": "desktop.web_bridge",
    "_ok": "desktop.web_bridge",
    "_validate_payload": "desktop.web_bridge",
}


def __getattr__(name: str) -> Any:
    module_name = _LAZY_EXPORTS.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module = importlib.import_module(module_name)
    value = getattr(module, name)
    # 缓存到模块命名空间，后续访问不再走 importlib
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(list(globals().keys()) + list(_LAZY_EXPORTS.keys()))
