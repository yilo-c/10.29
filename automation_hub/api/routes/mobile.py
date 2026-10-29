"""Mobile companion API routes.

Provides:
    POST /mobile/pairing/initiate   — Desktop generates a pairing token
    POST /mobile/pairing/complete   — Phone exchanges setup token for API token
    GET  /mobile/pairing/status     — Desktop checks paired devices
    POST /mobile/capture            — Phone quick-capture (text/voice/photo)
    GET  /mobile/dashboard          — Aggregated dashboard data for phone
"""
from __future__ import annotations

import json
import logging
import secrets
import time
import uuid
from datetime import datetime, timezone
from typing import Any

import os

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel

from automation_hub.api.auth.deps import get_current_token, hash_token, require_scope
from automation_hub.api.auth.tokens import generate_token
from automation_hub.api.db import get_db_connection
from automation_hub.api.models.mobile import (
    CaptureItem,
    DashboardResponse,
    MobilePairingStatus,
    PairingCompleteRequest,
    PairingCompleteResponse,
    PairingInitiateResponse,
)

logger = logging.getLogger("automation_hub.api.mobile")

router = APIRouter(prefix="/mobile", tags=["mobile"])

# ── In-memory setup token store ──────────────────────────────────
# setup_token -> {user_id, device_id, expires_at, pin_code}
_setup_tokens: dict[str, dict[str, Any]] = {}
# pin_code -> setup_token (reverse lookup)
_pin_index: dict[str, str] = {}

MOBILE_SCOPES = [
    "note:read", "note:write",
    "task:read", "task:write",
    "sync:push", "sync:pull",
    "device:manage",
]

SETUP_TOKEN_TTL = 300  # 5 minutes


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── Pairing: Desktop initiates ───────────────────────────────────


@router.post("/pairing/initiate", response_model=PairingInitiateResponse)
def initiate_pairing(token=Depends(require_scope("device:manage"))):
    """Desktop calls this to generate a one-time setup token for QR code + PIN."""
    setup_token = secrets.token_urlsafe(16)
    pin_code = str(secrets.randbelow(900000) + 100000)  # 6-digit PIN
    expires_at = time.time() + SETUP_TOKEN_TTL

    _setup_tokens[setup_token] = {
        "user_id": token["user_id"],
        "device_id": token.get("device_id", ""),
        "expires_at": expires_at,
        "pin_code": pin_code,
    }
    _pin_index[pin_code] = setup_token

    # Clean expired tokens periodically
    _cleanup_setup_tokens()

    return PairingInitiateResponse(
        setup_token=setup_token,
        pairing_url=f"/mobile/pair?token={setup_token}",
        pin_code=pin_code,
        expires_in_seconds=SETUP_TOKEN_TTL,
    )


# ── Pairing: Phone completes ─────────────────────────────────────


@router.post("/pairing/complete", response_model=PairingCompleteResponse)
def complete_pairing(payload: PairingCompleteRequest):
    """Phone exchanges setup token (or PIN) for a long-lived API token."""
    # Resolve setup token: direct or via PIN
    setup_token = payload.setup_token
    if not setup_token and payload.pin_code:
        setup_token = _pin_index.pop(payload.pin_code, "")

    setup = _setup_tokens.pop(setup_token, None) if setup_token else None
    if setup is None:
        raise HTTPException(status_code=400, detail="Invalid or expired setup token/PIN")

    if time.time() > setup["expires_at"]:
        raise HTTPException(status_code=400, detail="Setup token has expired")

    user_id = setup["user_id"]
    device_id = str(uuid.uuid4())
    raw_token = generate_token()
    token_hash = hash_token(raw_token)
    token_id = str(uuid.uuid4())

    with get_db_connection() as conn:
        # Register phone device
        conn.execute(
            "INSERT INTO devices (id, user_id, name, platform, created_at) VALUES (?, ?, ?, ?, ?)",
            (device_id, user_id, payload.device_name, payload.platform, _now_iso()),
        )
        # Create API token with mobile scopes
        conn.execute(
            """INSERT INTO api_tokens (id, user_id, device_id, token_hash, scopes, expires_at, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (token_id, user_id, device_id, token_hash,
             json.dumps(MOBILE_SCOPES, ensure_ascii=False), None, _now_iso()),
        )
        conn.commit()

    logger.info(
        "Mobile device paired: device_id=%s name=%s user_id=%s",
        device_id, payload.device_name, user_id,
    )

    return PairingCompleteResponse(
        api_token=raw_token,
        device_id=device_id,
        user_id=user_id,
        scopes=list(MOBILE_SCOPES),
    )


# ── Pairing status ───────────────────────────────────────────────


@router.get("/pairing/status", response_model=MobilePairingStatus)
def pairing_status(token=Depends(require_scope("device:manage"))):
    """Desktop checks which mobile devices are paired."""
    with get_db_connection() as conn:
        rows = conn.execute(
            "SELECT id, name, platform, created_at FROM devices WHERE user_id=? AND platform IN ('android', 'ios') ORDER BY created_at DESC",
            (token["user_id"],),
        ).fetchall()

    devices = [dict(r) for r in rows]
    return MobilePairingStatus(
        paired_devices=devices,
        is_paired=len(devices) > 0,
    )


# ── Quick capture ────────────────────────────────────────────────


@router.post("/capture")
def quick_capture(
    item: CaptureItem,
    token=Depends(require_scope("note:write")),
):
    """Phone quick-captures a note (text/voice/photo) → creates inbox item."""
    try:
        from api.db import upsert_personal_note
        note = upsert_personal_note(token["user_id"], {
            "title": f"手机速记 - {item.capture_type}",
            "content": item.content,
            "source": "phone",
            "tags": ["phone-capture", item.capture_type],
        })
        return {"status": "ok", "note_id": note.get("id", "")}
    except Exception as exc:
        logger.exception("Failed to save phone capture")
        raise HTTPException(status_code=500, detail=str(exc))


# ── File upload (voice / photo) ────────────────────────────────────


UPLOAD_DIR = None  # Will be set on first use


def _get_upload_dir() -> str:
    global UPLOAD_DIR
    if UPLOAD_DIR is None:
        from api.db.connection import DATABASE_PATH
        UPLOAD_DIR = os.path.join(os.path.dirname(str(DATABASE_PATH)), "mobile_uploads")
    os.makedirs(UPLOAD_DIR, exist_ok=True)
    return UPLOAD_DIR


@router.post("/capture/upload")
async def upload_capture(
    file: UploadFile = File(...),
    capture_type: str = "voice",
    source: str = "phone",
    token=Depends(require_scope("note:write")),
):
    """Upload a media file (voice recording or photo) from the phone."""
    upload_dir = _get_upload_dir()
    ext = os.path.splitext(file.filename or "upload")[1] or ".bin"
    filename = f"{capture_type}_{int(time.time())}_{uuid.uuid4().hex[:8]}{ext}"
    filepath = os.path.join(upload_dir, filename)

    try:
        content = await file.read()
        with open(filepath, "wb") as f:
            f.write(content)
    except Exception as exc:
        logger.exception("Failed to save uploaded file")
        raise HTTPException(status_code=500, detail=str(exc))

    logger.info(
        "Mobile upload saved: type=%s file=%s size=%d user_id=%s",
        capture_type, filename, len(content), token["user_id"],
    )

    return {
        "status": "ok",
        "filename": filename,
        "size": len(content),
        "url": f"/mobile/uploads/{filename}",
    }


# ── Chat placeholder ──────────────────────────────────────────────


@router.get("/chat")
def mobile_chat_get(token=Depends(require_scope("note:read"))):
    """Placeholder: get chat/message history for mobile."""
    return {"messages": [], "count": 0}


@router.post("/chat")
def mobile_chat_post(
    payload: dict[str, Any],
    token=Depends(require_scope("note:write")),
):
    """Placeholder: send a chat message from mobile."""
    return {"status": "ok", "message_id": None}


# ── Dashboard ────────────────────────────────────────────────────


@router.get("/dashboard", response_model=DashboardResponse)
def dashboard(token=Depends(require_scope("note:read"))):
    """Aggregated dashboard data for the phone home screen."""
    user_id = token["user_id"]

    with get_db_connection() as conn:
        recent_notes = conn.execute(
            "SELECT id, remote_id, title, content, updated_at FROM personal_notes WHERE user_id=? ORDER BY updated_at DESC LIMIT 10",
            (user_id,),
        ).fetchall()

        active_tasks = conn.execute(
            "SELECT id, name as title, is_completed FROM personal_tasks WHERE user_id=? AND is_completed=0 ORDER BY updated_at DESC LIMIT 10",
            (user_id,),
        ).fetchall()

    return DashboardResponse(
        recent_notes=[dict(r) for r in recent_notes],
        active_tasks=[dict(r) for r in active_tasks],
        pending_count=0,
    )


# ── Helpers ──────────────────────────────────────────────────────


def _cleanup_setup_tokens() -> None:
    now = time.time()
    expired = [k for k, v in _setup_tokens.items() if v["expires_at"] < now]
    for k in expired:
        pin = _setup_tokens[k].get("pin_code", "")
        _pin_index.pop(pin, None)
        _setup_tokens.pop(k, None)
