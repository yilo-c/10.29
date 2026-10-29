"""kernel.status —— 执行内核状态（Flip 可观测性）。

前端"内核状态徽章"与"深度模式入口"的数据源。报告当前 chat 路由、
网关可用性与健康、以及实际生效引擎与降级状态。

分层：本模块属于 backend.handlers，可 import chat_paths（同为 backend）。
路由解析与网关探测复用 chat_paths.gateway_path，单一事实来源。
"""

from __future__ import annotations

import logging
from typing import Any

from services.agent_gateway.routing import (
    resolve_chat_route as _resolve_chat_route,
    get_gateway as _get_gateway,
    kernel_type as _kernel_type,
)

logger = logging.getLogger("siyi.backend.handlers.kernel")


def build_kernel_status(context: Any) -> dict[str, Any]:
    """返回执行内核状态字典（纯数据，便于被 RPC / system.health 复用）。"""
    route = _resolve_chat_route(context)
    gw = _get_gateway(context)
    gw_present = gw is not None and getattr(gw, "name", "none") != "none"

    if route == "gateway" and gw_present:
        effective = "gateway"
        degraded = False
        # auto 内核：gateway_name 显示当前 active 内核名（而非 "auto"）
        gw_name = getattr(gw, "name", "?")
        if gw_name == "auto":
            active = (getattr(gw, "health", lambda: {})() or {}).get("active")
            gw_name = str(active) if active else "none"
        msg = f"执行内核已启用（{gw_name}）"
    elif route == "gateway" and not gw_present:
        effective = "butler(fallback)"
        degraded = True
        msg = "执行内核不可用，已降级到遗留管家"
    else:
        effective = "butler"
        degraded = False
        msg = "遗留管家路径（chat_route=legacy）"

    details: dict[str, Any] = {
        "route": route,
        "gateway_present": gw_present,
        "gateway_name": getattr(gw, "name", None),
        "kernel_type": _kernel_type(context),
        "effective_engine": effective,
        "degraded": degraded,
    }
    if gw_present and hasattr(gw, "health"):
        try:
            details["gateway_health"] = gw.health()
        except Exception:
            logger.exception("获取网关健康状态失败")
            pass
    # M3：能力特征声明 —— 前端据此决定是否渲染工具步骤/审批提示/续跑按钮
    if gw_present and hasattr(gw, "capabilities"):
        try:
            details["capabilities"] = gw.capabilities()
        except Exception:
            logger.exception("获取网关能力声明失败")
            pass
    return {
        "route": route,
        "effective_engine": effective,
        "degraded": degraded,
        "gateway_present": gw_present,
        "gateway_name": gw_name if (route == "gateway" and gw_present and getattr(gw, "name", "") == "auto") else getattr(gw, "name", None),
        "kernel_type": details["kernel_type"],
        "message": msg,
        "details": details,
    }


def register_kernel(registry, context: Any) -> None:
    """注册 kernel.* RPC（仅 kernel.status）。"""

    async def kernel_status(**params: Any) -> dict[str, Any]:
        return build_kernel_status(context)

    registry.register("kernel.status", kernel_status)
