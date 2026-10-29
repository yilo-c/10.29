"""
思忆集 application services.

Cross-cutting services used by multiple modules:
desktop doctor, reminder, browser bridge, sync orchestration.

Services should depend on domain/ interfaces, not on UI packages.

导出策略（2026-09-11）：这些类是「重」服务，顶层同步 import 会连锁拉动
repositories / desktop 等子包，在特定导入时序下被注册成半成品包文件系统
（headless 后端报 ``import services`` 时 ``desktop is not a package`` 的根因）。
因此改为 PEP 562 惰性 ``__getattr__``：只在真正访问 ``services.<Name>`` 时才
import 对应子模块，打破 ``import services`` 时的导入环，且任何
``services.<Name>`` / ``from services import <Name>`` 语义保持不变。
"""
from __future__ import annotations

import importlib
import logging
from typing import Any

logger = logging.getLogger("siyi.services")

# name -> 所属子模块；仅名字需暴露时才惰性加载对应模块，避免 import 副作用
_LAZY_EXPORTS: dict[str, str] = {
    "DesktopDoctorService": "services.desktop_doctor_service",
    "ReminderService": "services.reminder",
    "WebFetchResult": "services.web_fetch",
    "WebFetchService": "services.web_fetch",
    "WebSearchResult": "services.web_search",
    "WebSearchService": "services.web_search",
    "TTSService": "services.tts",
    "RuleCenter": "services.rule_center",
    "TechInsight": "services.tech_watch",
    "TechAssessment": "services.tech_watch",
    "TechWatchStore": "services.tech_watch",
    "TechWatchCollector": "services.tech_watch",
    "TechWatchJudge": "services.tech_watch",
    "JobHunterService": "services.job_hunter",
}


def __getattr__(name: str) -> Any:
    module_name = _LAZY_EXPORTS.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    try:
        module = importlib.import_module(module_name)
        value = getattr(module, name)
    except Exception as exc:
        # 保持向后兼容：惰性加载失败时给出可诊断告警且不抛（与旧 try/except 一致）
        logger.warning("Service %s import failed: %s", name, exc)
        raise AttributeError(name) from exc
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(list(globals().keys()) + list(_LAZY_EXPORTS.keys()))
