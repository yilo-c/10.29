# -*- coding: utf-8 -*-
"""Role definitions and management for RBAC.

Defines the four roles (admin, agent, cronjob, readonly) and their
corresponding scope mappings. Provides service functions for
assigning, listing, and querying user roles.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from automation_hub.api.db import get_db_connection


# Predefined role → scope mappings
ROLE_SCOPES: dict[str, list[str]] = {
    "admin": [
        "news:read", "news:write",
        "note:read", "note:write",
        "task:read", "task:write",
        "translation:read", "translation:write",
        "vocabulary:read", "vocabulary:write",
        "tool:read", "tool:write", "tool:execute",
        "approval:read", "approval:decide",
        "audit:read",
        "sync:push", "sync:pull",
        "device:manage",
        "proposal:read", "proposal:write",
        "repo:read", "repo:write",
        "agent:use",
        "user:admin",
        "agent-nodes:read", "agent-nodes:write",
    ],
    "agent": [
        "agent:use",
        "tool:execute",
        "tool:read",
    ],
    "cronjob": [
        "tool:execute",
        "tool:read",
    ],
    "readonly": [
        "news:read", "note:read", "task:read",
        "translation:read", "vocabulary:read",
        "tool:read",
        "approval:read",
        "audit:read",
        "sync:pull",
        "repo:read",
        "proposal:read",
    ],
}

ROLES = list(ROLE_SCOPES.keys())


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def assign_role(user_id: str, role: str, assigned_by: str | None = None) -> dict:
    """Assign a role to a user. Replaces any existing role assignment."""
    if role not in ROLES:
        raise ValueError(f"Invalid role: {role}. Must be one of {ROLES}")

    role_id = str(uuid.uuid4())
    now = now_iso()

    with get_db_connection() as conn:
        # Remove existing role for this user
        conn.execute("DELETE FROM user_roles WHERE user_id=?", (user_id,))
        # Insert new role
        conn.execute(
            "INSERT INTO user_roles (id, user_id, role, assigned_by, created_at) VALUES (?, ?, ?, ?, ?)",
            (role_id, user_id, role, assigned_by, now),
        )
        conn.commit()

    return {
        "id": role_id,
        "user_id": user_id,
        "role": role,
        "assigned_by": assigned_by,
        "created_at": now,
    }


def get_user_role(user_id: str) -> str | None:
    """Get the role assigned to a user. Returns None if unassigned."""
    with get_db_connection() as conn:
        row = conn.execute(
            "SELECT role FROM user_roles WHERE user_id=? ORDER BY created_at DESC LIMIT 1",
            (user_id,),
        ).fetchone()
    return row["role"] if row else None


def get_user_role_record(user_id: str) -> dict | None:
    """Get the full role record for a user."""
    with get_db_connection() as conn:
        row = conn.execute(
            "SELECT id, user_id, role, assigned_by, created_at FROM user_roles WHERE user_id=? ORDER BY created_at DESC LIMIT 1",
            (user_id,),
        ).fetchone()
    return dict(row) if row else None


def list_users_by_role(role: str | None = None) -> list[dict]:
    """List user role assignments, optionally filtered by role."""
    with get_db_connection() as conn:
        if role:
            rows = conn.execute(
                "SELECT ur.id, ur.user_id, u.name AS user_name, ur.role, ur.assigned_by, ur.created_at "
                "FROM user_roles ur LEFT JOIN users u ON u.id = ur.user_id "
                "WHERE ur.role=? ORDER BY ur.created_at DESC",
                (role,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT ur.id, ur.user_id, u.name AS user_name, ur.role, ur.assigned_by, ur.created_at "
                "FROM user_roles ur LEFT JOIN users u ON u.id = ur.user_id "
                "ORDER BY ur.created_at DESC",
            ).fetchall()
    return [dict(r) for r in rows]


def remove_role(user_id: str) -> bool:
    """Remove role assignment for a user."""
    with get_db_connection() as conn:
        cur = conn.execute("DELETE FROM user_roles WHERE user_id=?", (user_id,))
        conn.commit()
    return cur.rowcount > 0
