"""Sprint 3B — word.dictation_check RPC 单元测试。"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

import pytest


# ── 纯函数层 ──────────────────────────────────────────────────

from backend.handlers._dictation import _normalize, diff_chars, _suggest_score


class TestNormalize:
    def test_lower_and_trim(self):
        assert _normalize("  Hello  ") == "hello"

    def test_multiple_spaces_collapsed(self):
        assert _normalize("a   b  c") == "a b c"

    def test_empty(self):
        assert _normalize("") == ""

    def test_punctuation_preserved(self):
        # punctuation stays, only spaces and case are normalized
        assert _normalize("It's") == "it's"


class TestDiffChars:
    def test_identical(self):
        result = diff_chars("hello", "hello")
        assert all(d["op"] == "=" for d in result)
        assert len(result) == 5

    def test_substitution(self):
        result = diff_chars("cat", "bat")
        ops = [d["op"] for d in result]
        # c→b is a replace: "-" for c, "+" for b, then "at" matches
        assert "-" in ops
        assert "+" in ops

    def test_insertion(self):
        result = diff_chars("cat", "cart")
        ops = [d["op"] for d in result]
        assert "+" in ops  # user typed extra 'r'

    def test_deletion(self):
        result = diff_chars("cart", "cat")
        ops = [d["op"] for d in result]
        assert "-" in ops  # user missed 'r'

    def test_empty_actual(self):
        result = diff_chars("hello", "")
        assert all(d["op"] == "-" for d in result)

    def test_empty_expected(self):
        result = diff_chars("", "hello")
        assert all(d["op"] == "+" for d in result)


class TestSuggestScore:
    def test_exact_match(self):
        q, r = _suggest_score("hello", "hello", exact=True, correct=True)
        assert q == 5 and r == 4  # Easy

    def test_correct_case_insensitive(self):
        q, r = _suggest_score("Hello", "hello", exact=False, correct=True)
        assert q == 4 and r == 3  # Good

    def test_near_miss(self):
        # 1 char off → ratio ~0.8 → Hard
        q, r = _suggest_score("hello", "helo", exact=False, correct=False)
        assert q == 3 and r == 2  # Hard

    def test_completely_wrong(self):
        q, r = _suggest_score("ephemeral", "banana", exact=False, correct=False)
        assert q == 1 and r == 1  # Again


# ── RPC 层 ────────────────────────────────────────────────────

class _FakeRepo:
    """Minimal vocabulary_repository stub for RPC tests."""

    def __init__(self, words: list[dict[str, Any]] | None = None):
        self._words = words or []

    def list_words(self) -> list[dict[str, Any]]:
        return list(self._words)

    def record_review(self, word_id: int, quality: int):
        # return a plausible 4-tuple
        return (1, 2.5, 1, "2026-07-01T00:00:00+00:00")


class _FakeContext:
    def __init__(self, repo: _FakeRepo | None = None):
        self.vocabulary_repository = repo


class _FakeRegistry:
    def __init__(self):
        self.registered: dict[str, Any] = {}

    def register(self, name: str, fn):
        self.registered[name] = fn


@pytest.fixture
def registry():
    return _FakeRegistry()


@pytest.fixture
def ctx_with_repo():
    words = [
        {"id": 1, "word": "ephemeral", "meaning": "短暂的"},
        {"id": 2, "word": "ubiquitous", "meaning": "无处不在的"},
    ]
    return _FakeContext(repo=_FakeRepo(words))


def _register(ctx):
    from backend.handlers._dictation import register_dictation
    reg = _FakeRegistry()
    register_dictation(reg, ctx)
    return reg


@pytest.mark.asyncio
async def test_correct_dictation(ctx_with_repo):
    reg = _register(ctx_with_repo)
    fn = reg.registered["word.dictation_check"]
    result = await fn(word_id=1, actual="ephemeral")
    assert result["correct"] is True
    assert result["exact"] is True
    assert result["suggested_quality"] == 5
    assert result["suggested_rating"] == 4


@pytest.mark.asyncio
async def test_case_insensitive_correct(ctx_with_repo):
    reg = _register(ctx_with_repo)
    fn = reg.registered["word.dictation_check"]
    result = await fn(word_id=1, actual="Ephemeral")
    assert result["correct"] is True
    assert result["exact"] is False
    assert result["suggested_quality"] == 4


@pytest.mark.asyncio
async def test_wrong_answer(ctx_with_repo):
    reg = _register(ctx_with_repo)
    fn = reg.registered["word.dictation_check"]
    result = await fn(word_id=1, actual="eternal")
    assert result["correct"] is False
    assert result["suggested_quality"] == 1
    assert len(result["diff"]) > 0


@pytest.mark.asyncio
async def test_explicit_expected():
    ctx = _FakeContext(repo=None)
    reg = _register(ctx)
    fn = reg.registered["word.dictation_check"]
    result = await fn(expected="serendipity", actual="serendipity")
    assert result["correct"] is True


@pytest.mark.asyncio
async def test_auto_record(ctx_with_repo):
    reg = _register(ctx_with_repo)
    fn = reg.registered["word.dictation_check"]
    result = await fn(word_id=1, actual="ephemeral", auto_record=True)
    assert result["correct"] is True
    assert "review" in result
    assert result["review"]["repetitions"] == 1


@pytest.mark.asyncio
async def test_no_expected_no_word_id():
    ctx = _FakeContext(repo=None)
    reg = _register(ctx)
    fn = reg.registered["word.dictation_check"]
    result = await fn(actual="anything")
    assert "error" in result


@pytest.mark.asyncio
async def test_event_emitted(ctx_with_repo):
    from core.events import event_bus, AppEvent

    captured: list[AppEvent] = []

    def _cap(ev: AppEvent):
        if ev.event_type == "vocabulary.dictation.checked":
            captured.append(ev)

    event_bus.on("vocabulary.dictation.checked", _cap)
    try:
        reg = _register(ctx_with_repo)
        fn = reg.registered["word.dictation_check"]
        await fn(word_id=2, actual="wrong")
        assert len(captured) == 1
        assert captured[0].payload["correct"] is False
    finally:
        # EventBus doesn't expose unsubscribe; handlers are lightweight, no leak concern in test
        pass
