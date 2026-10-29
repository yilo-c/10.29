"""Task smoke tests: create, complete, delete, close/reopen preserves data."""
from __future__ import annotations

import sys
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from data.local_storage import LocalDataStore
from repositories._task import LocalPersonalTaskRepository


def test_task_create_and_reopen(tmp_path):
    """Create a task, close/reopen '"task survives."""
    store = LocalDataStore(base_dir=str(tmp_path))
    repo = LocalPersonalTaskRepository(store)

    task_id = repo.add_task({"name": "测试任务", "note": "备注鍐呭"})
    assert task_id is not None

    # Simulate close
    saved_id = task_id
    del repo
    del store

    # Reopen
    store2 = LocalDataStore(base_dir=str(tmp_path))
    repo2 = LocalPersonalTaskRepository(store2)

    tasks = repo2.list_tasks()
    match = [t for t in tasks if t.get("id") == saved_id]
    assert len(match) == 1
    assert match[0]["name"] == "测试任务"
    assert match[0]["note"] == "备注鍐呭"


def test_task_complete_toggle(tmp_path):
    """Toggle task completion, verify persistence."""
    store = LocalDataStore(base_dir=str(tmp_path))
    repo = LocalPersonalTaskRepository(store)

    task_id = repo.add_task({"name": "鍙畬成的任务"})
    tasks = repo.list_tasks()
    task = next(t for t in tasks if t["id"] == task_id)
    assert not task.get("completed")

    repo.toggle_task_completion(task_id, True)
    tasks = repo.list_tasks()
    task = next(t for t in tasks if t["id"] == task_id)
    assert task.get("completed") is True

    repo.toggle_task_completion(task_id, False)
    tasks = repo.list_tasks()
    task = next(t for t in tasks if t["id"] == task_id)
    assert task.get("completed") is False


def test_task_delete(tmp_path):
    """Delete a task, verify removal."""
    store = LocalDataStore(base_dir=str(tmp_path))
    repo = LocalPersonalTaskRepository(store)

    task_id = repo.add_task({"name": "待删闄"})
    assert any(t.get("id") == task_id for t in repo.list_tasks())

    repo.delete_task(task_id)
    assert not any(t.get("id") == task_id for t in repo.list_tasks())
