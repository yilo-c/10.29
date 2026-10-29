"""Handler-level tests for backend.handlers._tasks (tasks.* JSON-RPC methods).

验证 tasks.list / tasks.create / tasks.get / tasks.update / tasks.delete /
tasks.toggle 的接线正确性，使用 mock context + mock repository。
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from backend.method_registry import MethodRegistry
from backend.handlers._tasks import register_tasks


class FakeTaskRepo:
    """内存版任务仓库，模拟 personal_task_repository 的同步接口。"""

    def __init__(self) -> None:
        self._tasks: list[dict] = []
        self._next_id = 1

    def list_tasks(self, include_deleted: bool = False) -> list[dict]:
        tasks = [dict(t) for t in self._tasks]
        if include_deleted:
            return tasks
        return [t for t in tasks if not t.get("deleted_at")]

    def get_task(self, task_id) -> dict | None:
        for t in self._tasks:
            if t.get("id") == task_id:
                return dict(t)
        return None

    def add_task(self, info: dict) -> int:
        task_id = self._next_id
        self._next_id += 1
        task = {"id": task_id, **info}
        self._tasks.append(task)
        return task_id

    def update_task(self, task_id, new_info: dict) -> None:
        for t in self._tasks:
            if t.get("id") == task_id:
                t.update(new_info)
                return

    def delete_task(self, task_id) -> None:
        self._tasks = [t for t in self._tasks if t.get("id") != task_id]

    def toggle_task_completion(self, task_id, completed: bool) -> None:
        for t in self._tasks:
            if t.get("id") == task_id:
                t["completed"] = completed
                return


@pytest.fixture
def tasks_env():
    """构建 registry + context，注册 tasks.* 方法。"""
    registry = MethodRegistry()
    repo = FakeTaskRepo()
    context = MagicMock()
    context.personal_task_repository = repo
    register_tasks(registry, context)
    return registry, context, repo


# ── tasks.list ──


async def test_tasks_list_empty(tasks_env):
    registry, _, _ = tasks_env
    result = await registry.dispatch("tasks.list", {})
    assert result == []


async def test_tasks_list_with_data(tasks_env):
    registry, _, repo = tasks_env
    repo.add_task({"title": "任务A", "completed": False})
    repo.add_task({"title": "任务B", "completed": True})
    result = await registry.dispatch("tasks.list", {})
    assert len(result) == 2
    titles = {t["title"] for t in result}
    assert titles == {"任务A", "任务B"}


async def test_tasks_list_limit(tasks_env):
    registry, _, repo = tasks_env
    for i in range(10):
        repo.add_task({"title": f"任务{i}"})
    result = await registry.dispatch("tasks.list", {"limit": 3})
    assert len(result) == 3


async def test_tasks_list_no_repo(tasks_env):
    registry, context, _ = tasks_env
    context.personal_task_repository = None
    result = await registry.dispatch("tasks.list", {})
    assert result == []


# ── tasks.list_trashed（批次4 b4-m2：手机端任务墓碑）──


async def test_tasks_list_trashed_returns_soft_deleted(tasks_env):
    registry, _, repo = tasks_env
    repo.add_task({"title": "活跃任务", "deleted_at": None})
    repo.add_task({"title": "已删除任务", "deleted_at": "2026-07-04T00:00:00Z"})
    result = await registry.dispatch("tasks.list_trashed", {})
    assert len(result) == 1
    assert result[0]["title"] == "已删除任务"
    assert result[0]["deleted_at"] == "2026-07-04T00:00:00Z"


async def test_tasks_list_trashed_excludes_active(tasks_env):
    registry, _, repo = tasks_env
    repo.add_task({"title": "活跃任务"})
    result = await registry.dispatch("tasks.list_trashed", {})
    assert result == []


async def test_tasks_list_trashed_no_repo(tasks_env):
    registry, context, _ = tasks_env
    context.personal_task_repository = None
    result = await registry.dispatch("tasks.list_trashed", {})
    assert result == []


# ── tasks.create ──


async def test_tasks_create(tasks_env):
    registry, _, repo = tasks_env
    result = await registry.dispatch("tasks.create", {"title": "新任务", "priority": "high"})
    assert result["id"] == 1
    assert result["title"] == "新任务"
    assert result["priority"] == "high"
    assert len(repo.list_tasks()) == 1


async def test_tasks_create_no_repo(tasks_env):
    registry, context, _ = tasks_env
    context.personal_task_repository = None
    result = await registry.dispatch("tasks.create", {"title": "x"})
    assert "error" in result


# ── tasks.get ──


async def test_tasks_get_found(tasks_env):
    registry, _, repo = tasks_env
    task_id = repo.add_task({"title": "查找我"})
    result = await registry.dispatch("tasks.get", {"task_id": task_id})
    assert result is not None
    assert result["title"] == "查找我"


async def test_tasks_get_not_found(tasks_env):
    registry, _, _ = tasks_env
    result = await registry.dispatch("tasks.get", {"task_id": 999})
    assert result is None


async def test_tasks_get_no_repo(tasks_env):
    registry, context, _ = tasks_env
    context.personal_task_repository = None
    result = await registry.dispatch("tasks.get", {"task_id": 1})
    assert result is None


# ── tasks.update ──


async def test_tasks_update(tasks_env):
    registry, _, repo = tasks_env
    task_id = repo.add_task({"title": "原标题"})
    result = await registry.dispatch("tasks.update", {"task_id": task_id, "title": "新标题"})
    assert result is True
    tasks = repo.list_tasks()
    assert tasks[0]["title"] == "新标题"


async def test_tasks_update_no_repo(tasks_env):
    registry, context, _ = tasks_env
    context.personal_task_repository = None
    result = await registry.dispatch("tasks.update", {"task_id": 1, "title": "x"})
    assert result is False


# ── tasks.delete ──


async def test_tasks_delete(tasks_env):
    registry, _, repo = tasks_env
    task_id = repo.add_task({"title": "待删除"})
    result = await registry.dispatch("tasks.delete", {"task_id": task_id})
    assert result is True
    assert len(repo.list_tasks()) == 0


async def test_tasks_delete_no_repo(tasks_env):
    registry, context, _ = tasks_env
    context.personal_task_repository = None
    result = await registry.dispatch("tasks.delete", {"task_id": 1})
    assert result is False


# ── tasks.toggle ──


async def test_tasks_toggle(tasks_env):
    registry, _, repo = tasks_env
    task_id = repo.add_task({"title": "切换", "completed": False})
    result = await registry.dispatch("tasks.toggle", {"task_id": task_id, "completed": True})
    assert result is True
    tasks = repo.list_tasks()
    assert tasks[0]["completed"] is True


async def test_tasks_toggle_no_repo(tasks_env):
    registry, context, _ = tasks_env
    context.personal_task_repository = None
    result = await registry.dispatch("tasks.toggle", {"task_id": 1, "completed": True})
    assert result is False
