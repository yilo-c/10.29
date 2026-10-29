# -*- coding: utf-8 -*-
"""Scope enforcement middleware.

Checks token scopes on every request and enforces:

- CronJob tokens (tool:execute, tool:read only) limited to /tools/ endpoints
- ReadOnly tokens (no write scopes) blocked from write methods
- Token usage audit logging
"""

from __future__ import annotations

import json
import logging
from typing import Any

from fastapi import Request
from fastapi.responses import JSONResponse

from automation_hub.api.audit.service import log_event

logger = logging.getLogger("automation_hub.scope")

# Scopes that indicate write capability
WRITE_SCOPES: set[str] = {
    "news:write",
    "note:write",
    "task:write",
    "translation:write",
    "vocabulary:write",
    "tool:write",
    "tool:execute",
    "approval:decide",
    "user:admin",
    "proposal:write",
    "repo:write",
    "agent:use",
}

# The exact scope set for cronjob tokens
CRONJOB_SCOPE_SET: set[str] = {"tool:execute", "tool:read"}

# Paths that bypass scope checks entirely
PUBLIC_PATHS: set[str] = {"/auth/bootstrap", "/health", "/docs", "/openapi.json", "/redoc"}


def _extract_bearer_token(authorization: str) -> str | None:
    if not authorization:
        return None
    parts = authorization.strip().split(maxsplit=1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        return None
    return parts[1].strip() or None


def _is_readonly_token(scopes: set[str]) -> bool:
    """Check if token has no write scopes (readonly)."""
    return not bool(scopes & WRITE_SCOPES)


def _is_cronjob_token(scopes: set[str]) -> bool:
    """Check if token has exactly the cronjob scope set."""
    return scopes == CRONJOB_SCOPE_SET


WRITE_METHODS = frozenset({"POST", "PUT", "DELETE", "PATCH"})


def _is_write_method(method: str) -> bool:
    return method.upper() in WRITE_METHODS


def _is_tool_path(path: str) -> bool:
    return path.startswith("/tools/")


async def scope_enforcement_middleware(request: Request, call_next):
    """Enforce token scopes and log token usage."""

    path = request.url.path

    # Skip public paths
    if path in PUBLIC_PATHS:
        return await call_next(request)

    # Extract token from Authorization header
    auth_header = request.headers.get("Authorization", "")
    raw_token = _extract_bearer_token(auth_header)
    if not raw_token:
        return await call_next(request)

    # Look up token in database
    from api.auth.deps import hash_token
    from api.db import get_db_connection

    token_hash = hash_token(raw_token)
    token_record: dict[str, Any] | None = None

    with get_db_connection() as conn:
        row = conn.execute(
            "SELECT id, scopes, user_id, device_id FROM api_tokens "
            "WHERE token_hash=? AND revoked_at IS NULL "
            "AND (expires_at IS NULL OR expires_at > datetime('now'))",
            (token_hash,),
        ).fetchone()

    if not row:
        return await call_next(request)

    token_record = dict(row)

    try:
        scopes: set[str] = set(json.loads(token_record.get("scopes") or "[]"))
    except Exception:
        scopes = set()

    token_id = token_record["id"]
    user_id = token_record.get("user_id")
    device_id = token_record.get("device_id")

    # === Enforcement Rule 1: CronJob tokens only on /tools/ paths ===
    if _is_cronjob_token(scopes) and not _is_tool_path(path):
        logger.warning(
            "CronJob token blocked from non-tool path",
            extra={"token_id": token_id, "path": path, "method": request.method},
        )
        return JSONResponse(
            status_code=403,
            content={"detail": "CronJob tokens limited to tool:execute scope"},
        )

    # === Enforcement Rule 2: ReadOnly tokens blocked from write methods ===
    if _is_readonly_token(scopes) and _is_write_method(request.method):
        logger.warning(
            "ReadOnly token blocked from write endpoint",
            extra={"token_id": token_id, "path": path, "method": request.method},
        )
        return JSONResponse(
            status_code=403,
            content={"detail": "ReadOnly tokens blocked from write endpoints"},
        )

    # === Token usage audit ===
    log_event(
        event_type="auth.token_used",
        action="request",
        status="success",
        actor_user_id=user_id,
        actor_device_id=device_id,
        resource_type="token",
        resource_id=token_id,
        message=f"Token used: {request.method} {path}",
        meta={"method": request.method, "path": path, "scopes": list(scopes)},
    )

    return await call_next(request)
