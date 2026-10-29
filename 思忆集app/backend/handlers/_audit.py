# -*- coding: utf-8 -*-
"""Audit-related JSON-RPC handlers: list, get, list_rollbackable, rollback, stats.

S15: 操作审计日志查询与回滚接口。
依赖 context.audit_trail 和 context.rollback_manager（在 headless_main.py 中初始化）。
若审计组件未初始化（初始化失败），所有方法安全降级返回错误信息，不影响后端运行。
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

logger = logging.getLogger("siyi.backend.handlers.audit")

# 列表接口的快照预览长度（2026-10-15，U1 实测驱动）
_SNAPSHOT_PREVIEW_CHARS = 200


def _summarize_for_list(entry: Any) -> Any:
    """把审计条目的大快照字段压成预览（列表接口专用）。

    为什么：``before_snapshot`` / ``after_snapshot`` 存的是**完整变更前后内容**
    （实测单条最大 66KB——``notes.update`` 存了笔记全文），100 条 → 712KB 响应，
    逼近 WS 1MB 帧上限（超限会打死整条连接）。
    前端 ``AuditEntry`` 接口（ManagementModule.tsx:16-27）**零引用**这两个字段
    （2026-10-15 全文核对），详情走 ``audit.get``。故列表只给预览并标记
    ``*_truncated``，既不破坏结构也不丢信息。
    """
    if not isinstance(entry, dict):
        return entry
    out = dict(entry)
    for key in ("before_snapshot", "after_snapshot"):
        snap = entry.get(key)
        if snap is None:
            continue
        text = snap if isinstance(snap, str) else json.dumps(snap, ensure_ascii=False)
        if len(text) > _SNAPSHOT_PREVIEW_CHARS:
            out[key] = text[:_SNAPSHOT_PREVIEW_CHARS]
            out[f"{key}_truncated"] = True
            out[f"{key}_chars"] = len(text)
    return out


def register_audit(registry, context) -> None:
    """Register audit-related JSON-RPC methods."""

    def _get_audit_trail():
        return getattr(context, "audit_trail", None)

    def _get_rollback_manager():
        return getattr(context, "rollback_manager", None)

    async def audit_list(
        limit: int = 50,
        action_id: str = "",
        status: str = "",
        **kwargs: Any,
    ) -> "dict[str, Any]":
        """审计记录列表（摘要）。

        2026-10-15（U1 实测）：默认 limit 由 100 降为 50——单条审计记录实测
        ~7KB（含请求/结果载荷），100 条 → 712KB 响应，逼近 WS 1MB 帧上限。
        前端（ManagementModule）本就传 limit=50，故默认值与前端对齐不改变行为。
        """
        trail = _get_audit_trail()
        if trail is None:
            return {"error": "audit trail not initialized", "entries": []}
        try:
            entries = await asyncio.to_thread(
                trail.list_entries,
                limit=min(max(int(limit), 1), 500),
                action_id=action_id,
                status=status,
            )
            # 大快照压成预览（见 _summarize_for_list 注释）：712KB → ~30KB
            return {"entries": [_summarize_for_list(e) for e in entries], "count": len(entries)}
        except Exception as exc:
            logger.warning("audit.list failed: %s", exc)
            return {"error": str(exc), "entries": []}

    async def audit_get(entry_id: str = "", **kwargs: Any) -> "dict[str, Any] | None":
        trail = _get_audit_trail()
        if trail is None:
            return {"error": "audit trail not initialized"}
        if not entry_id:
            return {"error": "entry_id is required"}
        try:
            return await asyncio.to_thread(trail.get_entry, entry_id)
        except Exception as exc:
            logger.warning("audit.get failed: %s", exc)
            return {"error": str(exc)}

    async def audit_list_rollbackable(
        limit: int = 50,
        **kwargs: Any,
    ) -> "dict[str, Any]":
        trail = _get_audit_trail()
        if trail is None:
            return {"error": "audit trail not initialized", "entries": []}
        try:
            entries = await asyncio.to_thread(
                trail.list_rollbackable,
                min(max(int(limit), 1), 200),
            )
            return {"entries": entries, "count": len(entries)}
        except Exception as exc:
            logger.warning("audit.list_rollbackable failed: %s", exc)
            return {"error": str(exc), "entries": []}

    async def audit_can_rollback(entry_id: str = "", **kwargs: Any) -> "dict[str, Any]":
        rm = _get_rollback_manager()
        if rm is None:
            return {"can_rollback": False, "reason": "rollback manager not initialized"}
        if not entry_id:
            return {"can_rollback": False, "reason": "entry_id is required"}
        try:
            return await asyncio.to_thread(rm.can_rollback, entry_id)
        except Exception as exc:
            logger.warning("audit.can_rollback failed: %s", exc)
            return {"can_rollback": False, "reason": str(exc)}

    async def audit_rollback(entry_id: str, **kwargs: Any) -> "dict[str, Any]":
        rm = _get_rollback_manager()
        if rm is None:
            return {"rolled_back": False, "reason": "rollback manager not initialized"}
        if not entry_id:
            return {"rolled_back": False, "reason": "entry_id is required"}
        try:
            result = await asyncio.to_thread(rm.rollback, entry_id)
            if result.get("rolled_back"):
                logger.info("audit rollback ok: entry_id=%s", entry_id)
            else:
                logger.warning(
                    "audit rollback refused: entry_id=%s reason=%s",
                    entry_id,
                    result.get("reason"),
                )
            return result
        except Exception as exc:
            logger.exception("audit.rollback failed: entry_id=%s", entry_id)
            return {"rolled_back": False, "reason": str(exc)}

    async def audit_batch_rollback(
        entry_ids: "list[str]",
        **kwargs: Any,
    ) -> "dict[str, Any]":
        rm = _get_rollback_manager()
        if rm is None:
            return {
                "rolled_back": 0,
                "reason": "rollback manager not initialized",
                "total": len(entry_ids or []),
            }
        if not isinstance(entry_ids, list) or not entry_ids:
            return {
                "rolled_back": 0,
                "reason": "entry_ids must be a non-empty list",
                "total": 0,
            }
        try:
            return await asyncio.to_thread(rm.batch_rollback, entry_ids)
        except Exception as exc:
            logger.exception("audit.batch_rollback failed")
            return {
                "rolled_back": 0,
                "reason": str(exc),
                "total": len(entry_ids),
            }

    async def audit_stats(**kwargs: Any) -> "dict[str, Any]":
        trail = _get_audit_trail()
        if trail is None:
            return {"error": "audit trail not initialized"}

        def _compute_stats():
            # 台账修正（2026-10-16）："拉 2000 条 ≈10MB"不成立——_merged_entries
            # 受 MAX_TRAIL_SIZE=400 逻辑上限保护，实际 ≤400 条（~2MB），且 U1 已
            # 确认本方法响应无帧风险。SQL 聚合（COUNT/GROUP BY）会漏掉 pending
            # 内存条目（_merged 合并语义），不值得为此加 writer 聚合接口。
            # limit 与 MAX_TRAIL_SIZE 对齐（原 2000 纯属误导）。
            entries = trail.list_entries(limit=400)
            by_status: dict[str, int] = {}
            by_operation: dict[str, int] = {}
            by_target_type: dict[str, int] = {}
            for e in entries:
                s = str(e.get("status", "unknown"))
                by_status[s] = by_status.get(s, 0) + 1
                op = str(e.get("operation", "unknown"))
                by_operation[op] = by_operation.get(op, 0) + 1
                tt = str(e.get("target_type", "unknown"))
                by_target_type[tt] = by_target_type.get(tt, 0) + 1
            rollbackable = trail.list_rollbackable(limit=500)
            return {
                "total": len(entries),
                "by_status": by_status,
                "by_operation": by_operation,
                "by_target_type": by_target_type,
                "rollbackable_count": len(rollbackable),
            }

        try:
            return await asyncio.to_thread(_compute_stats)
        except Exception as exc:
            logger.warning("audit.stats failed: %s", exc)
            return {"error": str(exc)}

    # P2 修复（rpc-census-2026-08-30）：audit.get / audit.can_rollback 原签名
    # 要求必填位置参数 entry_id，registry.dispatch 以 handler(**params) 展开调用，
    # 空参直接 TypeError（-32603 broken）。改为签名默认值（直调兜底走既有
    # entry_id is required 降级）+ 注册层 JSON Schema required=entry_id
    # （空参返回干净 -32602 InvalidParamsError）。
    _required_entry_id: dict[str, Any] = {"type": "object", "required": ["entry_id"]}
    registry.register("audit.list", audit_list)
    registry.register_with_schema("audit.get", audit_get, _required_entry_id)
    registry.register("audit.list_rollbackable", audit_list_rollbackable)
    registry.register_with_schema("audit.can_rollback", audit_can_rollback, _required_entry_id)
    registry.register("audit.rollback", audit_rollback)
    registry.register("audit.batch_rollback", audit_batch_rollback)
    registry.register("audit.stats", audit_stats)
    logger.info("audit handlers registered (S15): audit.list/get/list_rollbackable/can_rollback/rollback/batch_rollback/stats")
