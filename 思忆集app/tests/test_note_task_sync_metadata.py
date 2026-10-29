"""P1-7：ensure_note_sync_metadata / ensure_task_sync_metadata 的 CONFLICT 优先级回归测试。

修复点：
- mark_dirty=True 时，处于 SYNC_STATE_CONFLICT 的条目不应被覆盖回 DIRTY；
- 仅校验 DIRTY/LOCAL_ONLY 之间的迁移仍然正常工作。
"""
from __future__ import annotations

import sys
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from sync.note_sync import (  # noqa: E402  (sys.path 调整后)
    SYNC_STATE_CONFLICT,
    SYNC_STATE_DIRTY,
    SYNC_STATE_LOCAL_ONLY,
    SYNC_STATE_SYNCED,
    ensure_note_sync_metadata,
)
from sync.task_sync import ensure_task_sync_metadata  # noqa: E402


class _StubStore:
    """LocalDataStore 的最小替身，仅满足 get_or_create_local_device_id 所需的接口。"""

    def __init__(self, device_id: str = "desktop-test-0001"):
        self._settings = {"siyi_device_id": device_id}

    def load_json(self, name, default=None):
        if name == "settings":
            return dict(self._settings)
        return default

    def save_json(self, name, value):
        if name == "settings":
            self._settings = dict(value or {})


# ── note_sync ────────────────────────────────────────────────────────────────


def test_note_mark_dirty_preserves_conflict_state():
    store = _StubStore()
    note = {
        "id": "n1",
        "remote_id": "r1",
        "version": 3,
        "sync_state": SYNC_STATE_CONFLICT,
        "last_synced_at": "2026-04-18T00:00:00",
        "device_id": "desktop-test-0001",
    }

    changed = ensure_note_sync_metadata(note, store, mark_dirty=True)

    assert note["sync_state"] == SYNC_STATE_CONFLICT, "CONFLICT 状态必须被保留"
    assert changed is False, "无字段需要迁移时不应报告 changed"


def test_note_mark_dirty_transitions_synced_to_dirty():
    store = _StubStore()
    note = {
        "id": "n2",
        "remote_id": "r2",
        "version": 1,
        "sync_state": SYNC_STATE_SYNCED,
        "last_synced_at": "2026-04-18T00:00:00",
        "device_id": "desktop-test-0001",
    }

    changed = ensure_note_sync_metadata(note, store, mark_dirty=True)

    assert note["sync_state"] == SYNC_STATE_DIRTY
    assert changed is True


def test_note_mark_dirty_keeps_local_only_when_no_remote_id():
    store = _StubStore()
    note = {
        "id": "n3",
        "remote_id": None,
        "version": 1,
        "sync_state": SYNC_STATE_LOCAL_ONLY,
        "last_synced_at": None,
        "device_id": "desktop-test-0001",
    }

    changed = ensure_note_sync_metadata(note, store, mark_dirty=True)

    assert note["sync_state"] == SYNC_STATE_LOCAL_ONLY
    assert changed is False


# ── task_sync ────────────────────────────────────────────────────────────────


def test_task_mark_dirty_preserves_conflict_state():
    store = _StubStore()
    task = {
        "id": "t1",
        "remote_id": "r1",
        "version": 2,
        "sync_state": SYNC_STATE_CONFLICT,
        "last_synced_at": "2026-04-18T00:00:00",
        "device_id": "desktop-test-0001",
    }

    changed = ensure_task_sync_metadata(task, store, mark_dirty=True)

    assert task["sync_state"] == SYNC_STATE_CONFLICT
    assert changed is False


def test_task_mark_dirty_transitions_synced_to_dirty():
    store = _StubStore()
    task = {
        "id": "t2",
        "remote_id": "r2",
        "version": 1,
        "sync_state": SYNC_STATE_SYNCED,
        "last_synced_at": "2026-04-18T00:00:00",
        "device_id": "desktop-test-0001",
    }

    changed = ensure_task_sync_metadata(task, store, mark_dirty=True)

    assert task["sync_state"] == SYNC_STATE_DIRTY
    assert changed is True
