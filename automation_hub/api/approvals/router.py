from __future__ import annotations
import json
import logging
from fastapi import APIRouter, Depends, HTTPException
from automation_hub.api.auth.deps import require_scope
from automation_hub.api.audit.service import log_event
from automation_hub.api.db import get_db_connection
from automation_hub.api.approvals.service import decide_approval, get_approval_by_id, reconcile_pending_approvals
from automation_hub.api.config import settings
from automation_hub.api._worker_bridge import enqueue_job_sync
from automation_hub.api.routes.mobile_ws import notify_user_sync
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/approvals", tags=["approvals"])


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def _notify_approval_decision(
    *,
    decision: str,
    row: dict | None,
    actor_user_id: str | None,
    note: str,
    extra: dict | None = None,
) -> None:
    """P0-5：审批决策后向审批发起人推送实时通知。

    通过 ``mobile_ws.notify_user_sync`` 走 WS 广播；无在线客户端时静默
    返回 0（审计日志已持久化决策，不丢数据）。
    """
    if not row:
        return
    target_user_id = row.get("created_by_user_id")
    if not target_user_id:
        return
    message = {
        "type": "approval_decided",
        "approval_id": row.get("id"),
        "decision": decision,  # approved / denied / cancelled
        "resource_type": row.get("resource_type"),
        "resource_id": row.get("resource_id"),
        "action": row.get("action"),
        "decided_by_user_id": actor_user_id,
        "note": note,
        "timestamp": now_iso(),
    }
    if extra:
        message.update(extra)
    try:
        notify_user_sync(target_user_id, message)
    except Exception:
        # 通知失败不影响审批主流程
        logger.exception("approval decision notify failed user=%s", target_user_id)


@router.get("")
def list_approvals(status: str = "pending", token=Depends(require_scope("approval:read"))):
    reconcile_pending_approvals()
    with get_db_connection() as conn:
        rows = conn.execute("SELECT * FROM approval_requests WHERE status=? ORDER BY created_at DESC", (status,)).fetchall()
        approvals = [dict(r) for r in rows]
        return {"approvals": approvals, "count": len(approvals)}


@router.get("/{approval_id}")
def get_approval(approval_id: str, token=Depends(require_scope("approval:read"))):
    reconcile_pending_approvals()
    row = get_approval_by_id(approval_id)
    if not row:
        raise HTTPException(404, "not found")
    return row


@router.post("/{approval_id}/approve")
def approve(approval_id: str, note: str = "", token=Depends(require_scope("approval:decide"))):
    reconcile_pending_approvals()
    try:
        row = decide_approval(approval_id, "approved", decided_by_user_id=token["user_id"], note=note)
    except ValueError:
        raise HTTPException(409, "not pending")
    if not row:
        raise HTTPException(404, "not found")

    if row.get("resource_type") == "run" and row.get("resource_id"):
        payload = row.get("payload_json") or {}
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except Exception:
                payload = {}

        tool_id = payload.get("tool_id")
        args = payload.get("args") or {}
        user_id = row.get("created_by_user_id")
        if tool_id:
            with get_db_connection() as conn:
                conn.execute(
                    "UPDATE tool_runs SET status='queued', finished_at=NULL, error_msg=NULL WHERE id=?",
                    (row["resource_id"],),
                )
                conn.commit()
            job_id = enqueue_job_sync(
                "arq_run_tool_job",
                row["resource_id"],
                tool_id,
                args,
                user_id,
                job_timeout=180,
            )
            # P0-4：入队失败时把 run 置为 failed，避免审批通过后永久卡 queued
            if job_id is None:
                with get_db_connection() as conn:
                    conn.execute(
                        "UPDATE tool_runs SET status='failed', finished_at=?, error_msg=? WHERE id=?",
                        (now_iso(), "enqueue failed after approval: ARQ/Redis unavailable", row["resource_id"]),
                    )
                    conn.commit()
                logger.error("enqueue failed for run %s after approval, marked as failed", row["resource_id"])

    log_event(
        event_type="approval.decided",
        action="approve",
        status="success",
        actor_user_id=token.get("user_id"),
        actor_device_id=token.get("device_id"),
        resource_type="approval",
        resource_id=approval_id,
        message="approval approved",
        meta={"decision": "approved", "note": note},
    )
    # P0-5：审批通过 → 续跑已由 enqueue 触发，通知发起人决策结果
    _notify_approval_decision(
        decision="approved",
        row=row,
        actor_user_id=token.get("user_id"),
        note=note,
        extra={"run_resumed": row.get("resource_type") == "run"},
    )
    return {"approved": True}


@router.post("/{approval_id}/deny")
def deny(approval_id: str, note: str = "", token=Depends(require_scope("approval:decide"))):
    reconcile_pending_approvals()
    try:
        row = decide_approval(approval_id, "denied", decided_by_user_id=token["user_id"], note=note)
    except ValueError:
        raise HTTPException(409, "not pending")
    if not row:
        raise HTTPException(404, "not found")

    log_event(
        event_type="approval.decided",
        action="deny",
        status="success",
        actor_user_id=token.get("user_id"),
        actor_device_id=token.get("device_id"),
        resource_type="approval",
        resource_id=approval_id,
        message="approval denied",
        meta={"decision": "denied", "note": note},
    )
    # P0-5：审批拒绝 → decide_approval 已置 tool_runs.status='denied'，
    # 这里向发起人推送拒绝通知（含 run_id 供前端更新 UI）
    _notify_approval_decision(
        decision="denied",
        row=row,
        actor_user_id=token.get("user_id"),
        note=note,
    )
    return {"denied": True}


@router.post("/{approval_id}/cancel")
def cancel(approval_id: str, note: str = "", token=Depends(require_scope("approval:decide"))):
    reconcile_pending_approvals()
    try:
        row = decide_approval(approval_id, "cancelled", decided_by_user_id=token["user_id"], note=note)
    except ValueError:
        raise HTTPException(409, "not pending")
    if not row:
        raise HTTPException(404, "not found")

    log_event(
        event_type="approval.decided",
        action="cancel",
        status="success",
        actor_user_id=token.get("user_id"),
        actor_device_id=token.get("device_id"),
        resource_type="approval",
        resource_id=approval_id,
        message="approval cancelled",
        meta={"decision": "cancelled", "note": note},
    )
    # P0-5：取消也通知发起人（与 deny 行为一致，资源已置 denied）
    _notify_approval_decision(
        decision="cancelled",
        row=row,
        actor_user_id=token.get("user_id"),
        note=note,
    )
    return {"cancelled": True}
