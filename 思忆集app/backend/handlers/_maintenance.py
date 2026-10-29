# -*- coding: utf-8 -*-
"""自维护提案 JSON-RPC handlers（P2-7：监督式自优化的人机审批面）。

方法：
- ``maintenance.proposals.list``   — 提案列表（可按状态过滤）
- ``maintenance.proposals.approve`` — 批准并执行白名单维护动作（含验证复查）
- ``maintenance.proposals.reject``  — 拒绝提案

巡检由 cron（system.self_maintenance，每日 05:00）自动生成提案；
本 handler 只暴露人在环内的审批面 —— 无审查的自动执行为永久红线。
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from backend.handlers._list_guard import capped_payload

logger = logging.getLogger("siyi.backend.handlers.maintenance")


def _get_service(context):
    from services.self_maintenance import get_self_maintenance_service

    data_store = getattr(context, "local_data_store", None)
    if data_store is None:
        return None
    return get_self_maintenance_service(data_store)


def register_maintenance(registry, context) -> None:
    """注册自维护提案相关 JSON-RPC 方法。"""

    async def maintenance_proposals_list(status: str = "", **kwargs: Any) -> dict[str, Any]:
        service = _get_service(context)
        if service is None:
            return {"success": False, "error": "data store unavailable", "proposals": []}
        try:
            proposals = await asyncio.to_thread(service.list_proposals, str(status or ""))
        except Exception as exc:  # noqa: BLE001
            logger.warning("maintenance.proposals.list failed: %s", exc)
            return {"success": False, "error": str(exc), "proposals": []}
        # 帧守卫（2026-10-15）：count 保持全量语义，proposals 截断 + truncated 标记
        total = len(proposals)
        proposals, truncated = capped_payload(list(proposals), method="maintenance.proposals.list")
        return {"success": True, "proposals": proposals, "count": total, "truncated": truncated}

    registry.register("maintenance.proposals.list", maintenance_proposals_list)

    async def maintenance_proposals_approve(proposal_id: str = "", **kwargs: Any) -> dict[str, Any]:
        if not proposal_id:
            return {"success": False, "error": "missing proposal_id"}
        service = _get_service(context)
        if service is None:
            return {"success": False, "error": "data store unavailable"}
        try:
            result = await asyncio.to_thread(service.approve, str(proposal_id), context)
        except Exception as exc:  # noqa: BLE001
            logger.warning("maintenance.proposals.approve failed: %s", exc)
            return {"success": False, "error": str(exc)}
        return result

    registry.register("maintenance.proposals.approve", maintenance_proposals_approve)

    async def maintenance_proposals_reject(proposal_id: str = "", **kwargs: Any) -> dict[str, Any]:
        if not proposal_id:
            return {"success": False, "error": "missing proposal_id"}
        service = _get_service(context)
        if service is None:
            return {"success": False, "error": "data store unavailable"}
        try:
            result = await asyncio.to_thread(service.reject, str(proposal_id))
        except Exception as exc:  # noqa: BLE001
            logger.warning("maintenance.proposals.reject failed: %s", exc)
            return {"success": False, "error": str(exc)}
        return result

    registry.register("maintenance.proposals.reject", maintenance_proposals_reject)
