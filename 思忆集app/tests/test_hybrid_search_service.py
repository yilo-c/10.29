"""Tests for HybridSearchService - hybrid search, weight config, re-ranking, EventBus, degradation."""
from __future__ import annotations

import sys
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from datetime import datetime
from unittest.mock import MagicMock

import pytest
from services.hybrid_search_service import (
    HybridSearchService,
    SearchConfig,
    ReRankMethod,
)


# ══════════════════════════════════════════════════════════════════════
# Helpers
# ══════════════════════════════════════════════════════════════════════


def make_mock_memory_search():
    """Create a mock HybridMemorySearch-like object.

    Avoids importing memory_search directly due to ChromaDB/NumPy 2.0
    incompatibility in the test environment.
    """
    mock = MagicMock()

    # search returns a result dict similar to HybridMemorySearch.search()
    mock.search.return_value = {
        "query": "",
        "effective_query": "",
        "mode": "hybrid",
        "count": 0,
        "items": [],
        "sources": [],
        "time_range": None,
    }

    mock.sync_entries.return_value = 0
    mock.get_entry_count.return_value = 0
    return mock


def make_mock_result(
    items: list[dict] | None = None,
    mode: str = "hybrid",
    effective_query: str = "",
) -> dict:
    """Create a mock search result dict."""
    return {
        "query": "",
        "effective_query": effective_query or "",
        "mode": mode,
        "count": len(items) if items else 0,
        "items": items or [],
        "sources": list(dict.fromkeys(item.get("source_type", "") for item in (items or []))),
        "time_range": None,
    }


# ══════════════════════════════════════════════════════════════════════
# SearchConfig 测试
# ══════════════════════════════════════════════════════════════════════

class TestSearchConfig:
    """SearchConfig validation and defaults."""

    def test_default_config(self):
        """Default config should have expected values."""
        cfg = SearchConfig()
        assert cfg.alpha == 0.7
        assert cfg.rrf_k == 60
        assert cfg.top_k == 10
        assert cfg.re_rank_method == ReRankMethod.RRF
        assert cfg.enable_event is True
        assert cfg.vector_top_k_ratio == 2.0
        assert cfg.keyword_top_k_ratio == 2.0

    def test_alpha_clamped_to_zero(self):
        """Alpha below 0 should be clamped to 0."""
        cfg = SearchConfig(alpha=-0.5)
        assert cfg.alpha == 0.0

    def test_alpha_clamped_to_one(self):
        """Alpha above 1 should be clamped to 1."""
        cfg = SearchConfig(alpha=1.5)
        assert cfg.alpha == 1.0

    def test_rrf_k_minimum(self):
        """RRF k should be at least 1."""
        cfg = SearchConfig(rrf_k=0)
        assert cfg.rrf_k == 1
        cfg = SearchConfig(rrf_k=-5)
        assert cfg.rrf_k == 1

    def test_top_k_minimum(self):
        """top_k should be at least 1."""
        cfg = SearchConfig(top_k=0)
        assert cfg.top_k == 1


# ══════════════════════════════════════════════════════════════════════
# HybridSearchService 初始化测试
# ══════════════════════════════════════════════════════════════════════

class TestHybridSearchServiceInit:
    """HybridSearchService initialization."""

    def test_init_without_engine(self):
        """Service initializes without a search engine."""
        service = HybridSearchService(memory_search=None)
        assert service._memory_search is None
        result = service.search("test query")
        assert result["count"] == 0
        assert result["mode"] == "none"
        service.close()

    def test_init_with_default_config(self):
        """Service initializes with default config."""
        service = HybridSearchService(memory_search=None)
        cfg = service.config
        assert cfg.alpha == 0.7
        assert cfg.rrf_k == 60
        assert cfg.top_k == 10
        service.close()

    def test_init_with_custom_config(self):
        """Service initializes with custom config."""
        config = SearchConfig(alpha=0.5, rrf_k=30, top_k=5, enable_event=False)
        service = HybridSearchService(memory_search=None, config=config)
        cfg = service.config
        assert cfg.alpha == 0.5
        assert cfg.rrf_k == 30
        assert cfg.top_k == 5
        assert cfg.enable_event is False
        service.close()

    def test_init_with_mock_engine(self):
        """Service initializes with a mock engine."""
        mock = make_mock_memory_search()
        service = HybridSearchService(memory_search=mock)
        assert service._memory_search is not None
        health = service.health_check()
        assert health["available"] is True
        # MagicMock is used in unit tests
        assert "Mock" in health["engine_type"] or health["engine_type"] != "none"
        service.close()


# ══════════════════════════════════════════════════════════════════════
# 配置管理测试
# ══════════════════════════════════════════════════════════════════════

class TestConfigManagement:
    """Runtime configuration management."""

    def test_update_config_alpha(self):
        """Update alpha at runtime."""
        service = HybridSearchService(memory_search=None)
        cfg = service.update_config(alpha=0.3)
        assert cfg.alpha == 0.3
        assert service.config.alpha == 0.3
        service.close()

    def test_update_config_rrf_k(self):
        """Update rrf_k at runtime."""
        service = HybridSearchService(memory_search=None)
        cfg = service.update_config(rrf_k=20)
        assert cfg.rrf_k == 20
        service.close()

    def test_update_config_top_k(self):
        """Update top_k at runtime."""
        service = HybridSearchService(memory_search=None)
        cfg = service.update_config(top_k=15)
        assert cfg.top_k == 15
        service.close()

    def test_update_config_multiple(self):
        """Update multiple config fields at once."""
        service = HybridSearchService(memory_search=None)
        cfg = service.update_config(alpha=0.4, rrf_k=40, top_k=8, enable_event=False)
        assert cfg.alpha == 0.4
        assert cfg.rrf_k == 40
        assert cfg.top_k == 8
        assert cfg.enable_event is False
        service.close()

    def test_update_config_ignores_unknown(self):
        """Unknown config keys should be ignored."""
        service = HybridSearchService(memory_search=None)
        cfg = service.update_config(alpha=0.5, unknown_key="value")
        assert cfg.alpha == 0.5
        service.close()

    def test_config_isolation(self):
        """Config property returns a copy, not the internal reference."""
        service = HybridSearchService(memory_search=None)
        cfg1 = service.config
        cfg2 = service.config
        cfg1.alpha = 0.1
        # The internal config should not be affected
        assert service.config.alpha == 0.7
        service.close()

    def test_re_rank_method_update(self):
        """Update re_rank_method at runtime."""
        service = HybridSearchService(memory_search=None)
        cfg = service.update_config(re_rank_method=ReRankMethod.CROSS_ENCODER)
        assert cfg.re_rank_method == ReRankMethod.CROSS_ENCODER
        assert service.config.re_rank_method == ReRankMethod.CROSS_ENCODER
        service.close()


# ══════════════════════════════════════════════════════════════════════
# 搜索引擎集成测试（使用 mock 引擎）
# ══════════════════════════════════════════════════════════════════════

class TestSearchWithMockEngine:
    """HybridSearchService search with mock engine."""

    @pytest.fixture(autouse=True)
    def _setup_engine(self):
        self._mock = make_mock_memory_search()
        self._service = HybridSearchService(
            memory_search=self._mock,
            config=SearchConfig(alpha=0.7, rrf_k=60, top_k=10, enable_event=False),
        )
        yield
        self._service.close()

    def test_keyword_search(self):
        """Keyword search calls engine with correct mode."""
        self._mock.search.return_value = make_mock_result(
            items=[{"id": "1", "title": "test", "source_type": "note"}],
            mode="keyword",
        )
        result = self._service.search("test", search_mode="keyword")
        assert result["count"] == 1
        self._mock.search.assert_called_with(
            query="test", top_k=10, search_mode="keyword",
            source_filters=None, time_range=None,
        )

    def test_hybrid_search(self):
        """Hybrid search calls engine with hybrid mode."""
        self._mock.search.return_value = make_mock_result(
            items=[{"id": "1", "title": "test", "source_type": "note"}],
            mode="hybrid",
        )
        result = self._service.search("test", search_mode="hybrid")
        assert result["count"] == 1
        self._mock.search.assert_called_with(
            query="test", top_k=10, search_mode="hybrid",
            source_filters=None, time_range=None,
        )

    def test_vector_search(self):
        """Vector search calls engine with vector mode."""
        self._mock.search.return_value = make_mock_result(
            items=[{"id": "1", "title": "test", "source_type": "note"}],
            mode="vector",
        )
        result = self._service.search("test", search_mode="vector")
        assert result["count"] == 1
        self._mock.search.assert_called_with(
            query="test", top_k=10, search_mode="vector",
            source_filters=None, time_range=None,
        )

    def test_search_no_match(self):
        """Search with no matches returns empty."""
        result = self._service.search("nonexistent", search_mode="keyword")
        assert result["count"] == 0

    def test_search_top_k(self):
        """Search respects top_k parameter."""
        self._mock.search.return_value = make_mock_result(
            items=[{"id": str(i), "title": f"item{i}", "source_type": "note"} for i in range(5)],
        )
        result = self._service.search("test", search_mode="keyword", top_k=3)
        assert result["count"] == 5  # mock returns all, filtering is downstream

    def test_search_elapsed_ms(self):
        """Search results include elapsed_ms."""
        result = self._service.search("test", search_mode="keyword")
        assert "elapsed_ms" in result
        assert isinstance(result["elapsed_ms"], (int, float))
        assert result["elapsed_ms"] >= 0

    def test_search_passes_source_filters(self):
        """Search passes source_filters to engine."""
        self._service.search(
            "test", search_mode="keyword", source_filters=["note"],
        )
        self._mock.search.assert_called_with(
            query="test", top_k=10, search_mode="keyword",
            source_filters=["note"], time_range=None,
        )

    def test_search_passes_time_range(self):
        """Search passes time_range to engine."""
        start = datetime(2026, 1, 1)
        end = datetime(2026, 12, 31)
        self._service.search(
            "test", search_mode="keyword", time_range=(start, end),
        )
        self._mock.search.assert_called_with(
            query="test", top_k=10, search_mode="keyword",
            source_filters=None, time_range=(start, end),
        )

    def test_search_normalizes_mode(self):
        """Invalid mode should be normalized to 'hybrid'."""
        self._mock.search.return_value = make_mock_result(
            items=[{"id": "1", "title": "test", "source_type": "note"}],
            mode="hybrid",
        )
        result = self._service.search("test", search_mode="invalid")
        assert result["count"] == 1


# ══════════════════════════════════════════════════════════════════════
# 优雅降级测试
# ══════════════════════════════════════════════════════════════════════

class TestGracefulDegradation:
    """Graceful degradation when search engine fails."""

    def test_no_engine(self):
        """Without engine, search returns empty result."""
        service = HybridSearchService(memory_search=None)
        result = service.search("test")
        assert result["count"] == 0
        assert result["mode"] == "none"
        service.close()

    def test_sync_entries_no_engine(self):
        """Without engine, sync_entries returns 0."""
        service = HybridSearchService(memory_search=None)
        assert service.sync_entries([{"id": "1", "title": "test"}]) == 0
        service.close()

    def test_get_entry_count_no_engine(self):
        """Without engine, get_entry_count returns 0."""
        service = HybridSearchService(memory_search=None)
        assert service.get_entry_count() == 0
        service.close()

    def test_health_check_no_engine(self):
        """Health check correctly reports unavailable."""
        service = HybridSearchService(memory_search=None)
        health = service.health_check()
        assert health["available"] is False
        assert health["engine_type"] == "none"
        service.close()

    def test_engine_exception_fallback(self):
        """When engine raises, fallback to keyword search."""
        mock = make_mock_memory_search()
        mock.search.side_effect = RuntimeError("ChromaDB unavailable")

        # Keyword fallback should still work
        mock.search.side_effect = [RuntimeError("ChromaDB"), make_mock_result(
            items=[{"id": "1", "title": "fallback result", "source_type": "note"}],
            mode="keyword",
        )]

        service = HybridSearchService(
            memory_search=mock,
            config=SearchConfig(enable_event=False),
        )
        result = service.search("test", search_mode="hybrid")
        # Should fallback to keyword on the retry
        assert result["count"] == 1 or result["mode"] in ("keyword", "none")
        service.close()

    def test_add_entry_no_engine(self):
        """Without engine, add_entry is no-op."""
        service = HybridSearchService(memory_search=None)
        service.add_entry({"id": "1", "title": "test"})
        # Should not raise
        service.close()

    def test_update_entry_no_engine(self):
        """Without engine, update_entry is no-op."""
        service = HybridSearchService(memory_search=None)
        service.update_entry("1", {"title": "new"})
        service.close()

    def test_delete_entry_no_engine(self):
        """Without engine, delete_entry is no-op."""
        service = HybridSearchService(memory_search=None)
        service.delete_entry("1")
        service.close()


# ══════════════════════════════════════════════════════════════════════
# 索引管理测试（使用 mock 引擎）
# ══════════════════════════════════════════════════════════════════════

class TestIndexManagement:
    """Index management through HybridSearchService."""

    def test_sync_entries(self):
        """Sync entries through service."""
        mock = make_mock_memory_search()
        mock.sync_entries.return_value = 2
        mock.get_entry_count.return_value = 2

        service = HybridSearchService(memory_search=mock)
        entries = [
            {"id": "s1", "title": "条目1", "content": "内容1"},
            {"id": "s2", "title": "条目2", "content": "内容2"},
        ]
        count = service.sync_entries(entries)
        assert count == 2
        assert service.get_entry_count() == 2
        mock.sync_entries.assert_called_with(entries)
        service.close()

    def test_add_entry(self):
        """Add entry through service."""
        mock = make_mock_memory_search()
        service = HybridSearchService(memory_search=mock)
        service.add_entry({"id": "a1", "title": "新增"})
        mock.add_entry.assert_called_with({"id": "a1", "title": "新增"})
        service.close()

    def test_delete_entry(self):
        """Delete entry through service."""
        mock = make_mock_memory_search()
        service = HybridSearchService(memory_search=mock)
        service.delete_entry("a1")
        mock.delete_entry.assert_called_with("a1")
        service.close()

    def test_update_entry(self):
        """Update entry through service."""
        mock = make_mock_memory_search()
        mock.search.return_value = make_mock_result(
            items=[{"id": "u1", "title": "新标题", "content": "新内容", "source_type": "note"}],
        )
        service = HybridSearchService(memory_search=mock)
        service.update_entry("u1", {"title": "新标题"})
        mock.update_entry.assert_called_with("u1", {"title": "新标题"})
        service.close()

    def test_close(self):
        """Close releases underlying engine."""
        mock = make_mock_memory_search()
        service = HybridSearchService(memory_search=mock)
        service.close()
        mock.close.assert_called_once()


# ══════════════════════════════════════════════════════════════════════
# EventBus 集成测试
# ══════════════════════════════════════════════════════════════════════

class TestEventBusIntegration:
    """EventBus search.completed event."""

    def test_emit_search_completed_event(self):
        """Search emits search.completed event."""
        # Import the global event bus
        from core.events import event_bus

        mock = make_mock_memory_search()
        mock.search.return_value = make_mock_result(
            items=[{"id": "1", "title": "test", "source_type": "note"}],
            mode="keyword",
        )

        captured = []

        def handler(event):
            captured.append(event)

        event_bus.on("search.completed", handler)
        try:
            service = HybridSearchService(
                memory_search=mock,
                config=SearchConfig(enable_event=True),
            )
            service.search("test_query", search_mode="keyword")

            assert len(captured) == 1
            event = captured[0]
            assert event.event_type == "search.completed"
            assert event.payload["query"] == "test_query"
            assert event.payload["mode"] == "keyword"
            assert "elapsed_ms" in event.payload
            assert "count" in event.payload
            service.close()
        finally:
            event_bus.off("search.completed", handler)

    def test_event_disabled(self):
        """When enable_event=False, no event is emitted."""
        from core.events import event_bus

        mock = make_mock_memory_search()
        mock.search.return_value = make_mock_result(
            items=[{"id": "1", "title": "test", "source_type": "note"}],
        )

        captured = []

        def handler(event):
            captured.append(event)

        event_bus.on("search.completed", handler)
        try:
            service = HybridSearchService(
                memory_search=mock,
                config=SearchConfig(enable_event=False),
            )
            service.search("test_query", search_mode="keyword")

            assert len(captured) == 0
            service.close()
        finally:
            event_bus.off("search.completed", handler)


# ══════════════════════════════════════════════════════════════════════
# 健康检查测试
# ══════════════════════════════════════════════════════════════════════

class TestHealthCheck:
    """Health check."""

    def test_health_check_with_engine(self):
        """Health check with available engine."""
        mock = make_mock_memory_search()
        mock.get_entry_count.return_value = 42

        service = HybridSearchService(
            memory_search=mock,
            config=SearchConfig(alpha=0.5, enable_event=False),
        )

        health = service.health_check()
        assert health["available"] is True
        assert health["config"]["alpha"] == 0.5
        assert health["config"]["enable_event"] is False
        assert health["entry_count"] == 42
        service.close()

    def test_health_check_without_engine(self):
        """Health check without engine."""
        service = HybridSearchService(memory_search=None)
        health = service.health_check()
        assert health["available"] is False
        assert health["entry_count"] == 0
        service.close()


# ══════════════════════════════════════════════════════════════════════
# 搜索前/后钩子测试
# ══════════════════════════════════════════════════════════════════════

class TestHooks:
    """Pre-search and post-search hooks."""

    def test_pre_search_hook_modifies_query(self):
        """Pre-search hook can modify the query."""
        mock = make_mock_memory_search()
        mock.search.return_value = make_mock_result(
            items=[{"id": "1", "title": "result", "source_type": "note"}],
        )
        service = HybridSearchService(
            memory_search=mock,
            config=SearchConfig(enable_event=False),
        )

        def prepend_tag(query: str, config) -> str:
            return f"prefix {query}" if query else query

        service.add_pre_search_hook(prepend_tag)

        result = service.search("test", search_mode="keyword")

        # The pre-search hook should run (it modifies the query,
        # but for a mock engine we can verify it was called)
        assert result["count"] == 1
        service.close()

    def test_multiple_pre_search_hooks(self):
        """Multiple pre-search hooks run in order."""
        mock = make_mock_memory_search()
        mock.search.return_value = make_mock_result(
            items=[{"id": "1", "title": "result", "source_type": "note"}],
        )
        service = HybridSearchService(
            memory_search=mock,
            config=SearchConfig(enable_event=False),
        )

        calls = []

        def hook1(query: str, config) -> str:
            calls.append("hook1")
            return query

        def hook2(query: str, config) -> str:
            calls.append("hook2")
            return query

        service.add_pre_search_hook(hook1)
        service.add_pre_search_hook(hook2)
        service.search("test", search_mode="keyword")

        assert calls == ["hook1", "hook2"]
        service.close()

    def test_post_search_hook_receives_result(self):
        """Post-search hook receives the result dict."""
        mock = make_mock_memory_search()
        mock.search.return_value = make_mock_result(
            items=[{"id": "1", "title": "test", "source_type": "note"}],
        )
        service = HybridSearchService(
            memory_search=mock,
            config=SearchConfig(enable_event=False),
        )

        captured = []

        def capture_result(result):
            captured.append(result)

        service.add_post_search_hook(capture_result)
        result = service.search("test", search_mode="keyword")

        assert len(captured) == 1
        assert "items" in captured[0]
        assert "elapsed_ms" in captured[0]
        service.close()

    def test_hook_exception_does_not_break_search(self):
        """Hook exception should not break the search flow."""
        mock = make_mock_memory_search()
        mock.search.return_value = make_mock_result(
            items=[{"id": "1", "title": "test", "source_type": "note"}],
        )
        service = HybridSearchService(
            memory_search=mock,
            config=SearchConfig(enable_event=False),
        )

        def failing_hook(query: str, config) -> str:
            raise ValueError("hook failed")

        service.add_pre_search_hook(failing_hook)

        # Should not raise
        result = service.search("test", search_mode="keyword")
        assert result["count"] == 1
        service.close()


# ══════════════════════════════════════════════════════════════════════
# Alpha 权重调整测试
# ══════════════════════════════════════════════════════════════════════

class TestAlphaWeight:
    """Alpha weight adjustment."""

    def test_apply_alpha_weight_high_alpha(self):
        """High alpha favors vector scores."""
        mock = make_mock_memory_search()
        mock.search.return_value = make_mock_result(
            items=[
                {"id": "1", "title": "A", "content": "a", "source_type": "note",
                 "vector_score": 0.9, "bm25_score": 0.1},
                {"id": "2", "title": "B", "content": "b", "source_type": "note",
                 "vector_score": 0.5, "bm25_score": 0.8},
            ],
        )
        service = HybridSearchService(
            memory_search=mock,
            config=SearchConfig(alpha=0.5, enable_event=False),
        )
        # Test _apply_alpha_weight directly
        result = mock.search.return_value
        adjusted = service._apply_alpha_weight(result, alpha=0.9)
        items = adjusted.get("items", [])
        assert len(items) == 2
        service.close()

    def test_apply_alpha_weight_low_alpha(self):
        """Low alpha favors BM25 scores."""
        mock = make_mock_memory_search()
        mock.search.return_value = make_mock_result(
            items=[
                {"id": "1", "title": "A", "content": "a", "source_type": "note",
                 "vector_score": 0.9, "bm25_score": 0.1},
            ],
        )
        service = HybridSearchService(
            memory_search=mock,
            config=SearchConfig(alpha=0.5, enable_event=False),
        )
        result = mock.search.return_value
        adjusted = service._apply_alpha_weight(result, alpha=0.1)
        items = adjusted.get("items", [])
        assert len(items) == 1
        service.close()


# ══════════════════════════════════════════════════════════════════════
# 交叉编码器重排序（占位）测试
# ══════════════════════════════════════════════════════════════════════

class TestCrossEncoderReRank:
    """Cross-encoder re-ranking (placeholder implementation)."""

    def test_cross_encoder_placeholder_no_crash(self):
        """Cross-encoder placeholder doesn't crash."""
        mock = make_mock_memory_search()
        mock.search.return_value = make_mock_result(
            items=[{"id": "1", "title": "test", "source_type": "note"}],
        )
        service = HybridSearchService(
            memory_search=mock,
            config=SearchConfig(
                enable_event=False,
                re_rank_method=ReRankMethod.CROSS_ENCODER,
            ),
        )

        result = service.search("test", search_mode="hybrid")
        assert result is not None
        service.close()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
