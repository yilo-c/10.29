"""L8 兼容：手机端 ↔ 桌面端 schema 对齐（组件级，真实仓库）。

对比桌面端 handlers / 仓库序列化字段与手机端
``思忆集手机端app/src/lib/contentSync.ts`` 实际读写的字段，确保双向契约
不回归：

- 手机端 ``pushNote`` 发 ``{title, content, tags}`` → ``notes.create`` →
  ``notes.list`` 可读回 ``id/title/content/tags/created_at/updated_at``；
- 手机端 ``pushTask`` 发 ``{text, done, priority}`` → ``tasks.create`` →
  完成态 ``done`` 映射到 ``completed``，拉回后 ``text`` / ``priority`` 保留；
- 时间戳为 ISO 字符串（手机端 ``new Date()`` 可解析）。

用真实 ``LocalPersonalNoteRepository`` / ``LocalPersonalTaskRepository``
+ 真实 ``LocalDataStore``，不依赖后端进程。
"""
from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock

import pytest

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from backend.handlers._notes import register_notes  # noqa: E402
from backend.handlers._tasks import register_tasks  # noqa: E402
from backend.method_registry import MethodRegistry  # noqa: E402
from data.local_storage import LocalDataStore  # noqa: E402
from repositories._note import LocalPersonalNoteRepository  # noqa: E402
from repositories._task import LocalPersonalTaskRepository  # noqa: E402


@pytest.fixture
def env(tmp_path):
    ds = LocalDataStore(base_dir=str(tmp_path / "data"))
    registry = MethodRegistry()
    context = MagicMock()
    context.personal_task_repository = LocalPersonalTaskRepository(ds)
    context.personal_note_repository = LocalPersonalNoteRepository(ds)
    register_tasks(registry, context)
    register_notes(registry, context)
    yield {"registry": registry}
    ds.close()


class TestNoteParity:
    async def test_mobile_note_payload_roundtrips(self, env) -> None:
        """手机端 pushNote {title, content, tags} 全字段可读回。"""
        reg = env["registry"]
        await reg.dispatch(
            "notes.create", {"title": "便签", "content": "正文", "tags": ["tag1"]}
        )
        notes = await reg.dispatch("notes.list", {})
        assert len(notes) == 1
        note = notes[0]
        for field in ("id", "title", "content", "tags", "created_at", "updated_at"):
            assert field in note, f"notes.list 缺字段 {field}: {note}"
        assert note["title"] == "便签"
        assert note["content"] == "正文"
        assert note["tags"] == ["tag1"]

    async def test_note_timestamp_is_iso(self, env) -> None:
        """created_at/updated_at 为 ISO 字符串，手机端 new Date() 可解析。"""
        reg = env["registry"]
        await reg.dispatch("notes.create", {"title": "t"})
        note = (await reg.dispatch("notes.list", {}))[0]
        datetime.fromisoformat(note["created_at"])
        datetime.fromisoformat(note["updated_at"])


class TestTaskParity:
    async def test_mobile_task_done_maps_to_completed(self, env) -> None:
        """手机端 pushTask {text, done: true, priority} → 拉回 completed=True。"""
        reg = env["registry"]
        await reg.dispatch("tasks.create", {"text": "买牛奶", "done": True, "priority": 3})
        task = (await reg.dispatch("tasks.list", {}))[0]
        assert task["completed"] is True, f"done 未映射到 completed: {task}"
        assert task["text"] == "买牛奶"
        assert task["priority"] == 3

    async def test_mobile_task_payload_priority_preserved(self, env) -> None:
        reg = env["registry"]
        await reg.dispatch("tasks.create", {"text": "低优先级", "done": False, "priority": 1})
        task = (await reg.dispatch("tasks.list", {}))[0]
        assert task["priority"] == 1
        assert task["completed"] is False

    async def test_task_list_returns_mobile_readable_fields(self, env) -> None:
        """tasks.list 返回字段覆盖手机端 syncTasks 读取面。"""
        reg = env["registry"]
        await reg.dispatch("tasks.create", {"text": "任务"})
        task = (await reg.dispatch("tasks.list", {}))[0]
        for field in ("id", "title", "text", "completed", "created_at"):
            assert field in task, f"tasks.list 缺字段 {field}: {task}"

    async def test_task_timestamp_is_iso(self, env) -> None:
        reg = env["registry"]
        await reg.dispatch("tasks.create", {"text": "t"})
        task = (await reg.dispatch("tasks.list", {}))[0]
        datetime.fromisoformat(task["created_at"])
