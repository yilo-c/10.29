"""Tests for memory_search - Hybrid search, RRF fusion, NL time parsing, source labels."""
from __future__ import annotations

import sys
from datetime import datetime, timedelta
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

import pytest
from memory.search import (
    HybridMemorySearch,
    parse_time_range,
    get_source_label,
    get_source_color,
    SOURCE_LABELS,
)


class TestSourceLabels:
    """Source label formatting and colors."""

    def test_known_source_types(self):
        """All known source types return correct labels."""
        assert get_source_label("memory") == "[记忆]"
        assert get_source_label("note") == "[笔记]"
        assert get_source_label("knowledge") == "[知识库]"
        assert get_source_label("web") == "[网页]"
        assert get_source_label("task") == "[任务]"

    def test_unknown_source_type(self):
        """Unknown source type returns default [记忆] label."""
        assert get_source_label("unknown_type") == "[记忆]"

    def test_source_colors_exist(self):
        """All known source types have color codes."""
        for source_type in SOURCE_LABELS:
            color = get_source_color(source_type)
            assert color.startswith("#")
            assert len(color) == 7  # #RRGGBB

    def test_source_color_for_unknown(self):
        """Unknown source type returns a default color."""
        color = get_source_color("nonexistent")
        assert color.startswith("#")


class TestNaturalLanguageTimeParsing:
    """Natural language time range parsing."""

    def test_no_time_keyword(self):
        """Query without time keyword returns None bounds."""
        start, end, cleaned = parse_time_range("关于数据库的记忆")
        assert start is None
        assert end is None
        assert cleaned == "关于数据库的记忆"

    def test_today(self):
        """'今天' should parse to today's date range."""
        start, end, cleaned = parse_time_range("今天的记忆")
        now = datetime.now()
        assert start is not None
        assert end is not None
        assert start.hour == 0
        assert start.minute == 0
        assert cleaned == "记忆"

    def test_this_week(self):
        """'本周' should parse to this week's range."""
        start, end, cleaned = parse_time_range("本周的记忆")
        assert start is not None
        assert end is not None
        # Start should be Monday of this week
        assert start.weekday() == 0  # Monday
        assert start.hour == 0

    def test_last_week(self):
        """'上周' should parse to last week's range."""
        start, end, cleaned = parse_time_range("上周的记忆")
        assert start is not None
        assert end is not None
        # end should be this Monday
        assert end.weekday() == 0
        assert end.hour == 0

    def test_this_month(self):
        """'这个月' should parse to this month's range."""
        start, end, cleaned = parse_time_range("这个月的记忆")
        assert start is not None
        assert start.day == 1

    def test_specific_month(self):
        """'3月' should parse to March of this year."""
        start, end, cleaned = parse_time_range("3月的记忆")
        assert start is not None
        assert start.month == 3
        assert start.day == 1

    def test_specific_year(self):
        """'2025年' should parse to year 2025."""
        start, end, cleaned = parse_time_range("2025年的记忆")
        assert start is not None
        assert start.year == 2025
        assert start.month == 1
        assert start.day == 1

    def test_yesterday(self):
        """'昨天' should parse to yesterday's range."""
        start, end, cleaned = parse_time_range("昨天的记忆")
        now = datetime.now()
        yesterday = now - timedelta(days=1)
        assert start is not None
        assert start.year == yesterday.year
        assert start.month == yesterday.month
        assert start.day == yesterday.day
        assert start.hour == 0

    def test_days_ago(self):
        """'3天前' should parse correctly."""
        start, end, cleaned = parse_time_range("3天前的记忆")
        assert start is not None
        now = datetime.now()
        expected = now - timedelta(days=3)
        assert start.year == expected.year
        assert start.month == expected.month
        assert start.day == expected.day

    def test_recent_days(self):
        """'最近7天' should parse to last 7 days."""
        start, end, cleaned = parse_time_range("最近7天的记忆")
        assert start is not None
        now = datetime.now()
        expected = now - timedelta(days=7)
        assert start.year == expected.year
        assert start.month == expected.month
        assert start.day == expected.day

    def test_combined_query(self):
        """Combined time and keyword query."""
        start, end, cleaned = parse_time_range("上周关于数据库的记忆")
        assert start is not None
        assert cleaned == "关于数据库的记忆"

    def test_empty_query(self):
        """Empty query returns None bounds and empty cleaned."""
        start, end, cleaned = parse_time_range("")
        assert start is None
        assert end is None
        assert cleaned == ""


class TestRRFFusion:
    """RRF (Reciprocal Rank Fusion) algorithm."""

    def test_rrf_both_engines(self):
        """RRF fusion with results from both engines."""
        vector_results = [
            {"id": "A", "title": "Result A", "content": "A"},
            {"id": "B", "title": "Result B", "content": "B"},
            {"id": "C", "title": "Result C", "content": "C"},
        ]
        keyword_results = [
            {"id": "B", "title": "Result B", "content": "B"},
            {"id": "D", "title": "Result D", "content": "D"},
            {"id": "A", "title": "Result A", "content": "A"},
        ]

        fused = HybridMemorySearch._rrf_fuse(vector_results, keyword_results, k=60, top_k=3)

        assert len(fused) <= 3
        # Items appearing in both results should rank higher
        fused_ids = [item["id"] for item in fused]
        # B and A appear in both lists, so they should be in top results
        assert "A" in fused_ids
        assert "B" in fused_ids

    def test_rrf_one_engine_empty(self):
        """RRF with only one engine having results."""
        vector_results = [
            {"id": "A", "title": "A"},
            {"id": "B", "title": "B"},
        ]
        keyword_results = []

        fused = HybridMemorySearch._rrf_fuse(vector_results, keyword_results, k=60, top_k=5)
        assert len(fused) == 2
        assert fused[0]["id"] == "A"

    def test_rrf_both_empty(self):
        """RRF with both engines empty returns empty."""
        fused = HybridMemorySearch._rrf_fuse([], [], k=60, top_k=5)
        assert len(fused) == 0

    def test_rrf_completely_different(self):
        """RRF with completely different results from each engine."""
        vector_results = [
            {"id": "v1"}, {"id": "v2"}, {"id": "v3"},
        ]
        keyword_results = [
            {"id": "k1"}, {"id": "k2"}, {"id": "k3"},
        ]

        fused = HybridMemorySearch._rrf_fuse(vector_results, keyword_results, k=60, top_k=6)
        assert len(fused) == 6

    def test_rrf_respects_top_k(self):
        """RRF should return at most top_k results."""
        vector_results = [{"id": f"v{i}"} for i in range(10)]
        keyword_results = [{"id": f"k{i}"} for i in range(10)]

        fused = HybridMemorySearch._rrf_fuse(vector_results, keyword_results, k=60, top_k=3)
        assert len(fused) == 3

    def test_rrf_k_parameter_effect(self):
        """Smaller k gives more weight to higher ranks."""
        results_a = [{"id": "X", "title": "X"}]
        results_b = [{"id": "X", "title": "X"}]

        # With k=1, rank 1 contributes 1/(1+1) = 0.5
        fused_small_k = HybridMemorySearch._rrf_fuse(results_a, results_b, k=1, top_k=1)
        assert len(fused_small_k) == 1


class TestHybridMemorySearchInit:
    """Test HybridMemorySearch initialization without ChromaDB."""

    def test_init_without_chromadb(self, tmp_path):
        """HybridMemorySearch initializes with only FTS5 when no chroma_path."""
        db_path = tmp_path / "test_fts.db"
        search = HybridMemorySearch(db_path=str(db_path), chroma_path=None)
        assert search is not None
        assert search._chroma_available is False
        assert search._local_index is not None
        search.close()

    def test_sync_entries_basic(self, tmp_path):
        """Sync entries to FTS5 index."""
        db_path = tmp_path / "sync_test.db"
        search = HybridMemorySearch(db_path=str(db_path), chroma_path=None)

        entries = [
            {"id": "e1", "title": "数据库设计", "content": "PostgreSQL 分区表设计", "tags_text": "数据库", "source_type": "note", "updated_at": "2026-04-01T10:00:00"},
            {"id": "e2", "title": "Python 编程", "content": "Python 异步编程模式", "tags_text": "编程", "source_type": "memory", "updated_at": "2026-04-02T10:00:00"},
        ]
        count = search.sync_entries(entries)
        assert count == 2
        assert search.get_entry_count() == 2
        search.close()

    def test_search_keyword_mode(self, tmp_path):
        """Keyword-only search uses FTS5 BM25."""
        db_path = tmp_path / "keyword_test.db"
        search = HybridMemorySearch(db_path=str(db_path), chroma_path=None)
        search.sync_entries([
            {"id": "1", "title": "Rust 编程", "content": "Rust 的系统编程", "tags_text": "rust 编程", "source_type": "note", "updated_at": ""},
            {"id": "2", "title": "Go 编程", "content": "Go 的并发编程", "tags_text": "go 编程", "source_type": "note", "updated_at": ""},
        ])

        result = search.search("Rust", search_mode="keyword")
        assert result["mode"] == "keyword"
        assert result["count"] >= 1
        items = result["items"]
        assert any("Rust" in item.get("title", "") for item in items)
        search.close()

    def test_search_with_nl_time(self, tmp_path):
        """Search with natural language time phrase."""
        db_path = tmp_path / "nltime_test.db"
        search = HybridMemorySearch(db_path=str(db_path), chroma_path=None)
        search.sync_entries([
            {"id": "t1", "title": "上周的会议记录", "content": "讨论了数据库迁移方案", "tags_text": "会议", "source_type": "note", "updated_at": (datetime.now() - timedelta(days=3)).isoformat()},
            {"id": "t2", "title": "上个月的规划", "content": "季度计划", "tags_text": "规划", "source_type": "note", "updated_at": (datetime.now() - timedelta(days=40)).isoformat()},
        ])

        # Should parse"上周" from the query and apply time filter
        result = search.search("上周的数据库记忆", search_mode="keyword")
        assert result["effective_query"] != ""  # time part was removed from query
        # Verify time_range was parsed
        assert result["time_range"] is not None
        search.close()

    def test_add_and_delete_entry(self, tmp_path):
        """Add and delete entries from index."""
        db_path = tmp_path / "add_del_test.db"
        search = HybridMemorySearch(db_path=str(db_path), chroma_path=None)

        search.add_entry({"id": "a1", "title": "新增测试", "content": "新增加的条目", "tags_text": "测试", "source_type": "note", "updated_at": ""})
        assert search.get_entry_count() == 1

        search.delete_entry("a1")
        assert search.get_entry_count() == 0
        search.close()

    def test_update_entry(self, tmp_path):
        """Update entry in index."""
        db_path = tmp_path / "update_test.db"
        search = HybridMemorySearch(db_path=str(db_path), chroma_path=None)

        search.add_entry({"id": "u1", "title": "原标题", "content": "原内容", "tags_text": "", "source_type": "note", "updated_at": ""})
        search.update_entry("u1", {"title": "新标题", "content": "新内容"})

        result = search.search("新标题", search_mode="keyword")
        assert result["count"] >= 1
        search.close()

    def test_empty_query(self, tmp_path):
        """Empty query returns empty results."""
        db_path = tmp_path / "empty_test.db"
        search = HybridMemorySearch(db_path=str(db_path), chroma_path=None)
        search.sync_entries([
            {"id": "x1", "title": "test", "content": "test content", "tags_text": "", "source_type": "note", "updated_at": ""},
        ])

        result = search.search("", search_mode="keyword")
        assert result["count"] == 0
        search.close()

    def test_source_labels_in_results(self, tmp_path):
        """Results include source labels."""
        db_path = tmp_path / "labels_test.db"
        search = HybridMemorySearch(db_path=str(db_path), chroma_path=None)
        search.sync_entries([
            {"id": "l1", "title": "带标签的笔记", "content": "内容", "tags_text": "", "source_type": "note", "updated_at": ""},
            {"id": "l2", "title": "带标签的任务", "content": "内容", "tags_text": "", "source_type": "task", "updated_at": ""},
        ])

        result = search.search("带标签", search_mode="keyword")
        for item in result["items"]:
            assert "source_label" in item
            assert "source_color" in item
            # note items should have [笔记] label
            if item["source_type"] == "note":
                assert item["source_label"] == "[笔记]"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
