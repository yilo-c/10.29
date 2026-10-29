"""Sprint 2A — clipboard add_word backend integration tests.

验证：
  1. word.add 接受 source 参数并落到词条
  2. word.add 派发 vocabulary.word_added 事件
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

import pytest

from core.events import AppEvent, event_bus
from backend.handlers._wordbook import register_wordbook
from backend.method_registry import MethodRegistry


class _FakeRepo:
    def __init__(self) -> None:
        self._next_id = 100
        self.added: list[dict] = []

    def add_word(self, data: dict[str, Any]) -> int:
        self._next_id += 1
        record = dict(data)
        record["id"] = self._next_id
        self.added.append(record)
        return self._next_id

    def list_words(self):
        return list(self.added)

    def list_wordbooks(self, include_deleted: bool = False):
        return [{"id": 1, "name": "默认", "book_key": "k"}]


class _FakeContext:
    def __init__(self) -> None:
        self.vocabulary_repository = _FakeRepo()
        self.ai_client = None  # 触发 enrich early-return


@pytest.mark.asyncio
async def test_word_add_records_source_and_emits_event():
    captured: list[AppEvent] = []
    cb = lambda e: captured.append(e) if e.event_type == "vocabulary.word_added" else None
    event_bus.on("vocabulary.word_added", cb)
    try:
        ctx = _FakeContext()
        registry = MethodRegistry()
        register_wordbook(registry, ctx)

        result = await registry.dispatch("word.add", {
            "wordbook_id": 1,
            "word": "ephemeral",
            "meaning": "短暂的",
            "source": "clipboard",
        })
        assert result["success"] is True
        added = ctx.vocabulary_repository.added
        assert len(added) == 1
        assert added[0]["word"] == "ephemeral"
        assert added[0]["source"] == "clipboard"

        assert len(captured) == 1
        assert captured[0].payload["word"] == "ephemeral"
        assert captured[0].payload["source"] == "clipboard"
        assert captured[0].payload["wordbook_id"] == 1
    finally:
        event_bus.off("vocabulary.word_added", cb)


@pytest.mark.asyncio
async def test_word_add_without_source_omits_field():
    captured: list[AppEvent] = []
    cb = lambda e: captured.append(e) if e.event_type == "vocabulary.word_added" else None
    event_bus.on("vocabulary.word_added", cb)
    try:
        ctx = _FakeContext()
        registry = MethodRegistry()
        register_wordbook(registry, ctx)
        await registry.dispatch("word.add", {
            "wordbook_id": 1,
            "word": "foo",
            "meaning": "bar",
        })
        added = ctx.vocabulary_repository.added
        assert "source" not in added[0]
        assert captured[0].payload.get("source") == ""
    finally:
        event_bus.off("vocabulary.word_added", cb)


@pytest.mark.asyncio
async def test_word_add_truncates_long_source():
    ctx = _FakeContext()
    registry = MethodRegistry()
    register_wordbook(registry, ctx)
    long_src = "x" * 200
    await registry.dispatch("word.add", {
        "wordbook_id": 1,
        "word": "trunk",
        "meaning": "test",
        "source": long_src,
    })
    assert len(ctx.vocabulary_repository.added[0]["source"]) == 64
