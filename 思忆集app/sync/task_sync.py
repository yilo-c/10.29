from __future__ import annotations

from copy import deepcopy
from .note_sync import (
    SYNC_STATE_CONFLICT,
    SYNC_STATE_DIRTY,
    SYNC_STATE_LOCAL_ONLY,
    SYNC_STATE_SYNCED,
    get_or_create_local_device_id,
)


def ensure_task_sync_metadata(task, data_store, mark_dirty=False):
    changed = False
    if not isinstance(task, dict):
        return changed

    if not task.get("device_id"):
        task["device_id"] = get_or_create_local_device_id(data_store)
        changed = True

    if "remote_id" not in task:
        task["remote_id"] = None
        changed = True

    version = task.get("version")
    if not isinstance(version, int) or version < 1:
        task["version"] = 1
        changed = True

    if "last_synced_at" not in task:
        task["last_synced_at"] = None
        changed = True

    if not task.get("sync_state"):
        task["sync_state"] = SYNC_STATE_LOCAL_ONLY if not task.get("remote_id") else SYNC_STATE_SYNCED
        changed = True

    if mark_dirty:
        current_state = task.get("sync_state")
        if current_state == SYNC_STATE_CONFLICT:
            # 冲突状态优先级最高，不被 mark_dirty 覆盖（需用户手动解决）
            pass
        else:
            expected_state = SYNC_STATE_LOCAL_ONLY if not task.get("remote_id") else SYNC_STATE_DIRTY
            if current_state != expected_state:
                task["sync_state"] = expected_state
                changed = True

    return changed


def build_task_sync_payload(task):
    payload = deepcopy(task or {})
    is_completed = bool(payload.get("is_completed", payload.get("completed", False)))
    return {
        "id": payload.get("id"),
        "remote_id": payload.get("remote_id"),
        "name": payload.get("name"),
        "note": payload.get("note"),
        "is_completed": is_completed,
        "completed": is_completed,
        "reminder_time": payload.get("reminder_time"),
        "created_at": payload.get("created_at"),
        "updated_at": payload.get("updated_at"),
        "deleted_at": payload.get("deleted_at"),
        "version": payload.get("version"),
        "device_id": payload.get("device_id"),
        "sync_state": payload.get("sync_state"),
        "last_synced_at": payload.get("last_synced_at"),
        "linked_note_id": payload.get("linked_note_id"),
    }
