"""Memory entry sync helpers — manual conflict review for sensitive memories."""
from __future__ import annotations

import hashlib
import json
from copy import deepcopy

from .note_sync import (
    SYNC_STATE_DIRTY,
    SYNC_STATE_LOCAL_ONLY,
    SYNC_STATE_SYNCED,
    get_or_create_local_device_id,
)


def build_memory_fingerprint(entry: dict | None) -> str:
    payload = entry or {}
    # 记忆指纹包含全部用户可见字段，确保跨设备能精确判定同一条记忆
    raw = json.dumps(
        {
            "text": str(payload.get("text") or payload.get("content") or ""),
            "memory_type": str(payload.get("memory_type") or ""),
            "anchor": str(payload.get("anchor") or ""),
            "created_at": str(payload.get("created_at") or ""),
            "tags": sorted(list(payload.get("tags") or [])),
        },
        sort_keys=True,
        ensure_ascii=False,
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def ensure_memory_sync_metadata(entry, data_store, mark_dirty: bool = False) -> bool:
    """记忆条目同步元数据补齐。冲突策略为 MANUAL（用户裁决）。"""
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

    fingerprint = build_memory_fingerprint(entry)
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


def build_memory_sync_payload(entry) -> dict:
    payload = deepcopy(entry or {})
    return {
        "id": payload.get("id"),
        "remote_id": payload.get("remote_id"),
        "fingerprint": payload.get("fingerprint") or build_memory_fingerprint(payload),
        "text": payload.get("text") or payload.get("content"),
        "memory_type": payload.get("memory_type"),
        "anchor": payload.get("anchor"),
        "tags": list(payload.get("tags") or []),
        "importance": payload.get("importance"),
        "emotion": payload.get("emotion"),
        "metadata": dict(payload.get("metadata") or {}),
        "created_at": payload.get("created_at"),
        "updated_at": payload.get("updated_at"),
        "deleted_at": payload.get("deleted_at"),
        "version": payload.get("version"),
        "device_id": payload.get("device_id"),
        "sync_state": payload.get("sync_state"),
        "last_synced_at": payload.get("last_synced_at"),
    }
