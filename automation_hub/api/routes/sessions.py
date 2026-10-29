"""跨端会话续接 API。

提供手机 ↔ 桌面之间携带对话上下文切换的能力：

    GET    /sessions              — 获取用户的活跃会话列表
    GET    /sessions/latest       — 获取用户最后活跃的会话
    PUT    /sessions/{session_id} — 保存/更新会话状态
    GET    /sessions/{session_id} — 获取指定会话详情
    POST   /sessions/switch       — 检测设备切换并返回续接提示
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from automation_hub.api.auth.deps import require_scope
from automation_hub.api.db import get_db_connection

logger = logging.getLogger("automation_hub.api.sessions")

router = APIRouter(prefix="/sessions", tags=["sessions"])


def _ensure_table():
    with get_db_connection() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS session_contexts (
                session_id TEXT NOT NULL,
                user_id TEXT NOT NULL,
                device_type TEXT NOT NULL DEFAULT 'desktop',
                topic TEXT DEFAULT '',
                last_message TEXT DEFAULT '',
                vector_summary TEXT DEFAULT '',
                pending_actions TEXT DEFAULT '[]',
                message_count INTEGER DEFAULT 0,
                started_at TEXT DEFAULT '',
                updated_at TEXT DEFAULT '',
                metadata TEXT DEFAULT '{}',
                PRIMARY KEY (session_id, user_id)
            )
        """.strip())
        conn.commit()


class SessionSaveRequest(BaseModel):
    session_id: str
    device_type: str = "desktop"
    topic: str = ""
    last_message: str = ""
    vector_summary: str = ""
    pending_actions: list[dict[str, Any]] = []
    message_count: int = 0
    started_at: str = ""
    updated_at: str = ""
    metadata: dict[str, Any] = {}


class SessionSwitchRequest(BaseModel):
    device_type: str
    current_session_id: str | None = None


class SessionSwitchResponse(BaseModel):
    switch_detected: bool
    from_device: str | None = None
    to_device: str | None = None
    session_id: str | None = None
    topic: str | None = None
    last_message: str | None = None
    pending_actions_count: int = 0
    message_count: int = 0
    prompt: str | None = None


class SessionListItem(BaseModel):
    session_id: str
    device_type: str
    topic: str
    last_message: str
    message_count: int
    updated_at: str


class SessionListResponse(BaseModel):
    sessions: list[SessionListItem]
    count: int


def _row_to_dict(row) -> dict[str, Any]:
    return {
        "session_id": row[0],
        "user_id": row[1],
        "device_type": row[2],
        "topic": row[3],
        "last_message": row[4],
        "vector_summary": row[5],
        "pending_actions": json.loads(row[6]) if isinstance(row[6], str) else (row[6] or []),
        "message_count": row[7] or 0,
        "started_at": row[8] or "",
        "updated_at": row[9] or "",
        "metadata": json.loads(row[10]) if isinstance(row[10], str) else (row[10] or {}),
    }


@router.get("", response_model=SessionListResponse)
def list_sessions(token=Depends(require_scope("device:manage"))):
    """获取用户的所有活跃会话。"""
    _ensure_table()
    user_id = token["user_id"]
    with get_db_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM session_contexts WHERE user_id=? ORDER BY updated_at DESC LIMIT 50",
            (user_id,),
        ).fetchall()
    sessions = [SessionListItem(
        session_id=r[0],
        device_type=r[2],
        topic=r[3] or "",
        last_message=(r[4] or "")[:120],
        message_count=r[7] or 0,
        updated_at=r[9] or "",
    ) for r in rows]
    return SessionListResponse(sessions=sessions, count=len(sessions))


@router.get("/latest")
def get_latest_session(token=Depends(require_scope("device:manage"))):
    """获取用户最后活跃的会话（用于手机端检查续接）。"""
    _ensure_table()
    user_id = token["user_id"]
    with get_db_connection() as conn:
        row = conn.execute(
            "SELECT * FROM session_contexts WHERE user_id=? ORDER BY updated_at DESC LIMIT 1",
            (user_id,),
        ).fetchone()
    if row is None:
        return None
    return _row_to_dict(row)


@router.put("/{session_id}")
def save_session(
    session_id: str,
    payload: SessionSaveRequest,
    token=Depends(require_scope("device:manage")),
):
    """保存或更新会话状态（桌面/手机调用）。"""
    _ensure_table()
    user_id = token["user_id"]
    now = datetime.now(timezone.utc).isoformat()

    with get_db_connection() as conn:
        existing = conn.execute(
            "SELECT started_at FROM session_contexts WHERE session_id=? AND user_id=?",
            (session_id, user_id),
        ).fetchone()
        started_at = existing[0] if existing else (payload.started_at or now)

        conn.execute(
            """INSERT OR REPLACE INTO session_contexts
               (session_id, user_id, device_type, topic, last_message,
                vector_summary, pending_actions, message_count,
                started_at, updated_at, metadata)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                session_id, user_id, payload.device_type,
                payload.topic, payload.last_message,
                payload.vector_summary,
                json.dumps(payload.pending_actions, ensure_ascii=False),
                payload.message_count,
                started_at, now,
                json.dumps(payload.metadata, ensure_ascii=False),
            ),
        )
        conn.commit()

    return {"status": "ok", "session_id": session_id, "updated_at": now}


@router.get("/{session_id}")
def get_session(
    session_id: str,
    token=Depends(require_scope("device:manage")),
):
    """获取指定会话的完整详情。"""
    _ensure_table()
    user_id = token["user_id"]
    with get_db_connection() as conn:
        row = conn.execute(
            "SELECT * FROM session_contexts WHERE session_id=? AND user_id=?",
            (session_id, user_id),
        ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="session not found")
    return _row_to_dict(row)


@router.post("/switch", response_model=SessionSwitchResponse)
def detect_switch(
    payload: SessionSwitchRequest,
    token=Depends(require_scope("device:manage")),
):
    """检测设备切换并返回续接提示。

    手机端在连接后调用此端点，传入自己的 device_type。
    如果最后活跃会话来自另一设备类型，返回切换提示。
    """
    _ensure_table()
    user_id = token["user_id"]
    device_type = payload.device_type

    with get_db_connection() as conn:
        latest = conn.execute(
            "SELECT * FROM session_contexts WHERE user_id=? ORDER BY updated_at DESC LIMIT 1",
            (user_id,),
        ).fetchone()

    if latest is None:
        return SessionSwitchResponse(switch_detected=False)

    last_device = latest[2]  # device_type
    if last_device == device_type and payload.current_session_id == latest[0]:
        return SessionSwitchResponse(switch_detected=False)

    topic = latest[3] or ""
    last_msg = (latest[4] or "")[:120]
    pending = json.loads(latest[6]) if isinstance(latest[6], str) else (latest[6] or [])
    msg_count = latest[7] or 0

    return SessionSwitchResponse(
        switch_detected=True,
        from_device=last_device,
        to_device=device_type,
        session_id=latest[0],
        topic=topic,
        last_message=last_msg,
        pending_actions_count=len(pending),
        message_count=msg_count,
        prompt=(
            f"你在 {last_device} 上正在讨论"
            f"{f'「{topic}」' if topic else ''}"
            f"，要继续吗？"
            if last_device != device_type else None
        ),
    )
