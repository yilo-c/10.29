"""Mobile WebSocket routes — real-time approval push and chat for PWA.

Endpoints:
    WS  /mobile/ws             — WebSocket for approval notifications + chat
    POST /mobile/push/approval  — Desktop/mobile pushes an approval request

Architecture:
    Desktop creates approval → Desktop sends POST /mobile/push/approval
      → Server stores and broadcasts to connected phone WebSocket clients
      → Phone receives {type: "approval", ...} in real-time
      → Phone responds with {type: "approval_response", ...}
      → Server forwards response to desktop via /mobile/push/approval/response
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect
from pydantic import BaseModel

from automation_hub.api.auth.deps import get_current_token, require_scope

logger = logging.getLogger("automation_hub.api.mobile_ws")

router = APIRouter(prefix="/mobile", tags=["mobile_ws"])

# ── Connection manager ────────────────────────────────────────────────


class WSConnectionManager:
    """Manages active WebSocket connections per user."""

    def __init__(self):
        self._connections: dict[str, set[WebSocket]] = {}   # user_id -> set of WS

    async def connect(self, user_id: str, ws: WebSocket) -> None:
        await ws.accept()
        self._connections.setdefault(user_id, set()).add(ws)
        logger.info("WS connected: user=%s total=%d", user_id, self._count(user_id))

    def disconnect(self, user_id: str, ws: WebSocket) -> None:
        conns = self._connections.get(user_id)
        if conns:
            conns.discard(ws)
            if not conns:
                self._connections.pop(user_id, None)
        logger.info("WS disconnected: user=%s remaining=%d", user_id, self._count(user_id))

    async def broadcast_to_user(self, user_id: str, message: dict[str, Any]) -> int:
        """Send a message to all WS connections for a user. Returns sent count."""
        conns = self._connections.get(user_id)
        if not conns:
            return 0
        disconnected: list[WebSocket] = []
        data = json.dumps(message, ensure_ascii=False)
        for ws in conns:
            try:
                await ws.send_text(data)
            except Exception:
                disconnected.append(ws)
        for ws in disconnected:
            conns.discard(ws)
        return len(conns)

    def _count(self, user_id: str) -> int:
        return len(self._connections.get(user_id, set()))


_manager = WSConnectionManager()


# ---- P0-5 同步通知辅助（供 sync FastAPI 端点调用） ----


def notify_user_sync(user_id: str | None, message: dict[str, Any]) -> int:
    """从同步上下文向用户的所有 WS 客户端推送消息。

    与 ``worker.arq_settings.enqueue_job_sync`` 同样的桥接模式：
    在调用线程中新建一次性事件循环跑 async 广播。

    Args:
        user_id: 目标用户 ID；为 None 时直接返回 0。
        message: 待发送的消息字典。

    Returns:
        实际送达的连接数；无连接或异常返回 0。
    """
    if not user_id:
        return 0
    try:
        return asyncio.run(_manager.broadcast_to_user(user_id, message))
    except Exception:
        logger.exception("notify_user_sync failed for user=%s", user_id)
        return 0


# ── WebSocket endpoint ────────────────────────────────────────────────


@router.websocket("/ws")
async def mobile_websocket(ws: WebSocket):
    """Persistent WebSocket for PWA phone.

    Authentication via first message: {"type": "auth", "token": "..."}
    After auth, receives:
      - approval notifications
      - chat messages

    The phone can send:
      - {"type": "ping"} → keepalive
      - {"type": "approval_response", "approval_id": "...", "action": "approve|reject"}
      - {"type": "chat", "text": "..."}
    """
    user_id: str | None = None
    await ws.accept()

    try:
        # Wait for auth message (timeout: 10s)
        try:
            raw = await asyncio.wait_for(ws.receive_text(), timeout=10.0)
        except asyncio.TimeoutError:
            await ws.send_text(json.dumps({"type": "error", "message": "Auth timeout"}))
            await ws.close()
            return

        try:
            msg = json.loads(raw)
        except json.JSONDecodeError:
            await ws.send_text(json.dumps({"type": "error", "message": "Invalid JSON"}))
            await ws.close()
            return

        if msg.get("type") != "auth" or not msg.get("token"):
            await ws.send_text(json.dumps({"type": "error", "message": "Auth required"}))
            await ws.close()
            return

        # Validate token
        try:
            token_data = get_current_token(msg["token"])
            user_id = token_data.get("user_id")
            if not user_id:
                raise ValueError("No user_id in token")
        except Exception:
            await ws.send_text(json.dumps({"type": "error", "message": "Invalid token"}))
            await ws.close()
            return

        # Register connection
        await _manager.connect(user_id, ws)
        await ws.send_text(json.dumps({"type": "auth_ok", "user_id": user_id}))

        # Message loop
        async for raw in ws.iter_text():
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                continue

            msg_type = data.get("type", "")

            if msg_type == "ping":
                await ws.send_text(json.dumps({"type": "pong"}))

            elif msg_type == "approval_response":
                # Forward approval response to connected desktop
                approval_id = data.get("approval_id")
                action = data.get("action", "")
                await _handle_approval_response(user_id, approval_id, action, ws)

            elif msg_type == "chat":
                # Handle chat message from phone
                text = data.get("text", "")
                response = {
                    "type": "chat_reply",
                    "text": f"收到: {text}",
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                }
                await ws.send_text(json.dumps(response, ensure_ascii=False))

    except WebSocketDisconnect:
        pass
    except Exception as exc:
        logger.exception("WS error for user=%s", user_id)
    finally:
        if user_id:
            _manager.disconnect(user_id, ws)


# ── REST endpoints for push ───────────────────────────────────────────


class ApprovalPushRequest(BaseModel):
    approval_id: str
    title: str
    description: str = ""
    action_type: str = "approval"
    priority: str = "normal"


@router.post("/push/approval")
async def push_approval(
    payload: ApprovalPushRequest,
    token=Depends(require_scope("sync:push")),
):
    """Push an approval notification to the user's paired phone(s)."""
    user_id = token["user_id"]
    message = {
        "type": "approval",
        "approval_id": payload.approval_id,
        "title": payload.title,
        "description": payload.description,
        "action_type": payload.action_type,
        "priority": payload.priority,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    sent = await _manager.broadcast_to_user(user_id, message)
    return {"status": "ok", "sent_to_devices": sent}


@router.post("/push/notification")
async def push_notification(
    payload: dict[str, Any],
    token=Depends(require_scope("sync:push")),
):
    """Push a generic notification to the user's paired phone(s)."""
    user_id = token["user_id"]
    message = {
        "type": "notification",
        "title": payload.get("title", ""),
        "body": payload.get("body", ""),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    sent = await _manager.broadcast_to_user(user_id, message)
    return {"status": "ok", "sent_to_devices": sent}


# ── Helpers ────────────────────────────────────────────────────────────


async def _handle_approval_response(
    user_id: str,
    approval_id: str | None,
    action: str,
    ws: WebSocket,
) -> None:
    """Store approval response and notify desktop peers.

    P0-5 修复：原实现用了不存在的 ``decided_by`` 列与错误的 ``rejected``
    状态值（实际 schema 是 ``decided_by_user_id`` + ``denied``），且未走
    ``decide_approval`` 服务层导致 tool_runs 资源状态不联动。改为统一调用
    ``decide_approval`` 完成状态机闭合 + 资源级联 + 审计 + 通知。
    """
    if not approval_id or action not in ("approve", "reject"):
        await ws.send_text(json.dumps({
            "type": "error", "message": "Invalid approval response",
        }))
        return

    # P0-5：统一走 decide_approval 服务，确保 tool_runs 联动与审计
    from automation_hub.api.approvals.service import decide_approval
    new_status = "approved" if action == "approve" else "denied"
    try:
        row = decide_approval(
            approval_id,
            new_status,
            decided_by_user_id=user_id,
            note=f"mobile_ws response: {action}",
        )
    except ValueError:
        await ws.send_text(json.dumps({
            "type": "error", "message": "Approval not pending or not found",
        }))
        return

    # 审批通过后，对 run 资源触发续跑（与 REST /approve 端点行为一致）
    if row and row.get("resource_type") == "run" and row.get("resource_id") and new_status == "approved":
        import json as _json
        from automation_hub.worker.arq_settings import enqueue_job_sync
        from automation_hub.api.db import get_db_connection
        from datetime import datetime as _dt, timezone as _tz

        payload = row.get("payload_json") or {}
        if isinstance(payload, str):
            try:
                payload = _json.loads(payload)
            except Exception:
                payload = {}

        tool_id = payload.get("tool_id")
        args = payload.get("args") or {}
        run_user_id = row.get("created_by_user_id")
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
                run_user_id,
                job_timeout=180,
            )
            if job_id is None:
                with get_db_connection() as conn:
                    conn.execute(
                        "UPDATE tool_runs SET status='failed', finished_at=?, error_msg=? WHERE id=?",
                        (_dt.now(_tz.utc).isoformat(),
                         "enqueue failed after mobile approval: ARQ/Redis unavailable",
                         row["resource_id"]),
                    )
                    conn.commit()

    # Notify confirmation
    await ws.send_text(json.dumps({
        "type": "approval_response_ack",
        "approval_id": approval_id,
        "action": action,
        "status": "recorded",
    }))

    logger.info(
        "Approval %s %s by user=%s", approval_id, action, user_id,
    )
