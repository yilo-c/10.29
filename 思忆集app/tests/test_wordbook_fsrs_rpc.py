"""Sprint 3A — word.review_fsrs / vocab.rollback_fsrs RPC 测试。"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

import pytest

from backend.handlers._wordbook import register_wordbook
from backend.method_registry import MethodRegistry
from repositories._vocab import LocalVocabularyRepository


class _InMemoryDataStore:
    def __init__(self) -> None:
        self._data: dict[str, Any] = {}
        self._next = 0

    def load_json(self, key, default):
        if key in self._data:
            return self._data[key]
        return default if not isinstance(default, (list, dict)) else type(default)(default)

    def save_json(self, key, data) -> None:
        self._data[key] = data

    def next_id(self, items):
        existing = max((int(x.get("id") or 0) for x in items), default=0)
        self._next = max(self._next, existing) + 1
        return self._next


class _Ctx:
    def __init__(self) -> None:
        self.local_data_store = _InMemoryDataStore()
        self.vocabulary_repository = LocalVocabularyRepository(self.local_data_store)
        self.ai_client = None


@pytest.mark.asyncio
async def test_review_fsrs_rejects_invalid_rating():
    ctx = _Ctx()
    registry = MethodRegistry()
    register_wordbook(registry, ctx)
    out = await registry.dispatch("word.review_fsrs", {"word_id": 1, "rating": 0})
    assert out["success"] is False
    out = await registry.dispatch("word.review_fsrs", {"word_id": 1, "rating": 5})
    assert out["success"] is False


@pytest.mark.asyncio
async def test_review_fsrs_maps_ratings_to_quality():
    ctx = _Ctx()
    registry = MethodRegistry()
    register_wordbook(registry, ctx)
    wid = ctx.vocabulary_repository.add_word({"word": "hello", "wordbook_id": 1})

    # rating=4 (Easy) 应当让 reps 增加 1 次
    out = await registry.dispatch("word.review_fsrs", {"word_id": wid, "rating": 4})
    assert out["success"] is True
    assert out["repetitions"] == 1

    # rating=1 (Again) → reps 重置
    out = await registry.dispatch("word.review_fsrs", {"word_id": wid, "rating": 1})
    assert out["repetitions"] == 0


@pytest.mark.asyncio
async def test_rollback_fsrs_requires_confirm():
    ctx = _Ctx()
    registry = MethodRegistry()
    register_wordbook(registry, ctx)
    out = await registry.dispatch("vocab.rollback_fsrs", {})
    assert out["success"] is False


@pytest.mark.asyncio
async def test_rollback_fsrs_cleans_all_fsrs_fields():
    ctx = _Ctx()
    registry = MethodRegistry()
    register_wordbook(registry, ctx)

    # 创建并评分两个词，让它们有 FSRS 字段
    w1 = ctx.vocabulary_repository.add_word({"word": "a", "wordbook_id": 1})
    w2 = ctx.vocabulary_repository.add_word({"word": "b", "wordbook_id": 1})
    await registry.dispatch("word.review", {"word_id": w1, "quality": 4})
    await registry.dispatch("word.review", {"word_id": w2, "quality": 5})

    before = ctx.vocabulary_repository.list_words()
    assert all("fsrs_state" in w for w in before)

    out = await registry.dispatch("vocab.rollback_fsrs", {"confirm": True})
    assert out["success"] is True
    assert out["cleaned"] == 2

    after = ctx.vocabulary_repository.list_words()
    for w in after:
        assert "fsrs_state" not in w
        assert "fsrs_stability" not in w
        # SM-2 字段应保留
        assert "repetitions" in w
        assert "efactor" in w


@pytest.mark.asyncio
async def test_rollback_then_review_repopulates_fsrs():
    """回滚后再评分应重新写入 FSRS 字段（因为 fsrs 库还在用）。"""
    ctx = _Ctx()
    registry = MethodRegistry()
    register_wordbook(registry, ctx)
    wid = ctx.vocabulary_repository.add_word({"word": "x", "wordbook_id": 1})
    await registry.dispatch("word.review", {"word_id": wid, "quality": 4})
    await registry.dispatch("vocab.rollback_fsrs", {"confirm": True})
    after_rollback = next(w for w in ctx.vocabulary_repository.list_words() if w["id"] == wid)
    assert "fsrs_state" not in after_rollback

    await registry.dispatch("word.review", {"word_id": wid, "quality": 4})
    after_redo = next(w for w in ctx.vocabulary_repository.list_words() if w["id"] == wid)
    assert "fsrs_state" in after_redo
