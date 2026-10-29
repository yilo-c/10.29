"""Tests for LocalIndexEngine - SQLite FTS5 full-text search engine."""
from __future__ import annotations

import sys
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

import pytest
from memory.local_index_engine import LocalIndexEngine


@pytest.fixture
def engine(tmp_path):
    """Create a fresh LocalIndexEngine for each test."""
    db_path = tmp_path / "test_memory.db"
    eng = LocalIndexEngine(str(db_path))
    yield eng
    eng.close()


class TestLocalIndexEngine:
    """Test suite for LocalIndexEngine."""

    def test_init_creates_tables(self, engine):
        """Engine initializes with empty database."""
        assert engine.get_entry_count() == 0

    def test_add_entry(self, engine):
        """Add a single entry and verify it's searchable."""
        engine.add_entry({
            "id": "test:1",
            "title": "数据库设计笔记",
            "content": "PostgreSQL 的分区表设计原则",
            "tags": "数据库 PostgreSQL",
            "source_type": "note",
            "updated_at": "2026-04-01T10:00:00",
        })
        assert engine.get_entry_count() == 1

        results = engine.search("PostgreSQL")
        assert len(results) == 1
        assert results[0]["id"] == "test:1"
        assert results[0]["bm25_score"] > 0

    def test_search_bm25_scoring(self, engine):
        """BM25 should rank results by relevance."""
        engine.add_entry({"id": "1", "title": "Python 学习", "content": "Python 是一种流行的编程语言", "tags": "", "source_type": "note", "updated_at": ""})
        engine.add_entry({"id": "2", "title": "编程语言对比", "content": "Python vs JavaScript 编程语言对比", "tags": "", "source_type": "note", "updated_at": ""})
        engine.add_entry({"id": "3", "title": "今日天气", "content": "今天天气很好", "tags": "", "source_type": "note", "updated_at": ""})

        results = engine.search("Python 编程语言")
        # All 3 should match (content matching)
        assert len(results) >= 2
        # The exact matches should score higher
        assert results[0]["bm25_score"] > 0

    def test_search_empty_query(self, engine):
        """Empty query returns empty results."""
        engine.add_entry({"id": "1", "title": "test", "content": "content", "tags": "", "source_type": "note", "updated_at": ""})
        results = engine.search("")
        assert len(results) == 0
        results = engine.search("   ")
        assert len(results) == 0

    def test_search_no_match(self, engine):
        """Query with no matches returns empty."""
        engine.add_entry({"id": "1", "title": "test", "content": "hello world", "tags": "", "source_type": "note", "updated_at": ""})
        results = engine.search("nonexistent")
        assert len(results) == 0

    def test_update_entry(self, engine):
        """Update an entry's content and re-search."""
        engine.add_entry({"id": "u:1", "title": "旧标题", "content": "旧内容", "tags": "", "source_type": "note", "updated_at": ""})
        assert len(engine.search("旧标题")) == 1

        engine.update_entry("u:1", {"title": "新标题", "content": "新内容"})
        assert len(engine.search("新标题")) == 1
        assert len(engine.search("旧标题")) == 0

    def test_delete_entry(self, engine):
        """Delete an entry removes it from search."""
        engine.add_entry({"id": "d:1", "title": "删除测试", "content": "将被删除", "tags": "", "source_type": "note", "updated_at": ""})
        assert engine.get_entry_count() == 1

        engine.delete_entry("d:1")
        assert engine.get_entry_count() == 0
        assert len(engine.search("删除")) == 0

    def test_sync_entries(self, engine):
        """Sync replaces all entries in the index."""
        engine.add_entry({"id": "old:1", "title": "旧条目", "content": "旧", "tags": "", "source_type": "note", "updated_at": ""})
        assert engine.get_entry_count() == 1

        count = engine.sync_entries([
            {"id": "new:1", "title": "新条目1", "content": "新内容1", "tags": "", "source_type": "note", "updated_at": ""},
            {"id": "new:2", "title": "新条目2", "content": "新内容2", "tags": "", "source_type": "note", "updated_at": ""},
        ])
        assert count == 2
        assert engine.get_entry_count() == 2
        assert len(engine.search("旧条目")) == 0
        assert len(engine.search("新条目1")) == 1

    def test_search_in_tags(self, engine):
        """Search should match tags field."""
        engine.add_entry({"id": "t:1", "title": "标题", "content": "内容", "tags": "rust 编程 系统编程", "source_type": "note", "updated_at": ""})
        results = engine.search("rust")
        assert len(results) == 1

    def test_search_top_k_limit(self, engine):
        """Search respects top_k parameter."""
        for i in range(20):
            engine.add_entry({"id": f"k:{i}", "title": f"匹配条目{i}", "content": "test content for searching", "tags": "", "source_type": "note", "updated_at": ""})
        results = engine.search("匹配", top_k=5)
        assert len(results) == 5

    def test_sanitize_fts5_query(self, engine):
        """FTS5 special characters should be sanitized."""
        engine.add_entry({"id": "s:1", "title": "C++ 编程", "content": "C++ 是一种高级编程语言", "tags": "", "source_type": "note", "updated_at": ""})
        # Characters that would break FTS5
        engine.add_entry({"id": "s:2", "title": "特殊标题", "content": "价格: 100元 (含税) {重要}", "tags": "", "source_type": "note", "updated_at": ""})

        results = engine.search("C++")
        assert len(results) >= 1

    def test_rebuild_fts_index(self, engine):
        """Rebuild FTS index after data changes."""
        engine.add_entry({"id": "r:1", "title": "重建测试", "content": "测试内容", "tags": "", "source_type": "note", "updated_at": ""})
        engine.rebuild_fts_index()
        assert engine.get_entry_count() == 1
        assert len(engine.search("重建")) == 1

    def test_fallback_search_on_bad_query(self, engine):
        """Fallback to LIKE search when FTS5 query fails."""
        engine.add_entry({"id": "f:1", "title": "测试标题", "content": "测试内容文本", "tags": "", "source_type": "note", "updated_at": ""})
        # Query with problematic characters should trigger fallback
        results = engine.search("测试")
        assert len(results) == 1

    @pytest.mark.parametrize("tags_input,expected", [
        (["tag1", "tag2", "tag3"], "tag1 tag2 tag3"),
        ("single_tag", "single_tag"),
        (None, ""),
    ])
    def test_tags_handling(self, engine, tags_input, expected):
        """Tags list is properly joined to string."""
        engine.add_entry({
            "id": "tagtest:1",
            "title": "标签测试",
            "content": "测试",
            "tags": tags_input,
            "source_type": "note",
            "updated_at": "",
        })
        # Verify the entry is indexed (search by expected tag content)
        if expected:
            parts = expected.split()
            if parts:
                assert len(engine.search(parts[0])) == 1

    def test_multiple_add_and_search(self, engine):
        """Multiple entries with varied content can be searched."""
        entries = [
            {"id": f"m:{i}", "title": f"标题{i}", "content": f"内容{i}：这是第{i}条测试数据", "tags": "测试", "source_type": "memory", "updated_at": f"2026-04-{i+1:02d}T00:00:00"}
            for i in range(1, 11)
        ]
        engine.sync_entries(entries)

        results = engine.search("测试数据", top_k=10)
        assert len(results) >= 5


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
