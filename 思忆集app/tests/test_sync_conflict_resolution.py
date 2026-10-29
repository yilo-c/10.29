# -*- coding: utf-8 -*-
"""P0-2：同步冲突可见可恢复测试。

覆盖：
- _merge_todos 冲突分支保留本地快照（versions）并发 sync.conflict 事件
- sync.conflicts.list 列出冲突条目
- sync.resolve_conflict remote/local 裁决 + 参数校验
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from core.events import AppEvent, event_bus
from sync.cloud_sync_client import CloudSyncClient


# ── fakes ──


class FakeTaskRepo:
    def __init__(self, tasks):
        self.tasks = {t["id"]: dict(t) for t in tasks}
        self.updates: list[tuple[str, dict]] = []

    def list_tasks(self, include_deleted=False):
        return list(self.tasks.values())

    def update_task(self, task_id, new_info):
        self.updates.append((task_id, dict(new_info)))
        self.tasks[task_id].update(new_info)

    def add_task(self, info):
        self.tasks[info["id"]] = dict(info)

    def delete_task(self, task_id, soft=True):
        self.tasks.pop(task_id, None)


class FakeNoteRepo:
    def __init__(self, notes):
        self.notes = {n["id"]: dict(n) for n in notes}
        self.updates: list[tuple[str, dict]] = []

    def list_notes(self, include_deleted=False):
        return list(self.notes.values())

    def get_note(self, note_id):
        return self.notes.get(note_id)

    def update_note(self, note_id, new_info):
        self.updates.append((note_id, dict(new_info)))
        self.notes[note_id].update(new_info)

    def add_note(self, info):
        self.notes[info["id"]] = dict(info)


class FakeRegistry:
    def __init__(self):
        self.handlers: dict[str, object] = {}

    def register(self, name, fn):
        self.handlers[name] = fn


class FakeContext:
    """测试夹具：属性名必须与生产 AppContext 真实注入名一致（R0 B-17）。

    历史教训：此夹具曾用 ``note_repository``/``task_repository``——生产 AppContext
    只注入 ``personal_note_repository``/``personal_task_repository``（见
    _context_builder.py:99-100），夹具提供错误世界的属性名导致测试绿灯、
    生产死线（sync.conflicts.list 恒空）。改名后生产代码若仍用旧名，本文件
    测试立即变红。
    """

    def __init__(self, note_repo=None, task_repo=None):
        self.personal_note_repository = note_repo
        self.personal_task_repository = task_repo
        self.cloud_sync_bridge = None
        self.settings_repository = None


def _make_client():
    return CloudSyncClient(
        worker_url="https://sync.example.workers.dev",
        sync_token="tok",
        device_id="desktop-test",
        register_secret="reg",
    )


# ── merge 冲突保留与事件 ──


def test_merge_todos_conflict_preserves_local_snapshot_and_emits_event():
    local_task = {
        "id": "t1",
        "title": "本地版",
        "completed": False,
        "updated_at": "2026-08-05T00:00:00Z",  # 本地在同步点之后改过
        "last_synced_at": "2026-08-01T00:00:00Z",
    }
    repo = FakeTaskRepo([local_task])
    client = _make_client()
    client.task_repo = repo

    received: list[AppEvent] = []
    event_bus.on("sync.conflict", received.append)
    try:
        remote = [
            {
                "id": "t1",
                "text": "远端版",
                "done": True,
                "updatedAt": "2026-08-06T00:00:00Z",  # 远端在同步点之后也改过 → 真冲突
                "createdAt": "2026-07-01T00:00:00Z",
            }
        ]
        client._merge_todos(remote)
    finally:
        event_bus.off("sync.conflict", received.append)

    # 冲突标记 + 本地快照保留
    merged = repo.tasks["t1"]
    assert merged["sync_state"] == "conflict"
    versions = merged.get("versions") or []
    assert versions, "冲突覆盖前必须保留本地快照"
    assert versions[0]["title"] == "本地版"
    # 当前内容为远端版（LWW 保持），等待用户裁决
    assert merged["title"] == "远端版"
    # sync.conflict 事件已发出
    assert len(received) == 1
    assert received[0].payload["resource"] == "todo"
    assert received[0].payload["id"] == "t1"


# ── 裁决 RPC ──


@pytest.fixture
def conflict_env():
    note = {
        "id": "n1",
        "title": "远端标题",
        "content": "远端内容",
        "sync_state": "conflict",
        "updated_at": "2026-08-06T00:00:00Z",
        "versions": [{"title": "本地标题", "content": "本地内容", "type": "note", "folder": "f1", "tags": ["a"]}],
    }
    task = {
        "id": "t1",
        "title": "远端任务",
        "completed": True,
        "sync_state": "conflict",
        "updated_at": "2026-08-06T00:00:00Z",
        "versions": [{"title": "本地任务", "completed": False, "category": "c", "priority": "high", "due_date": None}],
    }
    note_repo = FakeNoteRepo([note])
    task_repo = FakeTaskRepo([task])
    ctx = FakeContext(note_repo, task_repo)
    registry = FakeRegistry()
    from backend.handlers._sync import register_sync

    register_sync(registry, ctx)
    return registry, note_repo, task_repo


@pytest.mark.asyncio
async def test_conflicts_list_returns_conflict_entries(conflict_env):
    registry, _notes, _tasks = conflict_env
    result = await registry.handlers["sync.conflicts.list"]()
    assert result["count"] == 2
    kinds = {(c["resource"], c["id"]) for c in result["conflicts"]}
    assert kinds == {("note", "n1"), ("todo", "t1")}
    assert all(c["has_local_version"] for c in result["conflicts"])


@pytest.mark.asyncio
async def test_resolve_conflict_remote_accepts_current(conflict_env):
    registry, note_repo, _tasks = conflict_env
    result = await registry.handlers["sync.resolve_conflict"](resource="note", id="n1", choice="remote")
    assert result["success"] is True
    assert note_repo.notes["n1"]["sync_state"] == "synced"
    assert note_repo.notes["n1"]["title"] == "远端标题"


@pytest.mark.asyncio
async def test_resolve_conflict_local_restores_snapshot(conflict_env):
    registry, note_repo, task_repo = conflict_env
    result = await registry.handlers["sync.resolve_conflict"](resource="note", id="n1", choice="local")
    assert result["success"] is True
    n = note_repo.notes["n1"]
    assert n["title"] == "本地标题"
    assert n["content"] == "本地内容"
    assert n["sync_state"] == "dirty"  # 下次同步推回远端

    result = await registry.handlers["sync.resolve_conflict"](resource="todo", id="t1", choice="local")
    assert result["success"] is True
    t = task_repo.tasks["t1"]
    assert t["title"] == "本地任务"
    assert t["sync_state"] == "dirty"


@pytest.mark.asyncio
async def test_resolve_conflict_validation_errors(conflict_env):
    registry, _notes, _tasks = conflict_env
    resolve = registry.handlers["sync.resolve_conflict"]

    assert (await resolve(resource="bad", id="n1", choice="remote"))["success"] is False
    assert (await resolve(resource="note", id="", choice="remote"))["success"] is False
    assert (await resolve(resource="note", id="n1", choice="maybe"))["success"] is False
    assert (await resolve(resource="note", id="missing", choice="remote"))["success"] is False
