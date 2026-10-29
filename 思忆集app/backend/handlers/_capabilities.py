"""能力注册表 JSON-RPC 处理器 — 用量统计 + 能力审批。"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

logger = logging.getLogger("siyi.backend.handlers.capabilities")


def register_capabilities(registry, context) -> None:
    """注册能力相关 RPC 方法。"""

    # ── usage.record ──
    async def usage_record(action: str = "", domain: str = "", **kwargs: Any) -> dict[str, Any]:
        """记录一次用户操作（用于用量统计）。"""
        if not action:
            return {"error": "action required"}
        from services.usage_stats import record_action
        # P1 修复：record_action 内部做文件 I/O，用 to_thread 避免阻塞事件循环
        await asyncio.to_thread(record_action, context, action, domain or None)
        return {"success": True}

    registry.register("usage.record", usage_record)

    # ── usage.stats ──
    async def usage_stats(**kwargs: Any) -> dict[str, Any]:
        """查看当前用量统计。"""
        from services.usage_stats import get_stats
        # P1 修复：get_stats 内部做文件 I/O，用 to_thread 避免阻塞事件循环
        return await asyncio.to_thread(get_stats, context)

    registry.register("usage.stats", usage_stats)

    # ── capability.list ──
    async def capability_list(**kwargs: Any) -> list[dict[str, Any]]:
        """列出所有能力及其状态。"""
        from services.capability_registry import REGISTRY
        return [
            {
                "id": c.id,
                "label": c.label,
                "description": c.description,
                "risk": c.risk,
                "enabled": c.enabled,
                "icon": c.icon,
            }
            for c in REGISTRY
        ]

    registry.register("capability.list", capability_list)

    # ── capability.approve ──
    async def capability_approve(cap_id: str = "", **kwargs: Any) -> dict[str, Any]:
        """用户批准接入某项能力。"""
        if not cap_id:
            return {"error": "cap_id required"}
        from services.capability_registry import set_enabled
        from services.usage_stats import mark_enabled
        # P1 修复：set_enabled 与 mark_enabled 涉及内存状态 + 文件 I/O，
        # 用 to_thread 避免阻塞事件循环
        enabled = await asyncio.to_thread(set_enabled, cap_id, True)
        if enabled:
            await asyncio.to_thread(mark_enabled, context, cap_id)
            logger.info("User approved capability: %s", cap_id)
            return {"success": True, "cap_id": cap_id}
        return {"error": f"capability {cap_id} not found"}

    registry.register("capability.approve", capability_approve)

    # ── capability.dismiss ──
    async def capability_dismiss(cap_id: str = "", **kwargs: Any) -> dict[str, Any]:
        """用户忽略某个能力建议。"""
        if not cap_id:
            return {"error": "cap_id required"}
        # 标记为已读，30 天内不再提醒
        from services.usage_stats import mark_suggestion_sent
        # P1 修复：mark_suggestion_sent 内部做文件 I/O，用 to_thread 避免阻塞事件循环
        await asyncio.to_thread(mark_suggestion_sent, context, cap_id)
        return {"success": True}

    registry.register("capability.dismiss", capability_dismiss)
