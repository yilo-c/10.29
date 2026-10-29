from __future__ import annotations

import hashlib
from copy import deepcopy
from .note_sync import SYNC_STATE_DIRTY, SYNC_STATE_LOCAL_ONLY, SYNC_STATE_SYNCED, get_or_create_local_device_id


def build_wordbook_key(record):
    payload = record or {}
    fingerprint = "|".join(
        [
            str(payload.get("name") or ""),
            str(payload.get("created_at") or ""),
        ]
    )
    return hashlib.sha256(fingerprint.encode("utf-8")).hexdigest()


def build_word_key(record):
    payload = record or {}
    fingerprint = "|".join(
        [
            str(payload.get("word") or ""),
            str(payload.get("phonetic") or ""),
            str(payload.get("meaning") or ""),
            str(payload.get("example") or ""),
            str(payload.get("created_at") or ""),
            str(payload.get("wordbook_key") or ""),
        ]
    )
    return hashlib.sha256(fingerprint.encode("utf-8")).hexdigest()


def ensure_wordbook_sync_metadata(record, data_store, mark_dirty=False):
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

    book_key = build_wordbook_key(record)
    if record.get("book_key") != book_key:
        record["book_key"] = book_key
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


def ensure_word_sync_metadata(record, data_store, mark_dirty=False):
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

    if not record.get("wordbook_key"):
        record["wordbook_key"] = None
        changed = True

    word_key = build_word_key(record)
    if record.get("word_key") != word_key:
        record["word_key"] = word_key
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


def build_wordbook_sync_payload(record):
    payload = deepcopy(record or {})
    return {
        "id": payload.get("id"),
        "remote_id": payload.get("remote_id"),
        "book_key": payload.get("book_key") or build_wordbook_key(payload),
        "name": payload.get("name"),
        "description": payload.get("description"),
        "created_at": payload.get("created_at"),
        "updated_at": payload.get("updated_at"),
        "deleted_at": payload.get("deleted_at"),
        "version": payload.get("version"),
        "device_id": payload.get("device_id"),
        "sync_state": payload.get("sync_state"),
        "last_synced_at": payload.get("last_synced_at"),
    }


def build_word_sync_payload(record):
    payload = deepcopy(record or {})
    return {
        "id": payload.get("id"),
        "remote_id": payload.get("remote_id"),
        "word_key": payload.get("word_key") or build_word_key(payload),
        "word": payload.get("word"),
        "phonetic": payload.get("phonetic"),
        "meaning": payload.get("meaning"),
        "example": payload.get("example"),
        "wordbook_id": payload.get("wordbook_id"),
        "wordbook_remote_id": payload.get("wordbook_remote_id"),
        "wordbook_key": payload.get("wordbook_key"),
        "wordbook_name": payload.get("wordbook_name"),
        "created_at": payload.get("created_at"),
        "updated_at": payload.get("updated_at"),
        "deleted_at": payload.get("deleted_at"),
        "version": payload.get("version"),
        "device_id": payload.get("device_id"),
        "sync_state": payload.get("sync_state"),
        "last_synced_at": payload.get("last_synced_at"),
    }
