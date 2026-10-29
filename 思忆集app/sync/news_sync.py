"""News sync helpers — server-authoritative news items."""
from __future__ import annotations

import hashlib
from copy import deepcopy

from .note_sync import (
    SYNC_STATE_DIRTY,
    SYNC_STATE_LOCAL_ONLY,
    SYNC_STATE_SYNCED,
    get_or_create_local_device_id,
)


def build_news_fingerprint(item: dict | None) -> str:
    """资讯指纹：URL + 标题 + 发布时间，用于跨设备幂等。"""
    payload = item or {}
    raw = "|".join(
        [
            str(payload.get("url") or ""),
            str(payload.get("title") or ""),
            str(payload.get("published_at") or ""),
        ]
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def ensure_news_sync_metadata(item, data_store, mark_dirty: bool = False) -> bool:
    """补齐 news 条目的同步元数据。"""
    changed = False
    if not isinstance(item, dict):
        return changed

    if not item.get("device_id"):
        item["device_id"] = get_or_create_local_device_id(data_store)
        changed = True

    if "remote_id" not in item:
        item["remote_id"] = None
        changed = True

    version = item.get("version")
    if not isinstance(version, int) or version < 1:
        item["version"] = 1
        changed = True

    if "last_synced_at" not in item:
        item["last_synced_at"] = None
        changed = True

    if "deleted_at" not in item:
        item["deleted_at"] = None
        changed = True

    fingerprint = build_news_fingerprint(item)
    if item.get("fingerprint") != fingerprint:
        item["fingerprint"] = fingerprint
        changed = True

    if not item.get("sync_state"):
        item["sync_state"] = SYNC_STATE_LOCAL_ONLY if not item.get("remote_id") else SYNC_STATE_SYNCED
        changed = True

    if mark_dirty:
        expected = SYNC_STATE_LOCAL_ONLY if not item.get("remote_id") else SYNC_STATE_DIRTY
        if item.get("sync_state") != expected:
            item["sync_state"] = expected
            changed = True

    return changed


def build_news_sync_payload(item) -> dict:
    """转换为同步载荷。"""
    payload = deepcopy(item or {})
    return {
        "id": payload.get("id"),
        "remote_id": payload.get("remote_id"),
        "fingerprint": payload.get("fingerprint") or build_news_fingerprint(payload),
        "title": payload.get("title"),
        "url": payload.get("url"),
        "summary": payload.get("summary"),
        "source": payload.get("source"),
        "tags": list(payload.get("tags") or []),
        "category": payload.get("category"),
        "published_at": payload.get("published_at"),
        "fetched_at": payload.get("fetched_at"),
        "starred": bool(payload.get("starred", False)),
        "read": bool(payload.get("read", False)),
        "created_at": payload.get("created_at"),
        "updated_at": payload.get("updated_at"),
        "deleted_at": payload.get("deleted_at"),
        "version": payload.get("version"),
        "device_id": payload.get("device_id"),
        "sync_state": payload.get("sync_state"),
        "last_synced_at": payload.get("last_synced_at"),
    }
