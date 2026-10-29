"""notes 记忆回响测试 — 保存笔记后推送相关旧记忆（T7.2）。

覆盖：notes_create / notes_update（写保护路径与回退路径）保存后
广播 notes.related_memories 事件；空内容/检索失败不阻塞保存主流程。
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch


APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from backend.handlers._notes import register_notes


class _Registry:
    def __init__(self):
        self.registered: dict[str, object] = {}

    def register(self, name: str, fn, risk=None, risk_resolver=None) -> None:
        self.registered[name] = fn

    def register_with_schema(self, name: str, fn, schema=None, risk=None, risk_resolver=None) -> None:
        self.registered[name] = fn


class _FakeNoteRepo:
    def __init__(self):
        self.notes: list[dict] = []

    def add_note(self, info: dict) -> int:
        self.notes.append({**info, "id": len(self.notes) + 1})
        return len(self.notes)

    def get_note(self, nid):
        for n in self.notes:
            if str(n.get("id")) == str(nid):
                return n
        return None

    def update_note(self, nid, info):
        for i, n in enumerate(self.notes):
            if str(n.get("id")) == str(nid):
                self.notes[i] = {**n, **info}
                return True
        return False

    def list_notes(self, include_deleted=False):
        return list(self.notes)


class _FakeMemoryIndex:
    def __init__(self, items=None):
        self._items = items or [
            {"id": "m1", "title": "旧记忆一", "content": "去年学的英语计划", "source_type": "memory"},
            {"id": "m2", "title": "旧记忆二", "content": "项目上线记录", "source_type": "note"},
        ]

    def recall(self, query, top_k=8, **kw):
        return {"items": self._items[:top_k], "count": min(len(self._items), top_k)}


def _make_context():
    repo = _FakeNoteRepo()
    ctx = SimpleNamespace(
        personal_note_repository=repo,
        memory_index_service=_FakeMemoryIndex(),
        settings_repository=None,
        write_guard=None,
    )
    return ctx, repo


# ── notes_create 回响 ──


def test_create_emits_related_memories():
    ctx, repo = _make_context()
    reg = _Registry()
    emitted = []

    with patch("backend.handlers._notes.event_bus") as fake_bus:
        fake_bus.emit.side_effect = lambda ev: emitted.append(ev)
        register_notes(reg, ctx)
        result = asyncio.run(reg.registered["notes.create"](title="新笔记", content="我想记录一下学英语的进展"))

    assert result["id"] == 1
    # notes.changed + notes.related_memories 都应广播
    types = [e.event_type for e in emitted]
    assert "notes.related_memories" in types
    related_ev = next(e for e in emitted if e.event_type == "notes.related_memories")
    payload = related_ev.payload or {}
    assert len(payload.get("related_memories", [])) >= 1
    assert payload["related_memories"][0]["title"]


def test_create_empty_content_no_related():
    """空内容不触发记忆回响（但仍正常保存）。"""
    ctx, repo = _make_context()
    reg = _Registry()
    emitted = []

    with patch("backend.handlers._notes.event_bus") as fake_bus:
        fake_bus.emit.side_effect = lambda ev: emitted.append(ev)
        register_notes(reg, ctx)
        result = asyncio.run(reg.registered["notes.create"](title="空笔记", content=""))

    assert result["id"] == 1
    types = [e.event_type for e in emitted]
    assert "notes.related_memories" not in types


def test_create_recall_failure_does_not_block_save():
    """记忆检索抛异常时笔记仍保存成功（best-effort）。"""
    ctx, repo = _make_context()

    class _Boom:
        def recall(self, *a, **k):
            raise RuntimeError("chroma down")

    ctx.memory_index_service = _Boom()
    reg = _Registry()

    with patch("backend.handlers._notes.event_bus"):
        register_notes(reg, ctx)
        result = asyncio.run(reg.registered["notes.create"](title="不阻断", content="内容"))

    assert result["id"] == 1
    assert len(repo.notes) == 1


# ── notes_update 回响 ──


def test_update_emits_related_memories():
    ctx, repo = _make_context()
    reg = _Registry()
    emitted = []

    with patch("backend.handlers._notes.event_bus") as fake_bus:
        fake_bus.emit.side_effect = lambda ev: emitted.append(ev)
        register_notes(reg, ctx)
        # 先创建
        asyncio.run(reg.registered["notes.create"](title="a", content="初始"))
        # 再更新
        ok = asyncio.run(reg.registered["notes.update"](note_id=1, content="更新后的学习计划"))

    assert ok is True
    types = [e.event_type for e in emitted]
    assert "notes.related_memories" in types


# ── T4.1: notes 破坏性操作（delete/restore/permanent_delete/batch_delete）──


def _make_note_repo_with_del():
    """扩展 _FakeNoteRepo 支持 delete/restore。"""
    repo = _FakeNoteRepo()

    def delete_note(nid, soft=True):
        for n in repo.notes:
            if str(n.get("id")) == str(nid):
                if soft:
                    n["deleted_at"] = "2026-08-23T00:00:00"
                else:
                    repo.notes.remove(n)
                return True
        return False

    def restore_note(nid):
        for n in repo.notes:
            if str(n.get("id")) == str(nid):
                n.pop("deleted_at", None)
                return True
        return False

    repo.delete_note = delete_note
    repo.restore_note = restore_note
    return repo


def test_delete_cascades_and_notifies():
    repo = _make_note_repo_with_del()
    repo.notes.append({"id": 1, "title": "a", "content": "x"})
    ctx = SimpleNamespace(
        personal_note_repository=repo,
        memory_index_service=_FakeMemoryIndex(),
        settings_repository=None,
        write_guard=None,
    )
    reg = _Registry()
    emitted = []
    with patch("backend.handlers._notes.event_bus") as fake_bus:
        fake_bus.emit.side_effect = lambda ev: emitted.append(ev)
        with patch("backend.handlers._notes.cascade_delete_note", new=AsyncMock(return_value={})):
            register_notes(reg, ctx)
            ok = asyncio.run(reg.registered["notes.delete"](note_id=1))

    assert ok is True
    assert repo.notes[0].get("deleted_at")  # 软删除
    types = [e.event_type for e in emitted]
    assert "notes.changed" in types


def test_delete_no_repo_returns_false():
    ctx = SimpleNamespace(personal_note_repository=None)
    reg = _Registry()
    register_notes(reg, ctx)
    ok = asyncio.run(reg.registered["notes.delete"](note_id=1))
    assert ok is False


def test_restore_clears_deleted_at():
    repo = _make_note_repo_with_del()
    repo.notes.append({"id": 1, "title": "a", "content": "x", "deleted_at": "2026-08-23T00:00:00"})
    ctx = SimpleNamespace(personal_note_repository=repo, write_guard=None)
    reg = _Registry()
    with patch("backend.handlers._notes.event_bus"):
        register_notes(reg, ctx)
        ok = asyncio.run(reg.registered["notes.restore"](note_id=1))

    assert ok is True
    assert "deleted_at" not in repo.notes[0]


def test_permanent_delete_cascades():
    # S0-2：permanent_delete 只对「回收站里的软删笔记」生效（活笔记拒绝——见
    # test_ghost_update_and_perm_20260916.test_permanent_delete_rejects_live_note）。
    # 原测试直接对活笔记 perm 期望 True，正是修复前的倒挂行为，改为先软删。
    repo = _make_note_repo_with_del()
    repo.notes.append({"id": 1, "title": "a", "content": "x", "deleted_at": "2026-08-23T00:00:00"})
    ctx = SimpleNamespace(personal_note_repository=repo, write_guard=None)
    reg = _Registry()
    with patch("backend.handlers._notes.event_bus"):
        with patch("backend.handlers._notes.cascade_delete_note", new=AsyncMock(return_value={})) as mock_cascade:
            register_notes(reg, ctx)
            ok = asyncio.run(reg.registered["notes.permanent_delete"](note_id=1))

    assert ok is True
    assert len(repo.notes) == 0  # 硬删除
    mock_cascade.assert_awaited_once_with(ctx, 1)


def test_batch_delete_registered():
    """2026-10-15 反转：notes.batch_delete **必须注册**（批次 A c6fb2bc7 恢复）。

    原断言「已随 58e5fa8 下线、不再注册」依据该提交自述（前端零调用/无测试耦合），
    而 58e5fa8 连测试一并删走造成"无耦合"假象；全族复核后注册已恢复
    （_R4_METHODS 成员，走 sandbox 审批）。反转以防再次误下线。
    """
    repo = _make_note_repo_with_del()
    ctx = SimpleNamespace(personal_note_repository=repo, write_guard=None)
    reg = _Registry()
    with patch("backend.handlers._notes.event_bus"):
        register_notes(reg, ctx)
    assert "notes.batch_delete" in reg.registered


def test_list_trashed_filters_deleted():
    repo = _make_note_repo_with_del()
    repo.notes.append({"id": 1, "title": "a", "content": "x", "deleted_at": "2026-08-23T00:00:00"})
    repo.notes.append({"id": 2, "title": "b", "content": "y"})
    ctx = SimpleNamespace(personal_note_repository=repo)
    reg = _Registry()
    register_notes(reg, ctx)
    trashed = asyncio.run(reg.registered["notes.list_trashed"]())
    assert len(trashed) == 1
    assert trashed[0]["id"] == 1
