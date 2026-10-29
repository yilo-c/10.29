from __future__ import annotations
import secrets, uuid, json
from datetime import datetime, timedelta, timezone
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from automation_hub.api.db import get_db_connection
from automation_hub.api.auth.deps import hash_token, get_current_token, require_scope
from automation_hub.api.auth.roles import assign_role, get_user_role, get_user_role_record, list_users_by_role, remove_role, ROLE_SCOPES, ROLES
from automation_hub.api.audit.service import log_event
from automation_hub.api.config import settings

router = APIRouter(prefix="/auth", tags=["auth"])


BOOTSTRAP_SCOPES = [
    "news:read",
    "news:write",
    "note:read",
    "note:write",
    "task:read",
    "task:write",
    "translation:read",
    "translation:write",
    "vocabulary:read",
    "vocabulary:write",
    "tool:read",
    "tool:write",
    "tool:execute",
    "approval:read",
    "approval:decide",
    "audit:read",
    "proposal:read",
    "proposal:write",
    "proposals:admin",
    "repo:read",
    "repo:write",
    "agent:use",
    "user:admin",
    "agent-nodes:read",
    "agent-nodes:write",
    "device:manage",
    "sync:push",
    "sync:pull",
]

TOKEN_TEMPLATES = {
    "admin_ops": BOOTSTRAP_SCOPES,
    "agent": ["agent:use", "tool:execute", "tool:read"],
    # desktop 和 agent 使用相同 scope 集，确保桌面端与远端 worker 认证口径一致
    "desktop": ["agent:use", "tool:execute", "tool:read"],
    "cronjob": ["tool:execute", "tool:read"],
    "readonly": ["news:read", "note:read", "task:read", "translation:read", "vocabulary:read", "tool:read", "approval:read", "audit:read", "repo:read", "proposal:read"],
}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _compute_expires_at(expires_at: str | None, ttl_days: int | None) -> str | None:
    if expires_at:
        return expires_at
    if ttl_days is not None:
        return (datetime.now(timezone.utc) + timedelta(days=int(ttl_days))).isoformat()
    if int(settings.TOKEN_DEFAULT_TTL_DAYS or 0) > 0:
        return (datetime.now(timezone.utc) + timedelta(days=int(settings.TOKEN_DEFAULT_TTL_DAYS))).isoformat()
    return None


def _resolve_scopes(template: str | None, scopes: list[str] | None) -> list[str]:
    if scopes is not None:
        return scopes
    if template:
        resolved = TOKEN_TEMPLATES.get(template)
        if resolved is None:
            raise HTTPException(status_code=400, detail=f"unknown token template: {template}")
        return resolved
    return []


class DeviceCreate(BaseModel):
    name: str
    platform: str


class BootstrapCreate(BaseModel):
    admin_name: str = "Admin"
    device_name: str = "Bootstrap Device"
    device_platform: str = "local"


class TokenCreate(BaseModel):
    device_id: str | None = None
    template: str | None = None
    scopes: list[str] | None = None
    expires_at: str | None = None
    ttl_days: int | None = Field(default=None, ge=1)


class TokenRotate(BaseModel):
    template: str | None = None
    scopes: list[str] | None = None
    expires_at: str | None = None
    ttl_days: int | None = Field(default=None, ge=1)


@router.post("/bootstrap")
def bootstrap(payload: BootstrapCreate):
    with get_db_connection() as conn:
        existing = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        if existing:
            raise HTTPException(status_code=409, detail="system already initialized")

        user_id = str(uuid.uuid4())
        device_id = str(uuid.uuid4())
        token_id = str(uuid.uuid4())
        raw_token = secrets.token_urlsafe(32)
        token_hash = hash_token(raw_token)

        conn.execute(
            "INSERT INTO users(id,name,created_at) VALUES(?,?,?)",
            (user_id, payload.admin_name, now_iso()),
        )
        conn.execute(
            "INSERT INTO devices(id,user_id,name,platform,created_at) VALUES(?,?,?,?,?)",
            (device_id, user_id, payload.device_name, payload.device_platform, now_iso()),
        )
        conn.execute(
            """INSERT INTO api_tokens(id,user_id,device_id,token_hash,scopes,expires_at,created_at)
               VALUES(?,?,?,?,?,?,?)""",
            (token_id, user_id, device_id, token_hash, json.dumps(BOOTSTRAP_SCOPES, ensure_ascii=False), None, now_iso()),
        )
        conn.commit()

    log_event(
        event_type="auth.bootstrap",
        action="bootstrap",
        status="success",
        actor_user_id=user_id,
        actor_device_id=device_id,
        resource_type="user",
        resource_id=user_id,
        message="system bootstrapped",
    )

    return {
        "user_id": user_id,
        "device_id": device_id,
        "token_id": token_id,
        "token": raw_token,
        "scopes": BOOTSTRAP_SCOPES,
    }


@router.post("/devices")
def create_device(payload: DeviceCreate, token=Depends(get_current_token)):
    device_id = str(uuid.uuid4())
    with get_db_connection() as conn:
        conn.execute("INSERT INTO devices(id,user_id,name,platform,created_at) VALUES(?,?,?,?,?)",
                     (device_id, token["user_id"], payload.name, payload.platform, now_iso()))
        conn.commit()
    return {"id": device_id}


@router.get("/token-templates")
def list_token_templates(token=Depends(require_scope("user:admin"))):
    return {
        "templates": [
            {"name": name, "scopes": scopes}
            for name, scopes in TOKEN_TEMPLATES.items()
        ]
    }


@router.post("/tokens")
def create_token(payload: TokenCreate, token=Depends(require_scope("user:admin"))):
    scopes = _resolve_scopes(payload.template, payload.scopes)
    resolved_expires_at = _compute_expires_at(payload.expires_at, payload.ttl_days)
    raw = secrets.token_urlsafe(32)
    token_id = str(uuid.uuid4())
    th = hash_token(raw)
    with get_db_connection() as conn:
        conn.execute(
            """INSERT INTO api_tokens(id,user_id,device_id,token_hash,scopes,expires_at,created_at)
                         VALUES(?,?,?,?,?,?,?)""",
            (token_id, token["user_id"], payload.device_id, th,
             json.dumps(scopes, ensure_ascii=False), resolved_expires_at, now_iso()))
        conn.commit()

    log_event(
        event_type="auth.token_created",
        action="create",
        status="success",
        actor_user_id=token.get("user_id"),
        actor_device_id=token.get("device_id"),
        resource_type="token",
        resource_id=token_id,
        message="token created",
        meta={"issued_for_device_id": payload.device_id, "scopes": scopes, "expires_at": resolved_expires_at, "template": payload.template},
    )
    return {"token_id": token_id, "token": raw}


@router.get("/me")
def me(token=Depends(get_current_token)):
    return {"user_id": token["user_id"], "device_id": token.get("device_id"), "scopes": token.get("scopes_list")}


@router.get("/tokens")
def list_tokens(token=Depends(get_current_token)):
    with get_db_connection() as conn:
        rows = conn.execute(
            "SELECT id,device_id,scopes,expires_at,created_at,revoked_at FROM api_tokens WHERE user_id=?",
            (token["user_id"],)).fetchall()
        tokens = []
        for row in rows:
            item = dict(row)
            try:
                item["scopes"] = json.loads(item.get("scopes") or "[]")
            except Exception:
                item["scopes"] = []
            tokens.append(item)
        return {"tokens": tokens}


@router.post("/tokens/{token_id}/rotate")
def rotate_token(token_id: str, payload: TokenRotate, token=Depends(require_scope("user:admin"))):
    with get_db_connection() as conn:
        row = conn.execute(
            "SELECT * FROM api_tokens WHERE id=? AND user_id=? AND revoked_at IS NULL",
            (token_id, token["user_id"]),
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="not found")

        existing = dict(row)
        try:
            existing_scopes = json.loads(existing.get("scopes") or "[]")
        except Exception:
            existing_scopes = []

        scopes = payload.scopes if payload.scopes is not None else _resolve_scopes(payload.template, None) if payload.template else existing_scopes
        resolved_expires_at = _compute_expires_at(payload.expires_at or existing.get("expires_at"), payload.ttl_days)

        new_token_id = str(uuid.uuid4())
        raw_token = secrets.token_urlsafe(32)
        token_hash = hash_token(raw_token)

        conn.execute("UPDATE api_tokens SET revoked_at=? WHERE id=?", (now_iso(), token_id))
        conn.execute(
            """INSERT INTO api_tokens(id,user_id,device_id,token_hash,scopes,expires_at,created_at)
               VALUES(?,?,?,?,?,?,?)""",
            (
                new_token_id,
                existing["user_id"],
                existing.get("device_id"),
                token_hash,
                json.dumps(scopes, ensure_ascii=False),
                resolved_expires_at,
                now_iso(),
            ),
        )
        conn.commit()

    log_event(
        event_type="auth.token_rotated",
        action="rotate",
        status="success",
        actor_user_id=token.get("user_id"),
        actor_device_id=token.get("device_id"),
        resource_type="token",
        resource_id=new_token_id,
        message="token rotated",
        meta={"old_token_id": token_id, "new_token_id": new_token_id, "expires_at": resolved_expires_at, "scopes": scopes, "template": payload.template},
    )

    return {"old_token_id": token_id, "token_id": new_token_id, "token": raw_token}


# ==================== 角色管理 ====================


class RoleAssign(BaseModel):
    role: str = Field(description="角色名称", min_length=1)


class RoleAssignResponse(BaseModel):
    id: str
    user_id: str
    role: str
    assigned_by: str | None = None
    created_at: str


@router.get("/roles")
def list_roles(token=Depends(require_scope("user:admin"))):
    """列出所有可用角色及其权限范围。"""
    return {
        "roles": [
            {"name": name, "scopes": scopes}
            for name, scopes in ROLE_SCOPES.items()
        ]
    }


@router.get("/users/{user_id}/roles", response_model=RoleAssignResponse | None)
def get_user_roles(user_id: str, token=Depends(require_scope("user:admin"))):
    """获取用户的角色分配。"""
    record = get_user_role_record(user_id)
    if not record:
        return None
    return RoleAssignResponse(
        id=record["id"],
        user_id=record["user_id"],
        role=record["role"],
        assigned_by=record.get("assigned_by"),
        created_at=record["created_at"],
    )


@router.post("/users/{user_id}/roles", response_model=RoleAssignResponse)
def assign_user_role(user_id: str, payload: RoleAssign, token=Depends(require_scope("user:admin"))):
    """为用户分配角色。会替换已有角色。"""
    if payload.role not in ROLES:
        raise HTTPException(status_code=400, detail=f"invalid role: {payload.role}. must be one of {ROLES}")

    result = assign_role(
        user_id=user_id,
        role=payload.role,
        assigned_by=token.get("user_id"),
    )

    log_event(
        event_type="auth.role_assigned",
        action="assign_role",
        status="success",
        actor_user_id=token.get("user_id"),
        actor_device_id=token.get("device_id"),
        resource_type="user",
        resource_id=user_id,
        message=f"role {payload.role} assigned",
        meta={"role": payload.role, "assigned_by": token.get("user_id")},
    )

    return RoleAssignResponse(
        id=result["id"],
        user_id=result["user_id"],
        role=result["role"],
        assigned_by=result.get("assigned_by"),
        created_at=result["created_at"],
    )


@router.delete("/users/{user_id}/roles")
def remove_user_role(user_id: str, token=Depends(require_scope("user:admin"))):
    """移除用户的角色分配。"""
    removed = remove_role(user_id)
    if not removed:
        raise HTTPException(status_code=404, detail="no role assignment found")
    return {"removed": True}


@router.get("/role-assignments")
def list_role_assignments(
    role: str | None = None,
    token=Depends(require_scope("user:admin")),
):
    """列出所有角色分配，可按角色筛选。"""
    if role and role not in ROLES:
        raise HTTPException(status_code=400, detail=f"invalid role: {role}")
    assignments = list_users_by_role(role=role)
    return {"assignments": assignments, "count": len(assignments)}


@router.delete("/tokens/{token_id}")
def revoke_token(token_id: str, token=Depends(require_scope("user:admin"))):
    with get_db_connection() as conn:
        row = conn.execute("SELECT * FROM api_tokens WHERE id=? AND user_id=?", (token_id, token["user_id"]))
        if not row.fetchone():
            raise HTTPException(status_code=404, detail="not found")
        conn.execute("UPDATE api_tokens SET revoked_at=? WHERE id=?", (now_iso(), token_id))
        conn.commit()

    log_event(
        event_type="auth.token_revoked",
        action="revoke",
        status="success",
        actor_user_id=token.get("user_id"),
        actor_device_id=token.get("device_id"),
        resource_type="token",
        resource_id=token_id,
        message="token revoked",
    )
    return {"revoked": True}
