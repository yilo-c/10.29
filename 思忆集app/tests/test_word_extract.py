"""Sprint 2B — translate.extract_words + reading.save_article 联动测试。"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Any

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

import pytest

from core.events import AppEvent, event_bus
from backend.handlers._word_extract import (
    register_word_extract,
    _candidate_words_from_text,
    STOPWORDS,
    MIN_WORD_LENGTH,
)
from backend.method_registry import MethodRegistry


# ── _candidate_words_from_text 单元测试 ──

def test_candidate_filters_short_and_stopwords():
    text = "The world is around us, having ephemeral substantial moments today."
    cands = _candidate_words_from_text(text, exclude=set())
    # ephemeral / substantial / moments / having ：have>=5 + non-stopword
    assert "ephemeral" in cands
    assert "substantial" in cands
    # stopword 必须排除
    for sw in {"around", "having"}:
        assert sw not in cands
    # 短词必须排除（the/world?）
    for short in {"the", "is", "us"}:
        assert short not in cands


def test_candidate_excludes_existing():
    text = "Pellucid pellucid pellucid ephemeral. ephemeral! ephemeral, redundant redundant."
    cands = _candidate_words_from_text(text, exclude={"pellucid"})
    assert "pellucid" not in cands
    assert "ephemeral" in cands
    assert "redundant" in cands


def test_candidate_orders_by_frequency_then_length():
    # ephemeral 出现 1 次（长度 9），rare 出现 3 次（长度 4，但 < MIN，排除）
    # 必须由频次排序，长度做 tiebreaker
    text = "ephemeral ephemeral substantial substantial substantial vibrant"
    cands = _candidate_words_from_text(text, exclude=set())
    # substantial(3次) 排第一；ephemeral(2次) 第二；vibrant(1次) 最后
    assert cands.index("substantial") < cands.index("ephemeral") < cands.index("vibrant")


# ── translate.extract_words RPC ──

class _FakeRepo:
    def __init__(self, words: list[dict]) -> None:
        self._words = words

    def list_words(self):
        return list(self._words)


class _FakeAi:
    """模拟 ai_client，根据 prompt 返回固定 JSON。"""
    def __init__(self, words_to_return: list[dict] | None = None, fail: bool = False) -> None:
        self._words = words_to_return
        self._fail = fail

    def chat_completion(self, **kwargs: Any):
        if self._fail:
            raise RuntimeError("mock AI failure")
        words = self._words if self._words is not None else [
            {"word": "ephemeral", "phonetic": "瑟藞fem蓹r蓹l", "meaning": "短暂的", "context": "Life is ephemeral."},
        ]
        import json
        return {"choices": [{"message": {"content": json.dumps({"words": words}, ensure_ascii=False)}}]}


class _FakeContext:
    def __init__(self, words=None, ai=None) -> None:
        self.vocabulary_repository = _FakeRepo(words or [])
        self.ai_client = ai


@pytest.mark.asyncio
async def test_extract_words_returns_ai_output():
    ai = _FakeAi([
        {"word": "ephemeral", "phonetic": "瑟藞fem蓹r蓹l", "meaning": "短暂的", "context": "..."},
        {"word": "substantial", "phonetic": "s蓹b藞st忙n蕛(蓹)l", "meaning": "大量的", "context": "..."},
    ])
    ctx = _FakeContext(ai=ai)
    registry = MethodRegistry()
    register_word_extract(registry, ctx)
    res = await registry.dispatch("translate.extract_words", {
        "text": "Life can be ephemeral, but with substantial planning we make moments last.",
    })
    assert len(res["words"]) == 2
    assert {w["word"] for w in res["words"]} == {"ephemeral", "substantial"}
    assert res["candidates_count"] >= 2


@pytest.mark.asyncio
async def test_extract_words_falls_back_when_ai_fails():
    ai = _FakeAi(fail=True)
    ctx = _FakeContext(ai=ai)
    registry = MethodRegistry()
    register_word_extract(registry, ctx)
    res = await registry.dispatch("translate.extract_words", {
        "text": "Ephemeral substantial perspective controversy demonstrates compelling phenomena.",
    })
    # AI fail → 降级返回前 5 个频次高的候选词（不带释义）
    assert len(res["words"]) >= 1
    assert all(w["meaning"] == "" for w in res["words"])


@pytest.mark.asyncio
async def test_extract_words_excludes_existing_words_in_lib():
    ai = _FakeAi([{"word": "tossed_after_filter", "phonetic": "", "meaning": "", "context": ""}])
    ctx = _FakeContext(
        words=[{"word": "Ephemeral", "meaning": "已收"}],  # 已收入词的应被排除
        ai=ai,
    )
    registry = MethodRegistry()
    register_word_extract(registry, ctx)
    res = await registry.dispatch("translate.extract_words", {
        "text": "ephemeral substantial perspective controversy",
    })
    # ephemeral 已在词库 → 候选不含；候选 ≥1 时 AI 才会被调用
    assert "ephemeral" not in [w["word"].lower() for w in res["words"]]


@pytest.mark.asyncio
async def test_extract_words_empty_text():
    ctx = _FakeContext(ai=_FakeAi())
    registry = MethodRegistry()
    register_word_extract(registry, ctx)
    res = await registry.dispatch("translate.extract_words", {"text": ""})
    assert res == {"words": [], "candidates_count": 0}


@pytest.mark.asyncio
async def test_propose_words_for_article_emits_event():
    ai = _FakeAi([
        {"word": "ephemeral", "phonetic": "瑟藞fem蓹r蓹l", "meaning": "短暂的", "context": "..."},
    ])
    ctx = _FakeContext(ai=ai)
    registry = MethodRegistry()
    register_word_extract(registry, ctx)

    captured: list[AppEvent] = []
    cb = lambda e: captured.append(e) if e.event_type == "reading.words_proposed" else None
    event_bus.on("reading.words_proposed", cb)
    try:
        await ctx._propose_words_for_article(
            article_id="abc123",
            title="Why life is ephemeral",
            summary="A reflection on the substantial moments that define us.",
        )
    finally:
        event_bus.off("reading.words_proposed", cb)

    assert len(captured) == 1
    assert captured[0].payload["article_id"] == "abc123"
    assert captured[0].payload["count"] >= 1
    assert any(w["word"] == "ephemeral" for w in captured[0].payload["words"])


@pytest.mark.asyncio
async def test_propose_words_silent_when_no_candidates():
    ctx = _FakeContext(ai=_FakeAi())
    registry = MethodRegistry()
    register_word_extract(registry, ctx)

    captured: list[AppEvent] = []
    cb = lambda e: captured.append(e) if e.event_type == "reading.words_proposed" else None
    event_bus.on("reading.words_proposed", cb)
    try:
        # 短文本只有 stopword 和短词
        await ctx._propose_words_for_article(article_id="x", title="The end is", summary="of us all")
    finally:
        event_bus.off("reading.words_proposed", cb)

    assert not captured
