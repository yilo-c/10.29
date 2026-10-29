# -*- coding: utf-8 -*-
"""Regression tests for memory.decision similarity scoring (P2-20).

Previously ``_compute_similarity`` relied on ``query.split()`` whitespace
tokenization plus pure-Jaccard which degraded badly on Chinese text. The
upgraded scorer combines token Jaccard (with CJK 单字切分) and character
bigram Jaccard, giving meaningful scores for both ASCII and CJK queries.
"""
from __future__ import annotations

import pytest

from memory.decision import (
    DecisionMemory,
    DecisionRecord,
    _char_bigrams,
    _jaccard,
    _tokenize,
)


def _record(decision_id: str, goal: str, context: str = "", tags: list[str] | None = None) -> DecisionRecord:
    return DecisionRecord(
        decision_id=decision_id,
        goal=goal,
        context=context,
        chosen_path="",
        alternatives=[],
        reasoning="",
        tags=list(tags or []),
    )


def test_tokenize_splits_cjk_into_single_chars() -> None:
    tokens = _tokenize("优化数据库 query")
    assert "优" in tokens
    assert "数" in tokens
    assert "query" in tokens
    # 整段中文不应作为单一 token 出现
    assert "优化数据库" not in tokens


def test_tokenize_handles_empty_and_punctuation() -> None:
    assert _tokenize("") == set()
    tokens = _tokenize("Hello,世界!")
    assert "hello" in tokens
    assert "世" in tokens
    assert "界" in tokens


def test_char_bigrams_basic() -> None:
    bg = _char_bigrams("数据库")
    assert "数据" in bg
    assert "据库" in bg
    assert len(bg) == 2


def test_char_bigrams_short_input() -> None:
    assert _char_bigrams("") == set()
    # 单字符 fallback
    assert _char_bigrams("数") == {"数"}


def test_jaccard_basic() -> None:
    assert _jaccard(set(), {"a"}) == 0.0
    assert _jaccard({"a", "b"}, {"a", "b"}) == 1.0
    assert _jaccard({"a", "b"}, {"b", "c"}) == pytest.approx(1 / 3)


def test_find_similar_matches_chinese_query() -> None:
    """中文查询应能命中相似的中文决策——旧实现这里会近乎 0 分。"""
    memory = DecisionMemory()
    memory.store(_record("d1", "如何优化数据库查询", tags=["db"]))
    memory.store(_record("d2", "前端首屏渲染优化方案"))
    memory.store(_record("d3", "如何选择缓存策略"))

    matches = memory.find_similar("数据库查询优化", top_k=3, min_similarity=0.1)
    assert matches, "应能匹配到中文相似决策"
    assert matches[0].record.decision_id == "d1"
    assert matches[0].similarity > 0.3


def test_find_similar_phrase_boost() -> None:
    """完整短语包含在 goal 中应触发 phrase_boost。"""
    memory = DecisionMemory()
    memory.store(_record("d1", "选择 Redis 还是 Memcached 做缓存"))
    memory.store(_record("d2", "如何写好单元测试", context="选择 Redis 的若干理由"))

    matches = memory.find_similar("选择 Redis", top_k=5, min_similarity=0.0)
    ids = [m.record.decision_id for m in matches]
    # goal 命中 > context 命中
    assert ids.index("d1") < ids.index("d2")


def test_find_similar_tag_boost() -> None:
    memory = DecisionMemory()
    memory.store(_record("d1", "无关内容", tags=["performance"]))
    memory.store(_record("d2", "完全不相关"))

    matches = memory.find_similar("performance", top_k=2, min_similarity=0.0)
    assert matches[0].record.decision_id == "d1"
    assert matches[0].similarity >= 0.15


def test_find_similar_respects_min_similarity() -> None:
    memory = DecisionMemory()
    memory.store(_record("d1", "完全无关的话题"))
    matches = memory.find_similar("数据库优化", top_k=5, min_similarity=0.9)
    assert matches == []


def test_find_similar_empty_memory() -> None:
    memory = DecisionMemory()
    assert memory.find_similar("anything") == []


def test_find_similar_empty_query() -> None:
    memory = DecisionMemory()
    memory.store(_record("d1", "x"))
    assert memory.find_similar("") == []


def test_find_similar_topk_limit() -> None:
    memory = DecisionMemory()
    for i in range(5):
        memory.store(_record(f"d{i}", "优化 数据库 性能"))
    matches = memory.find_similar("数据库 性能 优化", top_k=2, min_similarity=0.0)
    assert len(matches) == 2


def test_find_similar_ascii_still_works() -> None:
    """确保中英文混合改造没有破坏纯英文场景。"""
    memory = DecisionMemory()
    memory.store(_record("d1", "Optimize database query performance"))
    memory.store(_record("d2", "Frontend rendering tweaks"))
    matches = memory.find_similar("database optimize query", top_k=2, min_similarity=0.1)
    assert matches
    assert matches[0].record.decision_id == "d1"
