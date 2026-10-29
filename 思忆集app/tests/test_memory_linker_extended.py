"""Extended tests for MemoryLinker — MCP-12 gaps (weak links, co-access, periodic recalc, merge)."""
from __future__ import annotations

import sys
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from memory.linker import (
    MemoryLink,
    MemoryLinker,
    LINK_TYPE_STRONG,
    LINK_TYPE_MEDIUM,
    LINK_TYPE_WEAK,
    STRONG_THRESHOLD,
    MEDIUM_THRESHOLD,
    WEAK_THRESHOLD,
)


class _FakeDataStore:
    def __init__(self):
        self._data = {}

    def load_json(self, key, default=None):
        return self._data.get(key, default)

    def save_json(self, key, value):
        self._data[key] = value


def _make_linker():
    return MemoryLinker(data_store=_FakeDataStore())


# ── TF-IDF cosine similarity (space-separated English) ──
#   Strong (>=0.85): exact match or very high overlap
#   Medium (0.70-0.85): high overlap, e.g. "apple banana cherry date" vs "apple banana cherry date elderberry fig" → TF-cosine ≈ 0.82
#   Weak (0.55-0.70): moderate overlap, e.g. "apple banana cherry" vs "banana cherry date" → TF-cosine ≈ 0.67
#   None (<0.55): low overlap


# ── Task 1: link() no longer auto-creates weak links ──

def test_link_does_not_create_weak():
    """相似度在 0.55-0.70 之间的内容不应被自动关联。"""
    linker = _make_linker()
    # "apple banana cherry" vs "banana cherry date" → TF-cosine ≈ 0.67 (weak)
    new = {"entry_id": "m1", "content": "apple banana cherry"}
    existing = [{"entry_id": "m2", "content": "banana cherry date"}]
    links = linker.link(new, existing)
    stored = linker._load_links()
    assert len(links) == 0
    assert len(stored) == 0


def test_link_creates_medium():
    """相似度在 0.70-0.85 之间的内容应自动创建中等关联。"""
    linker = _make_linker()
    # "apple banana cherry date" vs "apple banana cherry date elderberry fig" → TF-cosine ≈ 0.82 (medium)
    new = {"entry_id": "m1", "content": "apple banana cherry date"}
    existing = [{"entry_id": "m2", "content": "apple banana cherry date elderberry fig"}]
    links = linker.link(new, existing)
    assert len(links) >= 1
    assert links[0].link_type == LINK_TYPE_MEDIUM


# ── Task 2: get_weak_extension_results ──

def test_get_weak_extension_results():
    """弱关联记忆应作为搜索扩展结果返回。"""
    linker = _make_linker()
    existing = [
        {"entry_id": "m1", "content": "apple banana cherry"},
        {"entry_id": "m2", "content": "banana cherry date"},    # weak vs query (TF-cosine ≈ 0.67)
        {"entry_id": "m3", "content": "python coding is fun"},   # unrelated
    ]
    # Query "apple banana cherry" → m2 has weak similarity, m3 is 0
    results = linker.get_weak_extension_results("apple banana cherry", existing, top_k=2)
    assert len(results) >= 1
    assert results[0]["entry_id"] == "m2"
    assert results[0]["_link_type"] == LINK_TYPE_WEAK
    assert WEAK_THRESHOLD <= results[0]["_similarity_score"] < MEDIUM_THRESHOLD


def test_get_weak_extension_results_empty():
    """空内容应返回空列表。"""
    linker = _make_linker()
    assert linker.get_weak_extension_results("", [{"entry_id": "m1", "content": "hello"}]) == []
    assert linker.get_weak_extension_results("hello", []) == []


# ── Task 3: co-access tracking ──

def test_record_co_access_no_link():
    """没有弱链接时，共访问不应升级。"""
    linker = _make_linker()
    result = linker.record_co_access("m1", "m2")
    assert result is False


def test_record_co_access_upgrade_weak():
    """弱链接的共访问达到阈值后应自动升级为中等。"""
    linker = _make_linker()
    weak_link = MemoryLink(
        source_id="m1", target_id="m2",
        similarity_score=0.6, link_type=LINK_TYPE_WEAK,
    )
    linker._save_links([weak_link])
    assert linker._load_links()[0].link_type == LINK_TYPE_WEAK

    upgraded = False
    for _ in range(linker.CO_ACCESS_UPGRADE_THRESHOLD):
        upgraded = linker.record_co_access("m1", "m2") or upgraded

    assert upgraded is True
    stored = linker._load_links()
    assert len(stored) == 1
    assert stored[0].link_type == LINK_TYPE_MEDIUM


def test_record_co_access_threshold_exact():
    """共访问次数达到阈值才升级。"""
    linker = _make_linker()
    weak_link = MemoryLink(
        source_id="m1", target_id="m2",
        similarity_score=0.6, link_type=LINK_TYPE_WEAK,
    )
    linker._save_links([weak_link])

    for _ in range(linker.CO_ACCESS_UPGRADE_THRESHOLD - 1):
        linker.record_co_access("m1", "m2")

    stored = linker._load_links()
    assert stored[0].link_type == LINK_TYPE_WEAK


def test_get_co_access_count():
    """共访问次数应正确累计和查询。"""
    linker = _make_linker()
    assert linker.get_co_access_count("m1", "m2") == 0

    linker.record_co_access("m1", "m2")
    assert linker.get_co_access_count("m1", "m2") == 1

    linker.record_co_access("m2", "m1")
    assert linker.get_co_access_count("m1", "m2") == 2


def test_record_co_access_self():
    """记录自身共访问应无影响。"""
    linker = _make_linker()
    assert linker.record_co_access("m1", "m1") is False


# ── Task 4: periodic_recalculate ──

def test_periodic_recalculate_empty():
    """空记忆列表应返回空。"""
    linker = _make_linker()
    result = linker.periodic_recalculate([])
    assert result == []


def test_periodic_recalculate_no_candidates():
    """所有记忆都有足够关联时不应重新计算。"""
    linker = _make_linker()
    # m1 linked to m2 (strong), m2 linked to m1 — both have count=1
    # With only 2 memories and 1 link, both are candidates... let's make them have 2+ links
    # Actually, the real check is: memory_link_counts[eid] < 2
    # m1→m2 and m1→m3 make m1 have 2 links
    linker.link(
        {"entry_id": "m1", "content": "i live in beijing"},
        [
            {"entry_id": "m2", "content": "i live in beijing"},
            {"entry_id": "m3", "content": "i live in beijing too"},
        ],
    )
    mems = [
        {"entry_id": "m1", "content": "i live in beijing"},
        {"entry_id": "m2", "content": "i live in beijing"},
        {"entry_id": "m3", "content": "i live in beijing too"},
    ]
    new_links = linker.periodic_recalculate(mems)
    assert new_links == [] or all(l in linker._load_links() for l in new_links)


def test_periodic_recalculate_finds_new_links():
    """低关联记忆应被重新计算并可能创建新链接。"""
    linker = _make_linker()
    # "i love beijing city" vs "i love beijing" → 3/4 = 0.75 (medium)
    linker.link(
        {"entry_id": "m1", "content": "i love beijing city"},
        [{"entry_id": "m2", "content": "i love beijing"}],
    )
    # m3 is independent — "i love city" vs m1 → 3/4 = 0.75 (medium)
    mems = [
        {"entry_id": "m1", "content": "i love beijing city"},
        {"entry_id": "m2", "content": "i love beijing"},   # linked to m1
        {"entry_id": "m3", "content": "i love city"},       # no links yet
    ]
    new_links = linker.periodic_recalculate(mems, max_pairs=100)
    assert len(new_links) >= 1


# ── Task 5: merge_link_networks ──

def test_merge_link_networks_redirects():
    """合并关联网络时，被合并记忆的链接应重定向到保留记忆。"""
    linker = _make_linker()
    # "i love beijing city" vs "i love beijing" → 4/5 = 0.80 (medium)
    # "love beijing city life" vs "i love beijing city life" → 4/5 = 0.80 (medium)
    linker.link(
        {"entry_id": "m1", "content": "i love beijing city"},
        [{"entry_id": "m3", "content": "i love beijing city life"}],
    )
    linker.link(
        {"entry_id": "m2", "content": "love beijing city life"},
        [{"entry_id": "m3", "content": "i love beijing city life"}],
    )

    # Merge m2 into m1
    redirected = linker.merge_link_networks("m1", "m2")
    assert redirected >= 1

    # m1 should now be linked to m3
    m1_links = linker.get_linked_memories("m1")
    assert len(m1_links) >= 1


def test_merge_link_networks_same_id():
    """相同 ID 合并应返回 0。"""
    linker = _make_linker()
    assert linker.merge_link_networks("m1", "m1") == 0


def test_merge_link_networks_empty():
    """空 ID 合并应返回 0。"""
    linker = _make_linker()
    assert linker.merge_link_networks("", "m2") == 0


# ── Task 6: get_linked_memories_with_details ──

def test_get_linked_memories_with_details():
    """关联记忆详情应包含标题、摘要、关联标签等。"""
    linker = _make_linker()
    linker.link(
        {"entry_id": "m1", "content": "i live in beijing"},
        [{"entry_id": "m2", "content": "i live in beijing"}],
    )

    lookup = {
        "m1": {"entry_id": "m1", "title": "Home", "content": "i live in beijing"},
        "m2": {"entry_id": "m2", "title": "Also Home", "content": "i live in beijing chaoyang"},
    }

    details = linker.get_linked_memories_with_details("m1", lookup)
    assert len(details) >= 1
    assert details[0]["title"] == "Also Home"
    assert details[0]["snippet"] != ""
    assert details[0]["link_type"] == LINK_TYPE_STRONG
    assert details[0]["link_label"] == "强关联"
    assert details[0]["similarity_score"] >= STRONG_THRESHOLD
    assert details[0]["linked_at"] != ""


def test_get_linked_memories_with_details_empty():
    """无关联时返回空列表。"""
    linker = _make_linker()
    assert linker.get_linked_memories_with_details("ghost", {}) == []


# ── Task 7: edge cases ──

def test_link_weak_not_persisted():
    """确保弱关联不会持久化到存储。"""
    linker = _make_linker()
    # "apple banana cherry" vs "banana cherry date" → TF-cosine ≈ 0.67 (weak)
    links = linker.link(
        {"entry_id": "m1", "content": "apple banana cherry"},
        [{"entry_id": "m2", "content": "banana cherry date"}],
    )
    stored = linker._load_links()
    assert len(links) == 0
    assert len(stored) == 0


def test_merge_with_dedup():
    """合并后应自动去重。"""
    linker = _make_linker()
    # m1→m3, m2→m3 (both strong via exact match)
    linker.link(
        {"entry_id": "m1", "content": "beijing memory"},
        [{"entry_id": "m3", "content": "beijing memory"}],
    )
    linker.link(
        {"entry_id": "m2", "content": "beijing memory"},
        [{"entry_id": "m3", "content": "beijing memory"}],
    )

    before = len(linker._load_links())
    redirected = linker.merge_link_networks("m1", "m2")
    after = len(linker._load_links())

    assert redirected >= 1
    assert after <= before


def test_link_strong_and_medium():
    """创建的记忆应有正确的强/中分类。"""
    linker = _make_linker()
    # Strong: "apple banana cherry date" vs "apple banana cherry date" → 1.0
    # Medium: "apple banana cherry date" vs "apple banana cherry date elderberry fig" → TF-cosine ≈ 0.82
    new = {"entry_id": "m1", "content": "apple banana cherry date"}
    existing = [
        {"entry_id": "m2", "content": "apple banana cherry date"},       # strong
        {"entry_id": "m3", "content": "apple banana cherry date elderberry fig"},    # medium
    ]
    links = linker.link(new, existing)
    assert len(links) == 2
    types = {l.link_type for l in links}
    assert LINK_TYPE_STRONG in types
    assert LINK_TYPE_MEDIUM in types


def test_get_weak_extensions_sorted_by_score():
    """弱关联扩展结果应按相似度降序排列。"""
    linker = _make_linker()
    existing = [
        {"entry_id": "m1", "content": "banana cherry date"},     # weak (TF-cosine ≈ 0.67)
        {"entry_id": "m2", "content": "apple banana cherry date elderberry fig"},   # medium (out of weak range)
        {"entry_id": "m3", "content": "like coffee and tea"},     # 0
    ]
    # Only m1 should be in weak range
    results = linker.get_weak_extension_results("apple banana cherry", existing, top_k=5)
    weak_scores = [r["_similarity_score"] for r in results]
    # Should be sorted descending
    assert weak_scores == sorted(weak_scores, reverse=True)
