"""
Agent Nodes API 路由

提供本地代理节点的注册、心跳、策略下发、会话事件、执行授权等接口。
"""

from __future__ import annotations

import json
import uuid
import logging
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from pydantic import BaseModel, Field

from automation_hub.api.db import get_db_connection
from automation_hub.api.auth.deps import require_scope, hash_token
from automation_hub.api.auth.agent_node_deps import require_scope_or_agent_node

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/agent-nodes", tags=["agent-nodes"])


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_id() -> str:
    return str(uuid.uuid4())


# =====================================================================
# Pydantic 模型
# =====================================================================

class NodeRegisterRequest(BaseModel):
    hostname: str
    labels: dict[str, str] = Field(default_factory=dict)
    capabilities: list[str] = Field(default_factory=list)


class NodeRegisterResponse(BaseModel):
    node_id: str
    issued_token: str
    policy_version: str


class HeartbeatRequest(BaseModel):
    status: str = "online"
    active_runs: int = 0
    cpu_percent: float = 0.0
    mem_percent: float = 0.0


class HeartbeatResponse(BaseModel):
    ack: bool = True
    server_time: str
    commands: list[dict[str, Any]] = Field(default_factory=list)


class PolicyBundleResponse(BaseModel):
    version: str
    allowed_tools: list[str]
    denied_tools: list[str]
    path_allowlist: list[str]
    path_denylist: list[str]
    risk_auto_approve: list[str]
    max_concurrent_runs: int


class SessionEvent(BaseModel):
    type: str
    session_id: str
    data: dict[str, Any] = Field(default_factory=dict)
    timestamp: str = ""


class SessionEventsRequest(BaseModel):
    events: list[SessionEvent]


class ExecutionAuthorizationRequest(BaseModel):
    node_id: str
    tool_name: str
    args: dict[str, Any] = Field(default_factory=dict)
    risk_level: str = "low"


class ExecutionAuthorizationResponse(BaseModel):
    authorization_id: str
    decision: str  # approved | denied | pending
    reason: str = ""


class RunClaimRequest(BaseModel):
    node_id: str


class RunClaimResponse(BaseModel):
    status: str  # claimed | already_claimed | not_found
    run_id: str = ""
    tool_name: str = ""
    args: dict[str, Any] = Field(default_factory=dict)


class RunEvent(BaseModel):
    type: str  # output | completed | failed
    data: str = ""


class RunEventsRequest(BaseModel):
    events: list[RunEvent]


class DrainRequest(BaseModel):
    reason: str = ""


class RevokeRequest(BaseModel):
    reason: str = ""


# =====================================================================
# 1. 节点注册
# =====================================================================

@router.post("/register", response_model=NodeRegisterResponse)
def _register_node(
    req: NodeRegisterRequest,
    token: dict = Depends(require_scope("agent-nodes:write")),
) -> NodeRegisterResponse:
    node_id = _new_id()
    issued_token = _new_id()
    now = _now_iso()
    policy_version = "v0"

    with get_db_connection() as conn:
        conn.execute(
            """INSERT INTO agent_nodes
               (id, hostname, labels_json, capabilities_json, issued_token_hash,
                status, policy_version, cpu_percent, mem_percent, active_runs,
                registered_at, last_heartbeat_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                node_id,
                req.hostname,
                json.dumps(req.labels, ensure_ascii=False),
                json.dumps(req.capabilities, ensure_ascii=False),
                hash_token(issued_token),
                "online",
                policy_version,
                0.0,
                0.0,
                0,
                now,
                now,
            ),
        )
        conn.commit()

    logger.info("node registered: %s (%s)", node_id, req.hostname)
    return NodeRegisterResponse(node_id=node_id, issued_token=issued_token, policy_version=policy_version)


# =====================================================================
# 2. 心跳
# =====================================================================

@router.post("/{node_id}/heartbeat", response_model=HeartbeatResponse)
def _heartbeat(
    node_id: str,
    req: HeartbeatRequest,
    token: dict = Depends(require_scope_or_agent_node("agent-nodes:write")),
) -> HeartbeatResponse:
    if token.get("actor_type") == "node" and token.get("id") != node_id:
        raise HTTPException(status_code=403, detail="node token does not match node_id")

    now = _now_iso()
    with get_db_connection() as conn:
        row = conn.execute("SELECT id FROM agent_nodes WHERE id=?", (node_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="node not found")
        conn.execute(
            """UPDATE agent_nodes
               SET status=?, last_heartbeat_at=?, cpu_percent=?, mem_percent=?, active_runs=?
               WHERE id=?""",
            (req.status, now, req.cpu_percent, req.mem_percent, req.active_runs, node_id),
        )
        conn.commit()

    return HeartbeatResponse(ack=True, server_time=now)


# =====================================================================
# 3. 策略下发
# =====================================================================

@router.get("/{node_id}/policy-bundle", response_model=PolicyBundleResponse)
def _get_policy_bundle(
    node_id: str,
    token: dict = Depends(require_scope_or_agent_node("agent-nodes:read")),
) -> PolicyBundleResponse:
    if token.get("actor_type") == "node" and token.get("id") != node_id:
        raise HTTPException(status_code=403, detail="node token does not match node_id")

    with get_db_connection() as conn:
        row = conn.execute("SELECT id FROM agent_nodes WHERE id=?", (node_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="node not found")

    # 默认策略（后续可从 DB 动态查询）
    return PolicyBundleResponse(
        version="v0",
        allowed_tools=["shell", "powershell", "filesystem", "logs", "process", "docker", "k8s", "ssh"],
        denied_tools=[],
        path_allowlist=[],
        path_denylist=["/etc/shadow", "/root/.ssh"],
        risk_auto_approve=["low"],
        max_concurrent_runs=5,
    )


# =====================================================================
# 4. 会话事件
# =====================================================================

@router.post("/{node_id}/sessions/{session_id}/events")
def _push_session_events(
    node_id: str,
    session_id: str,
    req: SessionEventsRequest,
    token: dict = Depends(require_scope_or_agent_node("agent-nodes:write")),
) -> dict[str, Any]:
    if token.get("actor_type") == "node" and token.get("id") != node_id:
        raise HTTPException(status_code=403, detail="node token does not match node_id")

    now = _now_iso()
    with get_db_connection() as conn:
        for evt in req.events:
            conn.execute(
                """INSERT INTO agent_node_session_events
                   (id, node_id, session_id, event_type, data_json, created_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    _new_id(),
                    node_id,
                    session_id,
                    evt.type,
                    json.dumps(evt.data, ensure_ascii=False),
                    evt.timestamp or now,
                ),
            )
        conn.commit()
    return {"accepted": len(req.events)}


# =====================================================================
# 5. 执行授权
# =====================================================================

@router.post("/execution-authorizations", response_model=ExecutionAuthorizationResponse)
def _request_authorization(
    req: ExecutionAuthorizationRequest,
    token: dict = Depends(require_scope_or_agent_node("agent-nodes:write")),
) -> ExecutionAuthorizationResponse:
    if token.get("actor_type") == "node" and token.get("id") != req.node_id:
        raise HTTPException(status_code=403, detail="node token does not match request node_id")

    auth_id = _new_id()
    now = _now_iso()

    # 风险判定：low → 自动批准；其它 → pending（生产环境接入审批链）
    if req.risk_level == "low":
        decision = "approved"
    else:
        decision = "pending"

    with get_db_connection() as conn:
        conn.execute(
            """INSERT INTO execution_authorizations
               (id, node_id, tool_name, args_json, risk_level, decision, created_at, decided_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                auth_id,
                req.node_id,
                req.tool_name,
                json.dumps(req.args, ensure_ascii=False),
                req.risk_level,
                decision,
                now,
                now if decision == "approved" else None,
            ),
        )
        conn.commit()

    return ExecutionAuthorizationResponse(authorization_id=auth_id, decision=decision)


# =====================================================================
# 6. Run claim & events
# =====================================================================

@router.post("/runs/{run_id}/claim", response_model=RunClaimResponse)
def _claim_run(
    run_id: str,
    req: RunClaimRequest,
    token: dict = Depends(require_scope_or_agent_node("agent-nodes:write")),
) -> RunClaimResponse:
    if token.get("actor_type") == "node" and token.get("id") != req.node_id:
        raise HTTPException(status_code=403, detail="node token does not match request node_id")

    with get_db_connection() as conn:
        row = conn.execute(
            "SELECT * FROM tool_runs WHERE id=?", (run_id,)
        ).fetchone()
        if not row:
            return RunClaimResponse(status="not_found")
        run = dict(row)
        if run.get("claimed_by"):
            return RunClaimResponse(status="already_claimed")

        conn.execute(
            "UPDATE tool_runs SET claimed_by=?, status='running' WHERE id=?",
            (req.node_id, run_id),
        )
        conn.commit()

    return RunClaimResponse(
        status="claimed",
        run_id=run_id,
        tool_name=run.get("tool_name", ""),
        args=json.loads(run.get("args_json", "{}")),
    )


@router.post("/runs/{run_id}/events")
def _push_run_events(
    run_id: str,
    req: RunEventsRequest,
    token: dict = Depends(require_scope_or_agent_node("agent-nodes:write")),
) -> dict[str, Any]:
    now = _now_iso()
    with get_db_connection() as conn:
        for evt in req.events:
            conn.execute(
                """INSERT INTO run_events
                   (id, run_id, event_type, data, created_at)
                   VALUES (?, ?, ?, ?, ?)""",
                (_new_id(), run_id, evt.type, evt.data, now),
            )
            # 更新 run 状态
            if evt.type in ("completed", "failed"):
                conn.execute(
                    "UPDATE tool_runs SET status=?, completed_at=? WHERE id=?",
                    (evt.type, now, run_id),
                )
        conn.commit()
    return {"accepted": len(req.events)}


@router.post("/runs/{run_id}/artifacts")
async def _upload_artifact(
    run_id: str,
    file: UploadFile = File(...),
    token: dict = Depends(require_scope_or_agent_node("agent-nodes:write")),
) -> dict[str, Any]:
    content = await file.read()
    artifact_id = _new_id()
    now = _now_iso()

    with get_db_connection() as conn:
        conn.execute(
            """INSERT INTO run_artifacts
               (id, run_id, filename, size_bytes, content_type, data, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (artifact_id, run_id, file.filename, len(content), file.content_type, content, now),
        )
        conn.commit()

    return {"artifact_id": artifact_id, "size": len(content)}


# =====================================================================
# 7. 节点管理
# =====================================================================

@router.post("/{node_id}/drain")
def _drain_node(
    node_id: str,
    req: DrainRequest,
    token: dict = Depends(require_scope_or_agent_node("agent-nodes:write")),
) -> dict[str, Any]:
    if token.get("actor_type") == "node" and token.get("id") != node_id:
        raise HTTPException(status_code=403, detail="node token does not match node_id")

    now = _now_iso()
    with get_db_connection() as conn:
        conn.execute(
            "UPDATE agent_nodes SET status='draining', drain_reason=?, updated_at=? WHERE id=?",
            (req.reason, now, node_id),
        )
        conn.commit()
    return {"status": "draining", "node_id": node_id}


@router.post("/{node_id}/revoke")
def _revoke_node(
    node_id: str,
    req: RevokeRequest,
    token: dict = Depends(require_scope_or_agent_node("agent-nodes:write")),
) -> dict[str, Any]:
    if token.get("actor_type") == "node" and token.get("id") != node_id:
        raise HTTPException(status_code=403, detail="node token does not match node_id")

    now = _now_iso()
    with get_db_connection() as conn:
        conn.execute(
            "UPDATE agent_nodes SET status='revoked', revoke_reason=?, updated_at=? WHERE id=?",
            (req.reason, now, node_id),
        )
        conn.commit()
    return {"status": "revoked", "node_id": node_id}


# =====================================================================
# 8. Agent-to-agent messaging
# =====================================================================


class AgentMessageSend(BaseModel):
    recipient_id: str = Field(description="recipient agent node ID", min_length=1)
    message_type: str = Field(default="text", description="message type (text, task, notification)")
    payload: dict[str, Any] = Field(default_factory=dict)


class AgentMessageResponse(BaseModel):
    id: str
    sender_id: str
    recipient_id: str
    message_type: str
    payload: dict[str, Any]
    status: str
    created_at: str
    read_at: str | None = None


@router.post("/messages")
def _send_agent_message(
    req: AgentMessageSend,
    token: dict = Depends(require_scope_or_agent_node("agent-nodes:write")),
) -> dict[str, Any]:
    """Send a message from one agent to another via backend relay."""
    if token.get("actor_type") != "node":
        raise HTTPException(status_code=403, detail="only agent nodes can send messages")
    sender_id = token.get("node_id")
    if not sender_id:
        raise HTTPException(status_code=400, detail="sender identity not resolved")

    # Validate recipient exists
    with get_db_connection() as conn:
        recipient = conn.execute(
            "SELECT id FROM agent_nodes WHERE id=?", (req.recipient_id,)
        ).fetchone()
        if not recipient:
            raise HTTPException(status_code=404, detail="recipient node not found")

        msg_id = _new_id()
        now = _now_iso()
        conn.execute(
            """INSERT INTO agent_messages
               (id, sender_id, recipient_id, message_type, payload_json, status, created_at)
               VALUES (?, ?, ?, ?, ?, 'pending', ?)""",
            (
                msg_id,
                sender_id,
                req.recipient_id,
                req.message_type,
                json.dumps(req.payload, ensure_ascii=False),
                now,
            ),
        )
        conn.commit()

    return {"message_id": msg_id, "status": "pending"}


@router.get("/{node_id}/messages", response_model=list[AgentMessageResponse])
def _list_agent_messages(
    node_id: str,
    status: str | None = None,
    limit: int = 50,
    token: dict = Depends(require_scope_or_agent_node("agent-nodes:read")),
) -> list[dict[str, Any]]:
    """List messages for an agent node (both sent and received)."""
    if token.get("actor_type") == "node" and token.get("id") != node_id:
        raise HTTPException(status_code=403, detail="node token does not match node_id")

    with get_db_connection() as conn:
        if status:
            rows = conn.execute(
                """SELECT id, sender_id, recipient_id, message_type, payload_json, status, created_at, read_at
                   FROM agent_messages
                   WHERE (sender_id=? OR recipient_id=?) AND status=?
                   ORDER BY created_at DESC LIMIT ?""",
                (node_id, node_id, status, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                """SELECT id, sender_id, recipient_id, message_type, payload_json, status, created_at, read_at
                   FROM agent_messages
                   WHERE sender_id=? OR recipient_id=?
                   ORDER BY created_at DESC LIMIT ?""",
                (node_id, node_id, limit),
            ).fetchall()

    result = []
    for r in rows:
        item = dict(r)
        try:
            item["payload"] = json.loads(item.pop("payload_json"))
        except (json.JSONDecodeError, KeyError):
            item["payload"] = {}
        result.append(item)
    return result


@router.post("/{node_id}/messages/{message_id}/read")
def _mark_message_read(
    node_id: str,
    message_id: str,
    token: dict = Depends(require_scope_or_agent_node("agent-nodes:write")),
) -> dict[str, Any]:
    """Mark a message as read."""
    if token.get("actor_type") == "node" and token.get("id") != node_id:
        raise HTTPException(status_code=403, detail="node token does not match node_id")

    now = _now_iso()
    with get_db_connection() as conn:
        conn.execute(
            "UPDATE agent_messages SET status='read', read_at=? WHERE id=? AND recipient_id=?",
            (now, message_id, node_id),
        )
        conn.commit()
    return {"status": "read"}


# =====================================================================
# 9. Query/list endpoints
# =====================================================================

@router.get("/")
def _list_nodes(
    token: dict = Depends(require_scope("agent-nodes:read")),
) -> list[dict[str, Any]]:
    with get_db_connection() as conn:
        rows = conn.execute(
            "SELECT id, hostname, status, last_heartbeat_at, cpu_percent, mem_percent, active_runs FROM agent_nodes ORDER BY last_heartbeat_at DESC"
        ).fetchall()
    return [dict(r) for r in rows]


@router.get("/{node_id}")
def _get_node(
    node_id: str,
    token: dict = Depends(require_scope("agent-nodes:read")),
) -> dict[str, Any]:
    with get_db_connection() as conn:
        row = conn.execute("SELECT * FROM agent_nodes WHERE id=?", (node_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="node not found")
    return dict(row)
