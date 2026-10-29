"""Tests for MemoryLinker — 自动记忆关联 (MCP-12)."""
from __future__ import annotations

import sys
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from memory.linker import (
    MemoryLink,
    MergeSuggestion,
    MemoryLinker,
    _cosine_similarity,
    _jaccard_similarity,
    STRONG_THRESHOLD,
    MEDIUM_THRESHOLD,
    WEAK_THRESHOLD,
)


class _FakeDataStore:
    """In-memory data store for testing."""
    def __init__(self):
        self._data = {}

    def load_json(self, key, default=None):
        return self._data.get(key, default)

    def save_json(self, key, value):
        self._data[key] = value


def _make_linker():
    return MemoryLinker(data_store=_FakeDataStore())


# ── MemoryLink data class ──

def test_memory_link_defaults():
    link = MemoryLink(source_id="mem1", target_id="mem2", similarity_score=0.8)
    assert link.link_type == "medium"
    assert link.link_id != ""
    assert link.created_at != ""
    assert link.source_id == "mem1"
    assert link.target_id == "mem2"


def test_memory_link_roundtrip():
    link = MemoryLink(
        source_id="a", target_id="b", similarity_score=0.92,
        link_type="strong",
    )
    d = link.to_dict()
    restored = MemoryLink.from_dict(d)
    assert restored.source_id == "a"
    assert restored.target_id == "b"
    assert restored.similarity_score == 0.92
    assert restored.link_type == "strong"
    assert restored.link_id == link.link_id
    assert restored.created_at == link.created_at


# ── _jaccard_similarity ──

def test_jaccard_identical():
    assert _jaccard_similarity("我住在北京", "我住在北京") == 1.0


def test_jaccard_partial():
    sim = _jaccard_similarity("我住在北京", "我住在深圳")
    assert 0.0 < sim < 1.0


def test_jaccard_empty():
    assert _jaccard_similarity("", "hello") == 0.0
    assert _jaccard_similarity("hello", "") == 0.0
    assert _jaccard_similarity("", "") == 0.0


# ── _cosine_similarity ──

def test_cosine_identical():
    assert _cosine_similarity([1.0, 0.0], [1.0, 0.0]) == 1.0


def test_cosine_orthogonal():
    assert _cosine_similarity([1.0, 0.0], [0.0, 1.0]) == 0.0


def test_cosine_partial():
    sim = _cosine_similarity([1.0, 1.0], [1.0, 0.0])
    assert 0.0 < sim < 1.0


def test_cosine_empty():
    assert _cosine_similarity([], [1.0]) == 0.0
    assert _cosine_similarity([1.0], []) == 0.0


def test_cosine_mismatched_dims():
    assert _cosine_similarity([1.0, 0.0], [1.0]) == 0.0


# ── MemoryLinker.link() ──

def test_link_empty_memories():
    linker = _make_linker()
    entry = {"entry_id": "m1", "content": "我住在北京"}
    links = linker.link(entry, [])
    assert links == []


def test_link_no_match():
    linker = _make_linker()
    entry = {"entry_id": "m1", "content": "我住在北京"}
    existing = [{"entry_id": "m2", "content": "今天天气真好"}]
    links = linker.link(entry, existing)
    # Jaccard similarity of these two unrelated Chinese sentences should be low
    all_links = linker._load_links()
    assert len(all_links) == 0  # no links created since below threshold


def test_link_exact_match_strong():
    linker = _make_linker()
    entry = {"entry_id": "m1", "content": "我住在北京"}
    existing = [{"entry_id": "m2", "content": "我住在北京"}]
    links = linker.link(entry, existing)
    assert len(links) >= 1
    assert links[0].similarity_score >= STRONG_THRESHOLD
    assert links[0].link_type == "strong"


def test_link_self_excluded():
    linker = _make_linker()
    entry = {"entry_id": "m1", "content": "hello"}
    existing = [{"entry_id": "m1", "content": "hello"}]
    links = linker.link(entry, existing)
    assert links == []


def test_link_duplicates_not_recreated():
    linker = _make_linker()
    entry = {"entry_id": "m1", "content": "我住在北京"}
    existing = [{"entry_id": "m2", "content": "我住在北京"}]
    linker.link(entry, existing)
    # Second attempt should not create duplicate links
    links2 = linker.link(entry, existing)
    assert len(links2) == 0


def test_link_persists_across_calls():
    linker = _make_linker()
    entry = {"entry_id": "m1", "content": "我住在北京"}
    existing = [{"entry_id": "m2", "content": "我住在北京"}]
    linker.link(entry, existing)
    stored = linker._load_links()
    assert len(stored) == 1


# ── find_duplicates ──

def test_find_duplicates():
    linker = _make_linker()
    existing = [
        {"entry_id": "m1", "content": "我住在北京朝阳区"},
        {"entry_id": "m2", "content": "今天天气真好"},
    ]
    dup = linker.find_duplicates("我住在北京朝阳区", existing)
    assert len(dup) == 1
    assert dup[0]["entry_id"] == "m1"


def test_find_duplicates_empty():
    linker = _make_linker()
    assert linker.find_duplicates("", []) == []


# ── get_linked_memories ──

def test_get_linked_memories():
    linker = _make_linker()
    linker.link({"entry_id": "m1", "content": "我住在北京"},
                [{"entry_id": "m2", "content": "我住在北京"}])
    linked = linker.get_linked_memories("m1")
    assert len(linked) >= 1


def test_get_linked_memories_nonexistent():
    linker = _make_linker()
    assert linker.get_linked_memories("nonexistent") == []


# ── get_backlinks ──

def test_backlinks():
    linker = _make_linker()
    # Create a link from m1 to m2
    existing = [{"entry_id": "m2", "content": "我住在北京"}]
    linker.link({"entry_id": "m1", "content": "我住在北京"}, existing)
    # m2 should have backlinks from m1
    backlinks = linker.get_backlinks("m2")
    assert len(backlinks) >= 1
    assert backlinks[0]["source_id"] == "m1"


def test_backlinks_nonexistent():
    linker = _make_linker()
    assert linker.get_backlinks("ghost") == []


# ── get_backlink_count ──

def test_backlink_count():
    linker = _make_linker()
    linker.link({"entry_id": "m1", "content": "我住在北京"},
                [{"entry_id": "m2", "content": "我住在北京"}])
    assert linker.get_backlink_count("m2") >= 1
    assert linker.get_backlink_count("ghost") == 0


# ── suggest_merge ──

def test_suggest_merge():
    linker = _make_linker()
    a = {"content": "我住在北京"}
    b = {"content": "我住在北京"}
    suggestion = linker.suggest_merge(a, b)
    assert suggestion.similarity_score >= 0.85
    assert suggestion.suggested_action in ("merge", "keep_both")


def test_suggest_merge_different():
    linker = _make_linker()
    a = {"content": "我住在北京"}
    b = {"content": "今天天气真好"}
    suggestion = linker.suggest_merge(a, b)
    assert suggestion.similarity_score < 0.85


# ── cleanup_on_delete ──

def test_cleanup_on_delete():
    linker = _make_linker()
    linker.link({"entry_id": "m1", "content": "我住在北京"},
                [{"entry_id": "m2", "content": "我住在北京"}])
    assert len(linker._load_links()) >= 1
    linker.cleanup_on_delete("m1")
    assert len(linker._load_links()) == 0


def test_cleanup_on_delete_nonexistent():
    linker = _make_linker()
    assert linker.cleanup_on_delete("ghost") == 0


# ── reindex_memory ──

def test_reindex_memory():
    linker = _make_linker()
    # Create initial links
    linker.link({"entry_id": "m1", "content": "我住在北京"},
                [{"entry_id": "m2", "content": "我住在北京"}])
    assert len(linker._load_links()) >= 1
    # Reindex with different content
    new_links = linker.reindex_memory("m1", "今天天气真好",
                                       [{"entry_id": "m2", "content": "我住在北京"}])
    # m1's content changed; old link to m2 should be gone, no new link created
    stored = linker._load_links()
    # Old links should be cleaned up; new links may or may not exist
    assert all(l.source_id != "m1" or l.target_id != "m2" for l in stored) or not stored


# ── get_link_stats ──

def test_get_link_stats_empty():
    linker = _make_linker()
    stats = linker.get_link_stats()
    assert stats["total_links"] == 0
    assert stats["coverage_rate"] == 0.0


def test_get_link_stats_with_links():
    linker = _make_linker()
    linker.link({"entry_id": "m1", "content": "我住在北京"},
                [{"entry_id": "m2", "content": "我住在北京"},
                 {"entry_id": "m3", "content": "北京天气很好"}])
    stats = linker.get_link_stats(total_memories=3)
    assert stats["total_links"] >= 1
    assert stats["coverage_rate"] > 0


# ── upgrade_weak_links ──

def test_upgrade_weak_links():
    linker = _make_linker()
    # Manually create a weak link
    link = MemoryLink(source_id="m1", target_id="m2",
                      similarity_score=0.6, link_type="weak")
    linker._save_links([link])
    assert linker._load_links()[0].link_type == "weak"
    linker.upgrade_weak_links(["m1", "m2"], [])
    assert linker._load_links()[0].link_type == "medium"


def test_upgrade_weak_links_single():
    linker = _make_linker()
    assert linker.upgrade_weak_links(["m1"], []) == 0


# ── _classify_link ──

def test_classify_strong():
    linker = _make_linker()
    assert linker._classify_link(0.90) == "strong"
    assert linker._classify_link(0.85) == "strong"


def test_classify_medium():
    linker = _make_linker()
    assert linker._classify_link(0.75) == "medium"
    assert linker._classify_link(0.70) == "medium"


def test_classify_weak():
    linker = _make_linker()
    assert linker._classify_link(0.60) == "weak"
    assert linker._classify_link(0.55) == "weak"
    assert linker._classify_link(0.54) == "weak"


# ── MergeSuggestion ──

def test_merge_suggestion_defaults():
    ms = MergeSuggestion(memory_a={}, memory_b={}, similarity_score=0.9)
    assert ms.differences == []
    assert ms.suggested_action == ""
    d = ms.to_dict()
    assert d["similarity_score"] == 0.9
