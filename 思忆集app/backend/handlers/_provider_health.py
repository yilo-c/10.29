# -*- coding: utf-8 -*-
"""Provider 健康监控 JSON-RPC handlers (B2)。

提供三个 RPC 方法（与已有 ``provider.health`` 一次性探测互补，不冲突）：

- ``provider.health_status``     — 返回所有 provider 的健康状态
- ``provider.health_check_now``  — 立即触发健康检查（可选单个 provider）
- ``provider.health_reset``      — 重置 provider 的失败计数并标记为可用

监控器实例懒加载在 context 上（``context.provider_health_monitor``），
首次调用时创建并 attach 到 AI 客户端的 FallbackOpenAIClient（若存在），
让后续 AI 调用自动跳过已降级的 provider。
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

logger = logging.getLogger("siyi.backend.handlers.provider_health")

# 后台健康检查周期（秒）。与 ProviderHealthMonitor 默认值保持一致；
# 构造时必须显式以关键字传入，避免与 settings_repository 位置参数混淆。
DEFAULT_CHECK_INTERVAL_SECONDS = 300


def _load_providers_sync(context) -> "list[Any]":
    """同步加载 provider 列表（包裹在 to_thread 中调用）。"""
    from core.provider_config import load_providers

    return load_providers(context.settings_repository)


def _build_stage_provider_names(providers: "list[Any]") -> "dict[str, str]":
    """根据 provider 配置推断 FallbackOpenAIClient 各 stage -> provider name 映射。

    规则：
      - primary: 第一个 enabled 且非 fallback/local 且有 api_key 的 provider
      - fallback: 第一个 enabled 且 is_fallback 的 provider
      - local: 第一个 enabled 且 is_local 的 provider
      - local_coder: 暂不映射（无独立 provider 配置）
    """
    mapping: dict[str, str] = {}
    primary = next(
        (
            p
            for p in providers
            if p.enabled
            and not p.is_fallback
            and not p.is_local
            and p.api_key
        ),
        None,
    )
    if primary:
        mapping["primary"] = primary.name
    fallback = next(
        (
            p
            for p in providers
            if p.enabled and p.is_fallback and not p.is_local
        ),
        None,
    )
    if fallback:
        mapping["fallback"] = fallback.name
    local = next((p for p in providers if p.enabled and p.is_local), None)
    if local:
        mapping["local"] = local.name
    return mapping


async def _get_or_create_monitor(context) -> "Any":
    """懒加载/获取 ProviderHealthMonitor 实例。

    创建后存到 ``context.provider_health_monitor``，并 attach 到 AI 客户端。
    """
    monitor = getattr(context, "provider_health_monitor", None)
    if monitor is not None:
        return monitor
    from ai.provider_health_monitor import ProviderHealthMonitor

    providers = await asyncio.to_thread(_load_providers_sync, context)
    # 关键字传参：ProviderHealthMonitor.__init__ 的第 2 个形参是 check_interval(int)，
    # settings_repository 必须按名字传，否则会把仓储对象喂给 int() 抛 TypeError。
    monitor = ProviderHealthMonitor(
        providers=providers,
        check_interval=DEFAULT_CHECK_INTERVAL_SECONDS,
        settings_repository=context.settings_repository,
    )
    context.provider_health_monitor = monitor
    try:
        ai_client = getattr(context, "ai_client", None)
        inner = getattr(ai_client, "_inner_client", None) or getattr(
            ai_client, "inner_client", None
        )
        if inner is not None and hasattr(inner, "set_health_monitor"):
            stage_names = _build_stage_provider_names(providers)
            inner.set_health_monitor(monitor, stage_names)
            logger.info("ProviderHealthMonitor 已 attach 到 AI 客户端: %s", stage_names)
        else:
            logger.info(
                "ProviderHealthMonitor 未 attach：AI 客户端不支持 set_health_monitor"
                "（降级跳过仅在本 handler 的手动检查中生效）"
            )
    except Exception:
        # attach 失败不阻塞手工健康检查，但必须留下可观测日志（原先是 debug，看不见）
        logger.warning("attach health monitor to ai_client 失败", exc_info=True)
    return monitor


async def _refresh_monitor_providers(monitor: "Any", context) -> "list[Any]":
    """重新加载 provider 列表并同步到 monitor（用户可能新增/删除了 provider）。

    加载失败时如实抛出：由调用方转换成错误返回，不再静默吞掉
    （原实现吞异常后 return 未绑定的 providers，会二次抛 UnboundLocalError）。
    """
    providers = await asyncio.to_thread(_load_providers_sync, context)
    monitor.set_providers(providers)
    return providers


def register_provider_health(registry, context) -> None:
    """Register provider health monitoring JSON-RPC methods."""

    async def provider_health_status(**kwargs: Any) -> "dict[str, Any]":
        """返回所有 provider 的健康状态。

        Returns:
            成功: {"ok": True, "providers": [{name, is_marked_down,
                   consecutive_failures, last_check_at, last_error,
                   last_check_ok}, ...]}
            失败: {"ok": False, "providers": [], "error": str}
        """
        try:
            monitor = await _get_or_create_monitor(context)
            await _refresh_monitor_providers(monitor, context)
            statuses = monitor.get_status()
            return {"ok": True, "providers": statuses}
        except Exception as exc:
            # 失败必须显式带 ok=False：原先只返回 {"providers": [], "error": ...}，
            # 调用方只看 providers 会误判成"一切正常"。
            logger.exception("provider.health_status 失败")
            return {"ok": False, "providers": [], "error": str(exc)}

    async def provider_health_check_now(
        provider_name: "str | None" = None,
        **kwargs: Any,
    ) -> "dict[str, Any]":
        """立即触发健康检查。

        Args:
            provider_name: 可选，指定单个 provider 名；为空则检查全部。

        Returns:
            单个 provider: {"ok": True, "provider": {...status...}}
            全部: {"ok": True, "providers": [{...status...}, ...]}
            失败: {"ok": False, "error": str}
        """
        name = (
            provider_name
            or kwargs.get("name")
            or kwargs.get("provider_id")
            or ""
        )
        name = str(name).strip()
        try:
            monitor = await _get_or_create_monitor(context)
            providers = await _refresh_monitor_providers(monitor, context)
        except Exception as exc:
            logger.exception("初始化健康监控器失败")
            return {"ok": False, "error": f"监控器初始化失败: {exc}"}
        if name:
            target = next((p for p in providers if p.name == name), None)
            if target is None:
                return {"ok": False, "error": f"provider '{name}' 不存在"}
            try:
                status = await monitor.check_provider(target)
            except Exception as exc:
                logger.exception("provider.health_check_now(%s) 失败", name)
                return {"ok": False, "error": str(exc)}
            return {"ok": True, "provider": status}
        try:
            statuses = await monitor.check_all()
        except Exception as exc:
            logger.exception("provider.health_check_now(all) 失败")
            return {"ok": False, "error": str(exc)}
        return {"ok": True, "providers": statuses}

    async def provider_health_reset(provider_name: str = "", **kwargs: Any) -> "dict[str, Any]":
        """重置 provider 的失败计数并标记为可用。

        Args:
            provider_name: 要重置的 provider 名（必填）。

        Returns:
            {"success": bool, "name": str, "message": str}
        """
        name = (
            provider_name
            or kwargs.get("name")
            or kwargs.get("provider_id")
            or ""
        )
        name = name.strip()
        if not name:
            return {"success": False, "message": "provider_name is required"}
        try:
            monitor = await _get_or_create_monitor(context)
            await _refresh_monitor_providers(monitor, context)
            found = await monitor.reset_provider(name)
        except Exception as exc:
            logger.warning("provider.health_reset(%s) 失败: %s", name, exc, exc_info=True)
            return {"success": False, "name": name, "message": str(exc)}
        if found:
            return {
                "success": True,
                "name": name,
                "message": f"provider '{name}' 已重置为可用",
            }
        return {
            "success": False,
            "name": name,
            "message": f"provider '{name}' 未被健康监控跟踪",
        }

    registry.register("provider.health_status", provider_health_status)
    registry.register("provider.health_check_now", provider_health_check_now)
    registry.register("provider.health_reset", provider_health_reset)
