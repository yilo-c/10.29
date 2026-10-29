from __future__ import annotations

import uuid
from copy import deepcopy

from core.sync_protocol import SyncState

# sync_state 四值的唯一真源已上提至 core.sync_protocol.SyncState（波次④包B，
# 蓝本 §1a-2）。以下 4 常量为枚举 .value 的 re-export，保持既有
# `from sync.note_sync import SYNC_STATE_*` 消费者不破（_note.py/_task.py/
# _vocab_sync_mixin.py/_translation.py/desknotes_note_adapter.py/
# translation_sync.py/vocabulary_sync.py 及 sync 包内各模块）。
# 散字面量回归由 tests/test_wave4b_sync_state_ssot_20260924.py AST 棘轮拦截。
SYNC_STATE_LOCAL_ONLY = SyncState.LOCAL_ONLY.value
SYNC_STATE_DIRTY = SyncState.DIRTY.value
SYNC_STATE_SYNCED = SyncState.SYNCED.value
# 冲突状态：本地和远端同时修改了同一资源，需要用户手动解决或等待合并策略处理。
# 此状态优先级高于 DIRTY — 处于 CONFLICT 的条目不会被自动同步覆盖。
SYNC_STATE_CONFLICT = SyncState.CONFLICT.value


def get_or_create_local_device_id(data_store):
    if data_store is None:
        return "desktop-unknown"
    settings = data_store.load_json("settings", {})
    device_id = settings.get("siyi_device_id")
    if isinstance(device_id, str) and device_id.strip():
        return device_id.strip()
    device_id = f"desktop-{uuid.uuid4().hex[:12]}"
    settings["siyi_device_id"] = device_id
    data_store.save_json("settings", settings)
    return device_id


def ensure_note_sync_metadata(note, data_store, mark_dirty=False):
    changed = False
    if not isinstance(note, dict):
        return changed

    if not note.get("device_id"):
        note["device_id"] = get_or_create_local_device_id(data_store)
        changed = True

    if "remote_id" not in note:
        note["remote_id"] = None
        changed = True

    version = note.get("version")
    if not isinstance(version, int) or version < 1:
        note["version"] = 1
        changed = True

    if "last_synced_at" not in note:
        note["last_synced_at"] = None
        changed = True

    if not note.get("sync_state"):
        note["sync_state"] = SYNC_STATE_LOCAL_ONLY if not note.get("remote_id") else SYNC_STATE_SYNCED
        changed = True

    if mark_dirty:
        current_state = note.get("sync_state")
        if current_state == SYNC_STATE_CONFLICT:
            # 冲突状态优先级最高，不被 mark_dirty 覆盖（需用户手动解决）
            pass
        else:
            expected_state = SYNC_STATE_LOCAL_ONLY if not note.get("remote_id") else SYNC_STATE_DIRTY
            if current_state != expected_state:
                note["sync_state"] = expected_state
                changed = True

    return changed


def build_note_sync_payload(note):
    payload = deepcopy(note or {})
    return {
        "id": payload.get("id"),
        "remote_id": payload.get("remote_id"),
        "title": payload.get("title"),
        "content": payload.get("content"),
        "tags": list(payload.get("tags") or []),
        "category": payload.get("category"),
        "folder_path": payload.get("folder_path") or "/",
        "favorite": bool(payload.get("favorite", False)),
        "created_at": payload.get("created_at"),
        "updated_at": payload.get("updated_at"),
        "deleted_at": payload.get("deleted_at"),
        "version": payload.get("version"),
        "device_id": payload.get("device_id"),
        "sync_state": payload.get("sync_state"),
        "last_synced_at": payload.get("last_synced_at"),
        "related_news_url": payload.get("related_news_url"),
        "linked_task_id": payload.get("linked_task_id"),
    }
