"""Cross-device sync API routes.

Implements the 4 sync channels defined in cross-device-sync-spec.md §3:
    - GET /sync/pull  — SyncPull (desktop → phone)
    - POST /sync/push — SyncPush (phone → desktop, envelopes)
    - POST /sync/quick-relay — QuickRelay (phone → desktop, external content)
    - POST /sync/ack-write  — AckWrite (phone → desktop, confirmations)
    - POST /sync/heartbeat  — DeviceHeartbeat (bidirectional)

P4.2-P0a/P0b: storage layer migrated from in-memory dicts to SQLite via
``automation_hub.api.db.sync_store``. Cursor allocation is protected by a
process-wide ``threading.RLock`` to keep envelope ordering monotonic under
concurrent push requests.
"""
from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from ..auth.deps import get_current_token, require_scope
from ..db import sync_store
from ..db.session import get_db_session
from ..models.sync import (
    AckWriteRequest,
    DeviceHeartbeatRequest,
    QuickRelayRequest,
    SyncPullResponse,
    SyncPushRequest,
    SyncPushResponse,
)

logger = logging.getLogger("automation_hub.api.sync")

router = APIRouter(prefix="/sync", tags=["sync"])


# ── SyncPull: Desktop → Phone ───────────────────────────────────────


@router.get("/pull", response_model=SyncPullResponse)
async def sync_pull(
    device: str = "",
    since: int = 0,
    resource_type: str = "",
    limit: int = 100,
    token: dict[str, Any] = Depends(require_scope("sync:pull")),
):
    """Pull envelopes since cursor position.

    Phone calls this to fetch incremental changes from the desktop.
    """
    with get_db_session() as session:
        envelopes, has_more = sync_store.query_envelopes(
            session,
            since=since,
            resource_type=resource_type,
            limit=limit,
        )
        latest_cursor = max((env.get("cursor", 0) for env in envelopes), default=since)
        device_conflicts = sync_store.list_conflicts_for_device(session, device)

    return SyncPullResponse(
        envelopes=envelopes,
        latest_cursor=latest_cursor,
        has_more=has_more,
        conflict_summaries=device_conflicts,
    )


# ── SyncPush: Phone → Desktop ───────────────────────────────────────


@router.post("/push", response_model=SyncPushResponse)
async def sync_push(
    body: SyncPushRequest,
    token: dict[str, Any] = Depends(require_scope("sync:push")),
):
    """Push envelopes from phone to desktop.

    Phone sends batched envelopes. Desktop assigns cursor positions
    (monotonic, protected by an in-process lock) and persists to SQLite.
    """
    accepted = 0
    rejected = 0
    rejected_details: list[dict[str, Any]] = []
    final_cursor = 0

    payloads: list[tuple[Any, dict[str, Any]]] = []
    for env in body.envelopes:
        try:
            env_dict = env.model_dump() if hasattr(env, "model_dump") else dict(env)
        except Exception as exc:  # pragma: no cover - defensive
            rejected += 1
            rejected_details.append({
                "envelope_id": getattr(env, "envelope_id", "unknown"),
                "reason": str(exc),
            })
            continue
        payloads.append((env, env_dict))

    if payloads:
        with get_db_session() as session:
            start_cursor = sync_store.allocate_next_cursor(session, count=len(payloads))
            for offset, (env, env_dict) in enumerate(payloads):
                try:
                    cursor_value = start_cursor + offset
                    sync_store.insert_envelope(
                        session,
                        envelope_id=env_dict.get("envelope_id", ""),
                        device_id=env_dict.get("device_id", ""),
                        resource_type=env_dict.get("resource_type", ""),
                        resource_id=env_dict.get("resource_id", ""),
                        action=env_dict.get("action", ""),
                        payload=env_dict.get("payload"),
                        version=int(env_dict.get("version", 0) or 0),
                        cursor=cursor_value,
                        created_at=str(env_dict.get("created_at", "") or ""),
                        checksum=str(env_dict.get("checksum", "") or ""),
                    )
                    accepted += 1
                    final_cursor = cursor_value
                except Exception as exc:
                    rejected += 1
                    rejected_details.append({
                        "envelope_id": getattr(env, "envelope_id", "unknown"),
                        "reason": str(exc),
                    })

    # TODO: WebSocket notification to paired desktop device (phase C)
    logger.info(
        "sync push: accepted=%d rejected=%d latest_cursor=%d",
        accepted, rejected, final_cursor,
    )

    return SyncPushResponse(
        accepted=accepted,
        rejected=rejected,
        rejected_details=rejected_details,
    )


# ── QuickRelay: Phone → Desktop ─────────────────────────────────────


@router.post("/quick-relay")
async def quick_relay(
    body: QuickRelayRequest,
    token: dict[str, Any] = Depends(require_scope("sync:push")),
):
    """Relay external content from phone to desktop inbox.

    Used for: clipboard sharing, share-intent, photo relay, etc.
    Max payload: 64KB.
    """
    payload_bytes = len(json.dumps(body.payload, ensure_ascii=False).encode("utf-8"))
    if payload_bytes > 64 * 1024:
        raise HTTPException(
            status_code=413,
            detail=f"QuickRelay payload exceeds 64KB limit (got {payload_bytes} bytes). Please split and retry.",
        )

    now_iso = datetime.now(timezone.utc).isoformat()
    inbox_payload = {
        "device_id": body.device_id,
        "relay_type": body.relay_type,
        "payload": body.payload,
        "captured_at": body.captured_at,
        "received_at": now_iso,
        "user_id": token.get("user_id", ""),
    }
    envelope_id = hashlib.sha256(json.dumps(inbox_payload, sort_keys=True).encode()).hexdigest()[:32]
    checksum = hashlib.sha256(json.dumps(inbox_payload, sort_keys=True).encode()).hexdigest()

    with get_db_session() as session:
        cursor_value = sync_store.allocate_next_cursor(session)
        sync_store.insert_envelope(
            session,
            envelope_id=envelope_id,
            device_id=body.device_id,
            resource_type="inbox_item",
            resource_id=f"relay_{cursor_value}",
            action="create",
            payload=inbox_payload,
            version=1,
            cursor=cursor_value,
            created_at=now_iso,
            checksum=checksum,
        )

    logger.info("quick relay: type=%s device=%s cursor=%d", body.relay_type, body.device_id, cursor_value)
    return {
        "status": "accepted",
        "inbox_item_id": f"relay_{cursor_value}",
    }


# ── AckWrite: Phone → Desktop ────────────────────────────────────────


@router.post("/ack-write")
async def ack_write(
    body: AckWriteRequest,
    token: dict[str, Any] = Depends(require_scope("sync:push")),
):
    """Write back acknowledgements from phone.

    Only allows: read, dismissed, confirmed ack types.
    Phone never directly creates/modifies entities.
    """
    allowed = {"read", "dismissed", "confirmed"}
    if body.ack_type not in allowed:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid ack_type: {body.ack_type!r}. Must be one of {sorted(allowed)}",
        )

    now_iso = datetime.now(timezone.utc).isoformat()
    user_id = token.get("user_id", "")

    with get_db_session() as session:
        cursor_value = sync_store.allocate_next_cursor(session)
        sync_store.insert_envelope(
            session,
            envelope_id=f"ack_{cursor_value}",
            device_id=user_id,
            resource_type=body.entity_type,
            resource_id=body.entity_id,
            action="update",
            payload={"ack_type": body.ack_type, "acked_at": body.acked_at},
            version=1,
            cursor=cursor_value,
            created_at=now_iso,
            checksum="",
        )

    logger.info("ack write: type=%s entity=%s ack=%s", body.entity_type, body.entity_id, body.ack_type)
    return {"status": "accepted"}


# ── DeviceHeartbeat ──────────────────────────────────────────────────


@router.post("/heartbeat")
async def device_heartbeat(
    body: DeviceHeartbeatRequest,
    token: dict[str, Any] = Depends(get_current_token),
):
    """Device heartbeat — bidirectional health check.

    Uses get_current_token (authentication only) for compatibility
    with unpaired devices during initial handshake.
    """
    server_time = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

    with get_db_session() as session:
        sync_store.upsert_device(
            session,
            device_id=body.device_id,
            status=body.status,
            last_seen_at=body.last_seen_at,
            extra=body.extra,
        )

    logger.debug("heartbeat: device=%s status=%s", body.device_id, body.status)
    return {
        "status": "acknowledged",
        "server_time": server_time,
    }


# ── Device Management ────────────────────────────────────────────────


@router.get("/devices")
async def list_devices(token: dict[str, Any] = Depends(require_scope("device:manage"))):
    """List paired devices and their status.

    Requires device:manage scope.
    """
    with get_db_session() as session:
        devices = sync_store.list_devices(session)
    return {"devices": devices}
