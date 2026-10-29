from __future__ import annotations

import hashlib
from copy import deepcopy
from .note_sync import SYNC_STATE_DIRTY, SYNC_STATE_LOCAL_ONLY, SYNC_STATE_SYNCED, get_or_create_local_device_id


def build_translation_history_key(record):
    payload = record or {}
    fingerprint = "|".join(
        [
            str(payload.get("source_text") or ""),
            str(payload.get("translated_text") or ""),
            str(payload.get("from_lang") or ""),
            str(payload.get("to_lang") or ""),
            str(payload.get("created_at") or ""),
        ]
    )
    return hashlib.sha256(fingerprint.encode("utf-8")).hexdigest()


def ensure_translation_sync_metadata(record, data_store, mark_dirty=False):
    changed = False
    if not isinstance(record, dict):
        return changed

    if not record.get("device_id"):
        record["device_id"] = get_or_create_local_device_id(data_store)
        changed = True

    if "remote_id" not in record:
        record["remote_id"] = None
        changed = True

    version = record.get("version")
    if not isinstance(version, int) or version < 1:
        record["version"] = 1
        changed = True

    if "last_synced_at" not in record:
        record["last_synced_at"] = None
        changed = True

    if "deleted_at" not in record:
        record["deleted_at"] = None
        changed = True

    history_key = build_translation_history_key(record)
    if record.get("history_key") != history_key:
        record["history_key"] = history_key
        changed = True

    if not record.get("sync_state"):
        record["sync_state"] = SYNC_STATE_LOCAL_ONLY if not record.get("remote_id") else SYNC_STATE_SYNCED
        changed = True

    if mark_dirty:
        expected_state = SYNC_STATE_LOCAL_ONLY if not record.get("remote_id") else SYNC_STATE_DIRTY
        if record.get("sync_state") != expected_state:
            record["sync_state"] = expected_state
            changed = True

    return changed


def build_translation_sync_payload(record):
    payload = deepcopy(record or {})
    return {
        "id": payload.get("id"),
        "remote_id": payload.get("remote_id"),
        "history_key": payload.get("history_key") or build_translation_history_key(payload),
        "source_text": payload.get("source_text"),
        "translated_text": payload.get("translated_text"),
        "from_lang": payload.get("from_lang"),
        "to_lang": payload.get("to_lang"),
        "raw_result": payload.get("raw_result"),
        "created_at": payload.get("created_at"),
        "updated_at": payload.get("updated_at"),
        "deleted_at": payload.get("deleted_at"),
        "version": payload.get("version"),
        "device_id": payload.get("device_id"),
        "sync_state": payload.get("sync_state"),
        "last_synced_at": payload.get("last_synced_at"),
    }
