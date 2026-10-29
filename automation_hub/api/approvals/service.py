from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timedelta, timezone
from threading import Event, Thread

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from automation_hub.api.audit.service import log_event
from automation_hub.api.config import settings
from automation_hub.api.db.connection import now_iso
from automation_hub.api.db.session import get_db_session, create_approval_request, get_approval_request_by_id
from automation_hub.api.models.approvals import ApprovalRequest
from automation_hub.api.models.tools import ToolRun
from automation_hub.api.models.repos import Proposal


logger = logging.getLogger(__name__)


def _to_approval_dict(a: ApprovalRequest) -> dict:
    """将 ORM ApprovalRequest 转换为普通字典。"""
    return {
        "id": a.id,
        "created_by_user_id": a.created_by_user_id,
        "created_by_device_id": a.created_by_device_id,
        "resource_type": a.resource_type,
        "resource_id": a.resource_id,
        "action": a.action,
        "risk_level": a.risk_level,
        "request_reason": a.request_reason,
        "payload_json": a.payload_json,
        "status": a.status,
        "created_at": a.created_at.isoformat() if a.created_at else None,
        "decided_at": a.decided_at.isoformat() if a.decided_at else None,
        "decided_by_user_id": a.decided_by_user_id,
        "decision_note": a.decision_note,
    }


def _advance_linked_resource_to_terminal(
    session: Session, approval_id: str, decision_status: str, note: str, decided_at: str
) -> None:
    reason = f"approval {decision_status}"
    if note:
        reason = f"{reason}: {note}"

    # 查找审批对应的资源
    approval = session.get(ApprovalRequest, approval_id)
    if not approval:
        return

    resource_type = approval.resource_type
    resource_id = approval.resource_id

    if resource_type == "run" and resource_id:
        run = session.get(ToolRun, resource_id)
        if run and run.status not in {"succeeded", "failed", "denied"}:
            run.status = "denied"
            if not run.finished_at:
                run.finished_at = decided_at
            if not run.error_msg:
                run.error_msg = reason
        return

    if resource_type == "proposal" and resource_id:
        prop = session.get(Proposal, resource_id)
        if prop and prop.status not in {"applied", "rejected"}:
            prop.status = "rejected"


def create_approval(user_id: str, device_id: str | None, resource_type: str, resource_id: str,
                    action: str, risk_level: str, reason: str, payload: dict) -> str:
    rid = str(uuid.uuid4())
    with get_db_session() as session:
        create_approval_request(
            session, rid, user_id, device_id,
            resource_type, resource_id, action, risk_level, reason, payload,
        )
    return rid


def reconcile_pending_approvals() -> list[dict]:
    ttl_seconds = int(settings.APPROVAL_TTL_SECONDS or 0)
    if ttl_seconds <= 0:
        return []

    cutoff = datetime.now(timezone.utc) - timedelta(seconds=ttl_seconds)
    expired: list[dict] = []

    with get_db_session() as session:
        stmt = (
            select(ApprovalRequest)
            .where(ApprovalRequest.status == "pending")
            .order_by(ApprovalRequest.created_at.asc())
        )
        rows = list(session.execute(stmt).scalars())

        for approval in rows:
            created_at = approval.created_at
            if not created_at or created_at.replace(tzinfo=timezone.utc) > cutoff:
                continue

            approval.status = "expired"
            approval.decided_at = datetime.now(timezone.utc)
            approval.decided_by_user_id = None
            approval.decision_note = "expired by ttl"
            _advance_linked_resource_to_terminal(
                session, approval.id, "expired", "expired by ttl", now_iso()
            )
            expired.append(_to_approval_dict(approval))

        # 不需要手动 commit，get_db_session 自动提交

    for approval in expired:
        log_event(
            event_type="approval.expired",
            action="expire",
            status="success",
            resource_type="approval",
            resource_id=approval["id"],
            message="approval expired",
            meta={
                "resource_type": approval.get("resource_type"),
                "resource_id": approval.get("resource_id"),
                "decision_note": approval.get("decision_note"),
            },
        )

    return expired


def get_approval_by_id(approval_id: str) -> dict | None:
    with get_db_session() as session:
        approval = get_approval_request_by_id(session, approval_id)
        return _to_approval_dict(approval) if approval else None


def decide_approval(approval_id: str, new_status: str, *, decided_by_user_id: str | None, note: str = "") -> dict | None:
    with get_db_session() as session:
        approval = get_approval_request_by_id(session, approval_id)
        if not approval:
            return None

        if approval.status != "pending":
            raise ValueError("not pending")

        approval.status = new_status
        approval.decided_at = datetime.now(timezone.utc)
        approval.decided_by_user_id = decided_by_user_id
        approval.decision_note = note

        if new_status in {"denied", "expired", "cancelled"}:
            _advance_linked_resource_to_terminal(
                session, approval.id, new_status, note, now_iso()
            )

        return _to_approval_dict(approval)


def list_pending_approvals() -> list[dict]:
    """获取所有待处理的审批记录（纯查询，无过期副作用）。"""
    with get_db_session() as session:
        stmt = (
            select(ApprovalRequest)
            .where(ApprovalRequest.status == "pending")
            .order_by(ApprovalRequest.created_at.asc())
        )
        return [_to_approval_dict(a) for a in session.execute(stmt).scalars()]


def update_approval_status(
    approval_id: str, new_status: str,
    decided_by_user_id: str | None, decision_note: str,
) -> dict | None:
    """直接更新审批状态（无资源级联，纯状态变更）。"""
    with get_db_session() as session:
        approval = get_approval_request_by_id(session, approval_id)
        if not approval:
            return None

        if approval.status != "pending":
            raise ValueError("not pending")

        approval.status = new_status
        approval.decided_at = datetime.now(timezone.utc)
        approval.decided_by_user_id = decided_by_user_id
        approval.decision_note = decision_note

        return _to_approval_dict(approval)


def _approval_expirer_loop(stop_event: Event) -> None:
    interval = max(1, int(settings.APPROVAL_EXPIRER_INTERVAL_SECONDS or 60))
    while not stop_event.is_set():
        try:
            expired = reconcile_pending_approvals()
            if expired:
                logger.info("expired %s stale approvals", len(expired))
        except Exception:
            logger.exception("approval expirer loop failed")

        if stop_event.wait(interval):
            break


def start_approval_expirer(stop_event: Event) -> Thread | None:
    if int(settings.APPROVAL_TTL_SECONDS or 0) <= 0:
        return None

    thread = Thread(target=_approval_expirer_loop, args=(stop_event,), daemon=True, name="approval-expirer")
    thread.start()
    return thread
