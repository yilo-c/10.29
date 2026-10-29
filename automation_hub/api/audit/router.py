"""审计 API 路由。

提供审计日志查询和记录接口。
"""

from __future__ import annotations
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from automation_hub.api.auth.deps import get_current_token, require_scope
from automation_hub.api.audit.service import log_event, query_events


class AuditEventCreate(BaseModel):
    event_type: str
    action: str
    status: str = "success"
    resource_type: str | None = None
    resource_id: str | None = None
    message: str | None = None
    meta: dict | None = None


router = APIRouter(prefix="/audit", tags=["audit"])


@router.post("/events")
def create_audit_event(payload: AuditEventCreate, token=Depends(require_scope("audit:read"))):
    """记录外部审计事件（例如 release gate 检查结果）。

    允许经过认证的客户端（持有 audit:read scope）向审计日志写入事件。
    用于 CI/CD 流水线、release_check 等外部流程记录门禁结果。
    """
    event_id = log_event(
        event_type=payload.event_type,
        action=payload.action,
        status=payload.status,
        actor_user_id=token.get("user_id"),
        actor_device_id=token.get("device_id"),
        resource_type=payload.resource_type,
        resource_id=payload.resource_id,
        message=payload.message,
        meta=payload.meta,
    )
    return {"event_id": event_id}


@router.get("")
def get_audit_events(
    event_type: str | None = Query(None, description="事件类型筛选"),
    resource_type: str | None = Query(None, description="资源类型筛选"),
    resource_id: str | None = Query(None, description="资源 ID 筛选"),
    actor_user_id: str | None = Query(None, description="操作者筛选"),
    action: str | None = Query(None, description="动作筛选"),
    status: str | None = Query(None, description="状态筛选（success/fail）"),
    since: str | None = Query(None, description="开始时间（ISO 格式）"),
    until: str | None = Query(None, description="结束时间（ISO 格式）"),
    limit: int = Query(100, description="返回数量限制", le=1000),
    offset: int = Query(0, description="分页偏移", ge=0),
    token=Depends(require_scope("audit:read"))
):
    """查询审计事件。
    
    支持多维度筛选：事件类型、资源类型、操作者、时间范围等。
    """
    events = query_events(
        event_type=event_type,
        resource_type=resource_type,
        resource_id=resource_id,
        actor_user_id=actor_user_id,
        action=action,
        status=status,
        since=since,
        until=until,
        limit=limit,
        offset=offset,
    )
    
    return {
        "events": events,
        "count": len(events)
    }
