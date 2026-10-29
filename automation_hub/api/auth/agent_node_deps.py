from __future__ import annotations

import json
from datetime import datetime, timezone

from fastapi import Header, HTTPException

from automation_hub.api.auth.deps import hash_token
from automation_hub.api.db import get_db_connection


def _parse_scopes(raw_scopes: str | None) -> list[str]:
    if not raw_scopes:
        return []
    try:
        scopes = json.loads(raw_scopes)
    except Exception:
        return []
    return scopes if isinstance(scopes, list) else []


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def require_scope_or_agent_node(scope: str):
    """允许两类凭证：

    1) 用户 API token，且包含 scope
    2) 已注册 agent node token（无 scope 概念）
    """

    def _dependency(authorization: str | None = Header(default=None, alias="Authorization")) -> dict:
        if not authorization or not authorization.lower().startswith("bearer "):
            raise HTTPException(status_code=401, detail="missing token")

        raw_token = authorization.split(" ", 1)[1].strip()
        token_hash = hash_token(raw_token)

        # 优先尝试用户 token
        with get_db_connection() as conn:
            user_row = conn.execute(
                "SELECT * FROM api_tokens WHERE token_hash=? AND revoked_at IS NULL",
                (token_hash,),
            ).fetchone()

            if user_row:
                user = dict(user_row)
                expires_at = user.get("expires_at")
                if expires_at:
                    try:
                        parsed = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
                        if parsed.tzinfo is None:
                            parsed = parsed.replace(tzinfo=timezone.utc)
                        if parsed.astimezone(timezone.utc) <= _now_utc():
                            raise HTTPException(status_code=401, detail="token expired")
                    except ValueError:
                        pass

                scopes = _parse_scopes(user.get("scopes"))
                if scope not in scopes:
                    raise HTTPException(status_code=403, detail=f"missing scope: {scope}")

                user["actor_type"] = "user"
                user["scopes_list"] = scopes
                return user

            # 再尝试节点 token（哈希对比）
            node_row = conn.execute(
                "SELECT * FROM agent_nodes WHERE issued_token_hash=?",
                (token_hash,),
            ).fetchone()

            if not node_row:
                raise HTTPException(status_code=401, detail="invalid token")

            node = dict(node_row)
            if node.get("status") == "revoked":
                raise HTTPException(status_code=403, detail="node revoked")

            node["actor_type"] = "node"
            node["node_id"] = node.get("id")
            node_scopes = [
                "tool:execute",
                "tool:read",
                "agent-nodes:write",
                "agent-nodes:read",
            ]
            node["scopes_list"] = node_scopes
            node["scopes"] = json.dumps(node_scopes)
            return node

    return _dependency
