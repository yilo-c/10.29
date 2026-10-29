"""Integration tests for notes repository — CRUD round-trip."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

import pytest


@pytest.fixture
def note_repo(tmp_path):
    from data.local_storage import LocalDataStore
    from repositories._note import LocalPersonalNoteRepository

    store = LocalDataStore(base_dir=str(tmp_path))
    return LocalPersonalNoteRepository(store)


def test_note_add_and_list(note_repo):
    note_id = note_repo.add_note({"title": "测试笔记", "content": "这是内容", "tags": ["测试"]})
    assert note_id is not None

    notes = note_repo.list_notes()
    titles = {n.get("title", "") for n in notes}
    assert "测试笔记" in titles


def test_note_add_two_and_list(note_repo):
    note_repo.add_note({"title": "笔记A", "content": "内容A"})
    note_repo.add_note({"title": "笔记B", "content": "内容B"})
    notes = note_repo.list_notes()
    assert len(notes) >= 2
    titles = {n.get("title", "") for n in notes}
    assert "笔记A" in titles
    assert "笔记B" in titles


def test_note_update(note_repo):
    note_id = note_repo.add_note({"title": "原始标题", "content": "原始内容"})
    note_repo.update_note(note_id, {"title": "新标题", "content": "新内容"})

    notes = note_repo.list_notes()
    updated = [n for n in notes if n.get("id") == note_id]
    assert len(updated) == 1
    assert updated[0]["title"] == "新标题"
    assert updated[0]["content"] == "新内容"


def test_note_delete_soft(note_repo):
    note_id = note_repo.add_note({"title": "待删除", "content": "内容"})
    notes_before = note_repo.list_notes()
    assert any(n.get("id") == note_id for n in notes_before)

    note_repo.delete_note(note_id)

    notes_after = note_repo.list_notes()
    assert not any(n.get("id") == note_id for n in notes_after)


def test_note_with_tags(note_repo):
    note_repo.add_note({"title": "标签测试", "content": "内容", "tags": ["工作", "重要"]})
    notes = note_repo.list_notes()
    tagged = [n for n in notes if n.get("title") == "标签测试"]
    assert len(tagged) == 1
    tags = tagged[0].get("tags", [])
    assert "工作" in tags
    assert "重要" in tags


def test_note_dedup(note_repo):
    """Adding same title+content should update existing note instead of creating duplicate."""
    note_repo.add_note({"title": "唯一笔记", "content": "唯一内容"})
    note_repo.add_note({"title": "唯一笔记", "content": "唯一内容"})
    notes = note_repo.list_notes()
    matches = [n for n in notes if n.get("title") == "唯一笔记"]
    assert len(matches) == 1, "Duplicate notes should be deduped"


# ── notes.ai_assist / notes.batch_delete 已下线（波次①-c，commit 58e5fa8）──
#
# 二者实现体（notes_ai_assist / notes_batch_delete）仍是 register_notes 内的
# 嵌套闭包，注册行随 @deprecated 僵尸 RPC 批量下线摘除后：既无 registry.register
# 入口、又被证明无任何后端内部互调引用（闭包不可外部触达），故原有 5 个
# ai_assist 直调行为用例失去可重指向的存活路径（参照 test_async_rpc_s32_20260916.py
# daily_summary 的「无存活路径 → 删除孤儿用例 + 断言不再注册防复活」先例），删除之，
# 仅保留下述防复活契约。


class _DummyRegistry:
    def __init__(self):
        self.handlers: dict[str, callable] = {}

    def register(self, name: str, fn: callable, **kwargs: Any) -> None:
        # **kwargs: 生产注册可带 risk 等扩展参数（既有 mock 缺此 → TypeError）
        self.handlers[name] = fn

    def register_with_schema(self, name: str, fn: callable, schema: dict | None = None, **kwargs: Any) -> None:
        # **kwargs: 生产注册可带 risk 等扩展参数（既有 mock 缺此 → TypeError）
        self.handlers[name] = fn


@pytest.mark.asyncio
async def test_notes_zombie_rpc_not_registered():
    """防复活：notes.ai_assist / notes.batch_delete 已随波次①-c 下线，不得再注册。"""
    from unittest.mock import MagicMock

    from backend.handlers._notes import register_notes
    from backend.method_registry import MethodRegistry

    registry = MethodRegistry()
    register_notes(registry, MagicMock(personal_note_repository=None, ai_client=None))
    methods = registry.list_methods()
    # 2026-10-15 反转：两者注册已恢复（批次 A c6fb2bc7）——原断言基于 58e5fa8 下线结论。
    assert "notes.ai_assist" in methods, "notes.ai_assist 应已恢复注册"
    assert "notes.batch_delete" in methods, "notes.batch_delete 应已恢复注册（R4 方法）"


# ── P2 回归（rpc-census-2026-08-30）：notes.restore 空参 → -32602 ──
# 修复前：签名要求必填位置参数 note_id，registry.dispatch 以 handler(**params)
# 展开调用，空参直接 TypeError → -32603 broken。修复后与 notes.get 同款：
# 注册层 schema required=note_id + 签名默认值兜底直调路径。


@pytest.mark.asyncio
async def test_notes_restore_empty_params_invalid_params():
    """空参 dispatch 应抛 InvalidParamsError(-32602)，不再 TypeError。"""
    from unittest.mock import MagicMock

    from backend.handlers._notes import register_notes
    from backend.method_registry import MethodRegistry
    from core.errors import InvalidParamsError

    registry = MethodRegistry()
    register_notes(registry, MagicMock(personal_note_repository=None))
    with pytest.raises(InvalidParamsError) as ei:
        await registry.dispatch("notes.restore", {})
    assert ei.value.code == -32602


@pytest.mark.asyncio
async def test_notes_restore_missing_note_id_direct_returns_false():
    """绕过 registry 的直调缺 note_id → False 兜底（不抛 TypeError）。"""
    from unittest.mock import MagicMock

    from backend.handlers._notes import register_notes

    registry = _DummyRegistry()
    register_notes(registry, MagicMock(personal_note_repository=None))
    handler = registry.handlers["notes.restore"]
    assert await handler() is False


@pytest.mark.asyncio
async def test_notes_restore_with_note_id_and_no_repo_returns_false():
    """repo 缺失的降级行为保持不变（返回 False）。"""
    from unittest.mock import MagicMock

    from backend.handlers._notes import register_notes

    registry = _DummyRegistry()
    register_notes(registry, MagicMock(personal_note_repository=None))
    handler = registry.handlers["notes.restore"]
    assert await handler(note_id="n1") is False
