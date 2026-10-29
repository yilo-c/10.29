# -*- coding: utf-8 -*-
"""认证依赖"""

from __future__ import annotations

import hashlib
import json

from fastapi import Depends, Header, HTTPException, status


def hash_token(raw_token: str) -> str:
    """对原始 token 做 SHA-256 哈希，用于数据库存储。"""
    return hashlib.sha256(raw_token.encode()).hexdigest()


def get_current_token(authorization: str = Header(default="")):
    """从 Authorization 头解析 Bearer token 并返回 token 元数据。

    用作 FastAPI Depends 依赖项，返回 dict 包含 user_id, device_id, scopes_list。
    """
    raw = _extract_bearer_token(authorization)
    if raw is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing or invalid Authorization header")

    token_hash = hash_token(raw)

    from api.db import get_db_connection
    with get_db_connection() as conn:
        row = conn.execute(
            "SELECT * FROM api_tokens WHERE token_hash=? AND (expires_at IS NULL OR expires_at > datetime('now')) AND revoked_at IS NULL",
            (token_hash,),
        ).fetchone()

    if not row:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired token")

    token = dict(row)
    try:
        token["scopes_list"] = json.loads(token.get("scopes") or "[]")
    except Exception:
        token["scopes_list"] = []

    return token


def require_scope(required_scope: str):
    """需要特定权限范围的依赖"""
    def _require_scope(token=Depends(get_current_token)):
        scopes: list[str] = token.get("scopes_list") or []
        if required_scope not in scopes:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Insufficient scope: {required_scope}",
            )
        return token
    return _require_scope


def _extract_bearer_token(authorization: str) -> str | None:
    if not authorization:
        return None
    parts = authorization.strip().split(maxsplit=1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        return None
    return parts[1].strip() or None
