"""插件工具接入 — 把 tool 类型生命周期插件注册为 AI 可调用能力。

生命周期插件（plugins/installed 下的 tool 类型）此前只被桥接到 UI 扩展点，
tool 类型没有对应的 UI 扩展点，无法被小问调用。本模块补齐该缺口：
- 每个 tool 插件注册一个 CapabilitySpec（capability_id = plugin.<plugin_id>），
  payload_schema 取 manifest.config_schema；
- 在 router._handlers 注册同名 handler，经 sandboxed_execute 沙箱执行
  plugin.execute(params)，复用现有超时/错误边界保护。

在 CapabilityRouter._register_builtin_capabilities() 末尾调用（与
register_tool_management_capabilities 同级）。
"""

from __future__ import annotations

import inspect
import logging
import os
from typing import Any

from services.capability_types import CapabilitySpec

logger = logging.getLogger("siyi.assistant.capability.plugin_tools")


def _entry_point_for(plugin: Any) -> str:
    """求插件类可稳定 import 的 entry_point（模块路径:类名），而非 ``__module__``。

    自动发现加载插件时把 ``plugins/installed`` 插入 ``sys.path``，以短名导入
    （``importlib.import_module("infoclassifier.plugin")``），故 ``plugin.__module__``
    是 ``infoclassifier.plugin``。子进程 worker 只锚定 cwd=app_root（``sys.path[0]``），
    能 import ``plugins.installed.infoclassifier.plugin`` 却找不到短名 ``infoclassifier``，
    若直接复用 ``__module__`` 会导致 worker ``ModuleNotFoundError``。

    这里改为依据类文件绝对路径，求相对 app_root 的点分模块路径，得到统一的
    ``plugins.installed.<entry>.plugin:ClassName``，主/子进程两侧均可 import。
    """
    cls = plugin.__class__
    src = os.path.abspath(inspect.getfile(cls))
    # app_root = .../assistant/capability/_plugin_tools.py 上溯三级
    app_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    rel = os.path.relpath(src, app_root)
    if rel.startswith("..") or os.path.isabs(rel):
        # 类文件不在代码库根内（如已删除/外部包）：退回 __module__，worker 端尽力导入
        return f"{cls.__module__}:{cls.__name__}"
    rel = rel[:-3] if rel.endswith(".py") else rel  # 去 .py
    module = rel.replace(os.sep, ".")
    return f"{module}:{cls.__name__}"


__all__ = ["register_plugin_tools"]


def register_plugin_tools(router: Any) -> int:
    """把已发现的 tool 生命周期插件注册为可调用能力。

    Args:
        router: CapabilityRouter 实例（需有 capability_registry 与 _handlers）。

    Returns:
        成功注册的插件工具数量。
    """
    registry = getattr(router, "capability_registry", None)
    if registry is None:
        return 0

    try:
        from plugins.registry import get_lifecycle_registry
    except Exception as exc:
        logger.warning("plugin tool registration unavailable: %s", exc)
        return 0

    lifecycle = get_lifecycle_registry()
    registered = 0
    # 把 services.* 原生实现注册为能力路由的兜底（仅一次）
    _ensure_capability_router_fallbacks()
    _ensure_dedup_fallbacks()
    for plugin in lifecycle.list_plugins():
        manifest = getattr(plugin, "manifest", None)
        if manifest is None or manifest.type != "tool":
            continue
        plugin_id = str(manifest.id)
        capability_id = f"plugin.{plugin_id}"
        if registry.get_capability(capability_id) is not None:
            continue

        # 把 plugin 声明的能力桥接到能力路由（plugin 优先、原生兜底）
        _register_plugin_to_capability_router(plugin, manifest)

        # 把 manifest 声明的权限登记进全局 PermissionManager，
        # 使 sandboxed_execute 能在运行时拦截被撤销的权限（Task #5）。
        declared = list(getattr(manifest, "permissions", []) or [])
        if declared:
            try:
                from plugins.permissions import get_permission_manager

                get_permission_manager().declare_permissions(plugin_id, declared)
            except Exception as exc:  # 权限登记失败不应阻断能力注册
                logger.warning("plugin permission declare failed %s: %s", plugin_id, exc)

        spec = CapabilitySpec(
            capability_id=capability_id,
            display_name=str(manifest.name or plugin_id),
            description=str(manifest.description or f"插件工具 {manifest.name or plugin_id}"),
            category="plugin",
            group="plugin",
            source=f"plugin.{plugin_id}",
            enabled=True,
            payload_schema=dict(manifest.config_schema),
            risk_level="read",
            timeout_default=20,
        )
        try:
            registry.register_capability(spec)
        except Exception as exc:
            logger.warning("plugin tool capability register failed %s: %s", plugin_id, exc)
            continue

        router._handlers[capability_id] = _make_plugin_tool_handler(plugin, declared)
        registered += 1

    if registered:
        logger.info("Registered %d plugin tools", registered)
    return registered


def _ensure_capability_router_fallbacks() -> None:
    """把 services.* 原生实现注册为能力路由的兜底（仅一次）。"""
    from plugins.capability_router import get_capability_router

    router = get_capability_router()
    if getattr(router, "_fallbacks_registered", False):
        return
    # M1 清理将分类相关函数从 info_collector_service 迁至下方两个模块，
    # 此处 import 源须同步更新，否则模块加载阶段即 ImportError。
    from services.info_categorizer import categorize_item
    from services.info_constants import get_section_keywords, has_cjk

    router.register_fallback("info.classify", categorize_item)
    router.register_fallback("info.has_cjk", has_cjk)
    router.register_fallback("info.section_keywords", get_section_keywords)
    router._fallbacks_registered = True  # type: ignore[attr-defined]


def _ensure_dedup_fallbacks() -> None:
    """把信息去重原生实现注册为能力路由的兜底（仅一次）。"""
    from plugins.capability_router import get_capability_router

    router = get_capability_router()
    if getattr(router, "_dedup_fallbacks_registered", False):
        return
    from services.info_collector_service import dedup_items, title_similarity

    router.register_fallback("info.dedup", dedup_items)
    router.register_fallback("info.title_similarity", title_similarity)
    router._dedup_fallbacks_registered = True  # type: ignore[attr-defined]


def _register_plugin_to_capability_router(plugin: Any, manifest: Any) -> None:
    """把 plugin 声明的能力注册为能力路由的 plugin invoker（plugin 优先）。"""
    declared_caps = list(getattr(manifest, "capabilities", []) or ())
    if not declared_caps:
        return
    try:
        from plugins.capability_router import get_capability_router
    except Exception as exc:  # 路由不可用不应阻断能力注册
        logger.warning("capability_router unavailable: %s", exc)
        return

    router = get_capability_router()
    declared_perms = list(getattr(manifest, "permissions", []) or [])
    entry_point = _entry_point_for(plugin)

    def _invoker(params: dict[str, Any]) -> dict[str, Any]:
        from plugins.sandbox import (
            choose_isolation_mode,
            run_plugin_isolated,
            sandboxed_execute,
        )

        mode = choose_isolation_mode()
        if mode == "subprocess":
            return run_plugin_isolated(
                manifest.id,
                entry_point,
                dict(params or {}),
                required_permissions=list(declared_perms) if declared_perms else None,
            )
        return sandboxed_execute(
            manifest.id,
            plugin.execute,
            dict(params or {}),
            required_permissions=list(declared_perms) if declared_perms else None,
        )

    for cap in declared_caps:
        router.register_plugin_invoker(cap, _invoker)


def _make_plugin_tool_handler(plugin: Any, declared_permissions: list[str] | None = None):
    """为插件工具生成 router._handlers 可用的同步 handler（沙箱执行）。

    *declared_permissions* 来自 manifest.permissions；若非空，则在
    sandboxed_execute 内执行运行时权限拦截——任一权限被撤销时执行被拒绝。
    """

    def _handler(args: dict[str, Any]) -> dict[str, Any]:
        from plugins.sandbox import (
            choose_isolation_mode,
            run_plugin_isolated,
            sandboxed_execute,
        )

        mode = choose_isolation_mode()
        if mode == "subprocess":
            entry_point = _entry_point_for(plugin)
            return run_plugin_isolated(
                plugin.manifest.id,
                entry_point,
                dict(args or {}),
                required_permissions=list(declared_permissions) if declared_permissions else None,
            )
        return sandboxed_execute(
            plugin.manifest.id,
            plugin.execute,
            dict(args or {}),
            required_permissions=list(declared_permissions) if declared_permissions else None,
        )

    return _handler
