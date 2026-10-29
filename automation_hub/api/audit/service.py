"""审计服务模块。

提供审计事件的记录和查询功能，所有关键操作都应记录审计日志。
"""

from __future__ import annotations
import uuid
import json
from datetime import datetime, timezone
from automation_hub.api.db import get_db_connection


def now_iso() -> str:
    """返回当前 UTC 时间的 ISO 格式字符串。"""
    return datetime.now(timezone.utc).isoformat()


def log_event(
    event_type: str,
    action: str,
    status: str,
    actor_user_id: str | None = None,
    actor_device_id: str | None = None,
    resource_type: str | None = None,
    resource_id: str | None = None,
    message: str | None = None,
    meta: dict | None = None
) -> str:
    """记录审计事件。
    
    Args:
        event_type: 事件类型（如 auth.token_created、tool.executed）
        action: 操作动作
        status: 状态（success/fail）
        actor_user_id: 操作者用户 ID
        actor_device_id: 操作者设备 ID
        resource_type: 资源类型
        resource_id: 资源 ID
        message: 事件消息
        meta: 附加元数据
        
    Returns:
        审计事件 ID
        
    Examples:
        >>> log_event(
        ...     event_type="tool.executed",
        ...     action="execute",
        ...     status="success",
        ...     actor_user_id="user-123",
        ...     resource_type="tool",
        ...     resource_id="backup_notes"
        ... )
    """
    event_id = str(uuid.uuid4())
    meta_json = json.dumps(meta or {}, ensure_ascii=False)
    
    with get_db_connection() as conn:
        conn.execute(
            """INSERT INTO audit_events(
               id, actor_user_id, actor_device_id, event_type,
               resource_type, resource_id, action, status,
               message, meta_json, created_at
               ) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
            (
                event_id,
                actor_user_id,
                actor_device_id,
                event_type,
                resource_type,
                resource_id,
                action,
                status,
                message,
                meta_json,
                now_iso()
            )
        )
        conn.commit()
    
    return event_id


def query_events(
    event_type: str | None = None,
    resource_type: str | None = None,
    resource_id: str | None = None,
    actor_user_id: str | None = None,
    action: str | None = None,
    status: str | None = None,
    since: str | None = None,
    until: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> list[dict]:
    """查询审计事件。
    
    Args:
        event_type: 事件类型筛选
        resource_type: 资源类型筛选
        actor_user_id: 操作者筛选
        since: 开始时间（ISO 格式）
        until: 结束时间（ISO 格式）
        limit: 返回数量限制
        
    Returns:
        审计事件列表
    """
    query = "SELECT * FROM audit_events WHERE 1=1"
    params = []
    
    if event_type:
        query += " AND event_type=?"
        params.append(event_type)
    
    if resource_type:
        query += " AND resource_type=?"
        params.append(resource_type)
    
    if actor_user_id:
        query += " AND actor_user_id=?"
        params.append(actor_user_id)

    if resource_id:
        query += " AND resource_id=?"
        params.append(resource_id)

    if action:
        query += " AND action=?"
        params.append(action)

    if status:
        query += " AND status=?"
        params.append(status)
    
    if since:
        query += " AND created_at>=?"
        params.append(since)
    
    if until:
        query += " AND created_at<=?"
        params.append(until)
    
    query += " ORDER BY created_at DESC LIMIT ? OFFSET ?"
    params.append(limit)
    params.append(int(offset or 0))
    
    with get_db_connection() as conn:
        rows = conn.execute(query, tuple(params)).fetchall()
        return [dict(row) for row in rows]
