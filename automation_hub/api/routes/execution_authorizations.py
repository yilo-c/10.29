"""执行授权 API 路由。"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from automation_hub.api.auth.agent_node_deps import require_scope_or_agent_node
from automation_hub.api.db import get_db_connection

router = APIRouter(prefix="/execution-authorizations", tags=["execution-authorizations"])


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class ExecutionAuthorizationRequest(BaseModel):
    node_id: str
    tool_name: str
    args: dict[str, Any] = Field(default_factory=dict)
    risk_level: str = "low"


class ExecutionAuthorizationResponse(BaseModel):
    authorization_id: str
    decision: str
    reason: str = ""


@router.post("", response_model=ExecutionAuthorizationResponse)
def request_execution_authorization(
    req: ExecutionAuthorizationRequest,
    token: dict = Depends(require_scope_or_agent_node("agent-nodes:write")),
) -> ExecutionAuthorizationResponse:
    if token.get("actor_type") == "node" and token.get("id") != req.node_id:
        raise HTTPException(status_code=403, detail="node token does not match request node_id")

    auth_id = str(uuid.uuid4())
    now = _now_iso()

    if req.risk_level == "low":
        decision = "approved"
        reason = "auto-approved by risk policy"
    else:
        decision = "pending"
        reason = "manual approval required"

    with get_db_connection() as conn:
        conn.execute(
            """INSERT INTO execution_authorizations
               (id, node_id, tool_name, args_json, risk_level, decision, reason, created_at, decided_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                auth_id,
                req.node_id,
                req.tool_name,
                json.dumps(req.args, ensure_ascii=False),
                req.risk_level,
                decision,
                reason,
                now,
                now if decision == "approved" else None,
            ),
        )
        conn.commit()

    return ExecutionAuthorizationResponse(authorization_id=auth_id, decision=decision, reason=reason)
