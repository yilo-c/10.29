"""Inbox sync helpers — phone→PC inbox items with manual conflict review."""
from __future__ import annotations

import hashlib
from copy import deepcopy

from .note_sync import (
    SYNC_STATE_DIRTY,
    SYNC_STATE_LOCAL_ONLY,
    SYNC_STATE_SYNCED,
    get_or_create_local_device_id,
)


def build_inbox_fingerprint(entry: dict | None) -> str:
    payload = entry or {}
    raw = "|".join(
        [
            str(payload.get("content") or ""),
            str(payload.get("captured_at") or ""),
            str(payload.get("source_device_id") or ""),
            str(payload.get("source_type") or ""),
        ]
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def ensure_inbox_sync_metadata(entry, data_store, mark_dirty: bool = False) -> bool:
    """补齐收件箱条目同步元数据。

    收件箱使用 ``MANUAL`` 冲突策略：当本地和远端同时修改了同一条目时，
    必须由用户手动选择。在 metadata 层只补齐字段，不直接覆盖任何决策。
    """
    changed = False
    if not isinstance(entry, dict):
        return changed

    if not entry.get("device_id"):
        entry["device_id"] = get_or_create_local_device_id(data_store)
        changed = True

    if "remote_id" not in entry:
        entry["remote_id"] = None
        changed = True

    version = entry.get("version")
    if not isinstance(version, int) or version < 1:
        entry["version"] = 1
        changed = True

    if "last_synced_at" not in entry:
        entry["last_synced_at"] = None
        changed = True

    if "deleted_at" not in entry:
        entry["deleted_at"] = None
        changed = True

    if "is_processed" not in entry:
        entry["is_processed"] = False
        changed = True

    fingerprint = build_inbox_fingerprint(entry)
    if entry.get("fingerprint") != fingerprint:
        entry["fingerprint"] = fingerprint
        changed = True

    if not entry.get("sync_state"):
        entry["sync_state"] = SYNC_STATE_LOCAL_ONLY if not entry.get("remote_id") else SYNC_STATE_SYNCED
        changed = True

    if mark_dirty:
        expected = SYNC_STATE_LOCAL_ONLY if not entry.get("remote_id") else SYNC_STATE_DIRTY
        if entry.get("sync_state") != expected:
            entry["sync_state"] = expected
            changed = True

    return changed


def build_inbox_sync_payload(entry) -> dict:
    payload = deepcopy(entry or {})
    return {
        "id": payload.get("id"),
        "remote_id": payload.get("remote_id"),
        "fingerprint": payload.get("fingerprint") or build_inbox_fingerprint(payload),
        "content": payload.get("content"),
        "content_type": payload.get("content_type") or "text",
        "source_type": payload.get("source_type"),
        "source_device_id": payload.get("source_device_id"),
        "captured_at": payload.get("captured_at"),
        "is_processed": bool(payload.get("is_processed", False)),
        "processed_at": payload.get("processed_at"),
        "tags": list(payload.get("tags") or []),
        "extra": dict(payload.get("extra") or {}),
        "created_at": payload.get("created_at"),
        "updated_at": payload.get("updated_at"),
        "deleted_at": payload.get("deleted_at"),
        "version": payload.get("version"),
        "device_id": payload.get("device_id"),
        "sync_state": payload.get("sync_state"),
        "last_synced_at": payload.get("last_synced_at"),
    }
