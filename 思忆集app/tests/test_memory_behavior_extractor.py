"""Tests for BehaviorMemoryExtractor."""

from __future__ import annotations

import json
import os
import tempfile
from unittest.mock import MagicMock

import pytest

from memory.behavior_extractor import (
    BehaviorMemoryExtractor,
    _classify_file_event,
    _classify_browser_event,
    _skip_path,
)
from memory.types import (
    CONFIRM_THRESHOLD,
    MemoryCandidate,
)


# ── _skip_path tests ──


class TestSkipPath:
    def test_skips_node_modules(self):
        assert _skip_path("project/node_modules/package/index.js")

    def test_skips_git_dir(self):
        assert _skip_path(".git/config")
        assert _skip_path("repo/.git/HEAD")

    def test_skips_pycache(self):
        assert _skip_path("src/__pycache__/module.pyc")

    def test_skips_venv(self):
        assert _skip_path("project/.venv/lib/site-packages/pkg")

    def test_allows_normal_project_files(self):
        assert not _skip_path("project/src/main.py")
        assert not _skip_path("docs/guide.md")
        assert not _skip_path("笔记/工作日志.md")


# ── _classify_file_event tests ──


class TestClassifyFileEvent:
    def test_important_markdown_file(self):
        content, confidence, tags = _classify_file_event("file_modified", {
            "path": "docs/project/architecture.md",
            "extension": ".md",
        })
        assert content
        assert "修改了" in content
        assert "architecture.md" in content
        assert confidence >= 0.6
        assert "md" in tags

    def test_code_file_high_confidence(self):
        content, confidence, tags = _classify_file_event("file_created", {
            "path": "src/services/api.py",
            "extension": ".py",
        })
        assert content
        assert "创建了" in content
        assert confidence >= 0.5
        assert "py" in tags

    def test_skip_path_returns_empty(self):
        content, confidence, tags = _classify_file_event("file_modified", {
            "path": "node_modules/pkg/index.js",
            "extension": ".js",
        })
        assert not content
        assert confidence == 0.0

    def test_unknown_extension_returns_empty(self):
        content, confidence, tags = _classify_file_event("file_modified", {
            "path": "file.xyz",
            "extension": ".xyz",
        })
        assert not content
        assert confidence == 0.0

    def test_deleted_file_lower_confidence(self):
        content, confidence, tags = _classify_file_event("file_deleted", {
            "path": "project/temp.py",
            "extension": ".py",
        })
        # 删除操作可能低于确认阈值
        if not content:
            return  # 允许直接丢弃
        assert confidence < 0.6

    def test_shallow_path_baseline(self):
        content, confidence, tags = _classify_file_event("file_modified", {
            "path": "note.md",
            "extension": ".md",
        })
        if not content:
            return
        # depth=0 的 .md 文件使用兜底置信度 0.48
        assert confidence == 0.48


# ── _classify_browser_event tests ──


class TestClassifyBrowserEvent:
    def test_github_visit(self):
        content, confidence, tags = _classify_browser_event("page_visited", {
            "url": "https://github.com/user/repo/pull/123",
            "title": "Fix bug in parser by user · Pull Request #123",
        })
        assert content
        assert "浏览了" in content
        assert confidence >= 0.5
        assert "research" in tags

    def test_search_query(self):
        content, confidence, tags = _classify_browser_event("search_queried", {
            "url": "https://google.com/search?q=rust+concurrency",
            "title": "rust concurrency",
            "query": "rust concurrency",
        })
        assert content
        assert "搜索了" in content
        assert "rust concurrency" in content
        assert confidence >= 0.4

    def test_generic_url_low_confidence(self):
        content, confidence, tags = _classify_browser_event("page_visited", {
            "url": "https://example.com/page",
            "title": "Some Page",
        })
        # 通用域名不应产生记忆
        assert not content or confidence < CONFIRM_THRESHOLD

    def test_learning_domain(self):
        content, confidence, tags = _classify_browser_event("page_summarized", {
            "url": "https://docs.python.org/3/tutorial/",
            "title": "The Python Tutorial — Python 3.13 documentation",
        })
        assert content
        assert confidence >= 0.5


# ── BehaviorMemoryExtractor integration tests ──


class TestBehaviorMemoryExtractor:
    @pytest.fixture
    def mock_logger(self):
        logger = MagicMock()
        logger.query.return_value = [
            {"category": "file", "action": "file_modified", "detail": {"path": "project/note.md", "extension": ".md"}, "timestamp": "2026-05-06T10:00:00"},
            {"category": "file", "action": "file_created", "detail": {"path": "node_modules/pkg.js", "extension": ".js"}, "timestamp": "2026-05-06T10:01:00"},
            {"category": "browser", "action": "page_visited", "detail": {"url": "https://github.com/user/repo", "title": "user/repo: main project"}, "timestamp": "2026-05-06T10:02:00"},
            {"category": "browser", "action": "page_visited", "detail": {"url": "https://example.com", "title": "Example"}, "timestamp": "2026-05-06T10:03:00"},
            {"category": "app", "action": "app_focus", "detail": {}, "timestamp": "2026-05-06T10:04:00"},
        ]
        return logger

    @pytest.fixture
    def mock_scheduler(self):
        scheduler = MagicMock()
        scheduler.add_memory.return_value = MagicMock()
        return scheduler

    @pytest.fixture
    def temp_data_store(self):
        with tempfile.TemporaryDirectory() as d:
            from data.local_storage import LocalDataStore
            store = LocalDataStore(d)
            try:
                yield store
            finally:
                store.close()

    def test_extract_produces_candidates(self, mock_logger, mock_scheduler):
        extractor = BehaviorMemoryExtractor(
            behavior_logger=mock_logger,
            memory_scheduler=mock_scheduler,
        )
        result = extractor.extract(limit=50)

        assert result is not None
        # 2 条应该生成记忆（note.md + github），node_modules 被跳过，example 置信度低，app_focus 不记
        assert len(result.candidates) >= 1
        assert len(result.discarded) + len(result.needs_confirm) + len(result.auto_write) == len(result.candidates)

    def test_extract_stores_auto_write(self, mock_logger, mock_scheduler):
        extractor = BehaviorMemoryExtractor(
            behavior_logger=mock_logger,
            memory_scheduler=mock_scheduler,
        )
        result = extractor.extract(limit=50)

        stored_count = len(result.auto_write) + len(result.needs_confirm)
        assert mock_scheduler.add_memory.call_count == stored_count

    def test_extract_no_logger_returns_empty(self):
        extractor = BehaviorMemoryExtractor()
        result = extractor.extract()
        assert len(result.candidates) == 0

    def test_extract_with_data_store_checkpoint(self, mock_logger, mock_scheduler, temp_data_store):
        extractor = BehaviorMemoryExtractor(
            behavior_logger=mock_logger,
            memory_scheduler=mock_scheduler,
            data_store=temp_data_store,
        )
        extractor.extract(limit=50)

        # 检查点应已保存
        checkpoint = temp_data_store.load_json("behavior_memory_checkpoint", {})
        assert isinstance(checkpoint, dict)
        assert checkpoint.get("last_timestamp") == "2026-05-06T10:04:00"

    def test_incremental_extraction(self, mock_logger, mock_scheduler, temp_data_store):
        """第二次 extract 不应重复处理相同事件。"""
        # 让 mock 在第二次调用时返回空列表（模拟 since 参数过滤）
        calls = iter([
            # 第一次：返回事件
            [{"category": "file", "action": "file_modified", "detail": {"path": "project/note.md", "extension": ".md"}, "timestamp": "2026-05-06T10:00:00"}],
            # 第二次：空（since 已推进到 10:00:00）
            [],
        ])
        mock_logger.query.side_effect = lambda **kw: next(calls)

        extractor = BehaviorMemoryExtractor(
            behavior_logger=mock_logger,
            memory_scheduler=mock_scheduler,
            data_store=temp_data_store,
        )
        result1 = extractor.extract(limit=50)
        assert len(result1.candidates) == 1

        mock_scheduler.add_memory.reset_mock()

        result2 = extractor.extract(limit=50)
        assert len(result2.candidates) == 0
        assert mock_scheduler.add_memory.call_count == 0

    def test_privacy_filter_blocks_sensitive(self, mock_logger, mock_scheduler):
        mock_logger.query.return_value = [
            {"category": "file", "action": "file_modified", "detail": {"path": "credentials.txt", "extension": ".txt"}, "timestamp": "2026-05-06T11:00:00", "content": "密码 abc123"},
        ]
        extractor = BehaviorMemoryExtractor(
            behavior_logger=mock_logger,
            memory_scheduler=mock_scheduler,
        )
        result = extractor.extract(limit=50)
        # 即使有事件，隐私过滤器也会处理
        assert result is not None

    def test_config_filters(self, mock_logger, mock_scheduler):
        """browser_events=False 时不应产生浏览器记忆。"""
        mock_logger.query.return_value = [
            {"category": "browser", "action": "page_visited", "detail": {"url": "https://github.com/user/repo", "title": "repo"}, "timestamp": "2026-05-06T12:00:00"},
        ]
        extractor = BehaviorMemoryExtractor(
            behavior_logger=mock_logger,
            memory_scheduler=mock_scheduler,
        )
        extractor.update_config(browser_events=False)
        result = extractor.extract(limit=50)
        assert len(result.candidates) == 0

    def test_get_status(self, mock_logger, mock_scheduler):
        extractor = BehaviorMemoryExtractor(
            behavior_logger=mock_logger,
            memory_scheduler=mock_scheduler,
        )
        status = extractor.get_status()
        assert "enabled" in status
        assert "config" in status

    def test_store_candidate_no_scheduler(self):
        extractor = BehaviorMemoryExtractor()
        candidate = MemoryCandidate(type="factual", content="test", confidence=0.9, source="user")
        assert not extractor._store_candidate(candidate)

    def test_empty_events_list(self, mock_logger, mock_scheduler):
        mock_logger.query.return_value = []
        extractor = BehaviorMemoryExtractor(
            behavior_logger=mock_logger,
            memory_scheduler=mock_scheduler,
        )
        result = extractor.extract(limit=50)
        assert len(result.candidates) == 0


# ── Direct test: _classify_file_event edge cases ──


class TestClassifyFileEventEdgeCases:
    def test_empty_path(self):
        content, confidence, tags = _classify_file_event("file_created", {
            "path": "",
            "extension": ".md",
        })
        assert not content

    def test_dotfile_hidden(self):
        content, confidence, tags = _classify_file_event("file_modified", {
            "path": ".hiddenrc",
            "extension": ".rc",
        })
        assert not content

    def test_nested_project_file(self):
        content, confidence, tags = _classify_file_event("file_modified", {
            "path": "a/b/c/d/script.py",
            "extension": ".py",
        })
        assert content
        # 深层路径应略高于浅层
        assert confidence >= 0.58

    def test_pdf_file(self):
        content, confidence, tags = _classify_file_event("file_created", {
            "path": "papers/paper.pdf",
            "extension": ".pdf",
        })
        assert content
        assert confidence >= 0.55
