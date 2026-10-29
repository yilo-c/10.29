# -*- coding: utf-8 -*-
"""审批相关的数据库操作（SQLAlchemy 2.0版本）"""

import json
import uuid
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any

from .session import get_db_session
from ..models.approvals import ApprovalRequest


def now_iso() -> str:
    """获取当前ISO格式时间字符串"""
    return datetime.now(timezone.utc).isoformat()


def create_approval(
    user_id: str,
    device_id: Optional[str],
    resource_type: str,
    resource_id: str,
    action: str,
    risk_level: str,
    reason: str,
    payload: Dict[str, Any]
) -> str:
    """创建审批请求"""
    approval_id = str(uuid.uuid4())
    
    with get_db_session() as session:
        from .session import create_approval_request
        create_approval_request(
            session,
            approval_id,
            user_id,
            device_id,
            resource_type,
            resource_id,
            action,
            risk_level,
            reason,
            payload
        )
    
    return approval_id


def get_approval_by_id(approval_id: str) -> Optional[Dict[str, Any]]:
    """根据ID获取审批请求"""
    with get_db_session() as session:
        approval = session.get(ApprovalRequest, approval_id)
        if approval:
            return {
                "id": approval.id,
                "created_by_user_id": approval.created_by_user_id,
                "created_by_device_id": approval.created_by_device_id,
                "resource_type": approval.resource_type,
                "resource_id": approval.resource_id,
                "action": approval.action,
                "risk_level": approval.risk_level,
                "request_reason": approval.request_reason,
                "payload_json": approval.payload_json,
                "status": approval.status,
                "created_at": approval.created_at.isoformat(),
                "decided_at": approval.decided_at.isoformat() if approval.decided_at else None,
                "decided_by_user_id": approval.decided_by_user_id,
                "decision_note": approval.decision_note
            }
        return None


def list_pending_approvals() -> List[Dict[str, Any]]:
    """获取所有待处理的审批请求"""
    with get_db_session() as session:
        from sqlalchemy import select
        stmt = select(ApprovalRequest).where(ApprovalRequest.status == "pending").order_by(ApprovalRequest.created_at.asc())
        approvals = session.execute(stmt).scalars().all()
        
        result = []
        for approval in approvals:
            result.append({
                "id": approval.id,
                "created_by_user_id": approval.created_by_user_id,
                "created_by_device_id": approval.created_by_device_id,
                "resource_type": approval.resource_type,
                "resource_id": approval.resource_id,
                "action": approval.action,
                "risk_level": approval.risk_level,
                "request_reason": approval.request_reason,
                "payload_json": approval.payload_json,
                "status": approval.status,
                "created_at": approval.created_at.isoformat(),
                "decided_at": approval.decided_at.isoformat() if approval.decided_at else None,
                "decided_by_user_id": approval.decided_by_user_id,
                "decision_note": approval.decision_note
            })
        return result


def update_approval_status(
    approval_id: str,
    new_status: str,
    decided_by_user_id: Optional[str],
    decision_note: str
) -> Optional[Dict[str, Any]]:
    """更新审批请求状态"""
    with get_db_session() as session:
        approval = session.get(ApprovalRequest, approval_id)
        if not approval:
            return None
        
        if approval.status != "pending":
            raise ValueError("not pending")
        
        approval.status = new_status
        approval.decided_by_user_id = decided_by_user_id
        approval.decision_note = decision_note
        approval.decided_at = datetime.now(timezone.utc)
        
        return {
            "id": approval.id,
            "created_by_user_id": approval.created_by_user_id,
            "created_by_device_id": approval.created_by_device_id,
            "resource_type": approval.resource_type,
            "resource_id": approval.resource_id,
            "action": approval.action,
            "risk_level": approval.risk_level,
            "request_reason": approval.request_reason,
            "payload_json": approval.payload_json,
            "status": approval.status,
            "created_at": approval.created_at.isoformat(),
            "decided_at": approval.decided_at.isoformat() if approval.decided_at else None,
            "decided_by_user_id": approval.decided_by_user_id,
            "decision_note": approval.decision_note
        }