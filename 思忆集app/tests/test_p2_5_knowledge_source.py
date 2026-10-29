"""KnowledgeSource Protocol + 5 个 Adapter 单元测试（P2-5 批次 A）。

覆盖：
- KnowledgeSource Protocol runtime_checkable
- MemoryAdapter: recall 优先、search 回退、都缺失则不触发
- KnowledgeGraphAdapter: search 优先、query 回退、意图过滤
- RagAdapter: 意图过滤、query_knowledge_base 缺失
- NoteAdapter: list_tasks 缺失、标题/正文包含过滤
- WordbookAdapter: 含字母才触发、search_words 缺失
- _coerce_items: dict-with-items / list / None / 杂项
- UnifiedKnowledgeService.sources 暴露、extra_sources 注入
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from knowledge.source import (
    KnowledgeGraphAdapter,
    KnowledgeSource,
    MemoryAdapter,
    NoteAdapter,
    RagAdapter,
    WordbookAdapter,
    _coerce_items,
)
from knowledge.unified_service import UnifiedKnowledgeService


# ═══════════════════════════════════════════════════════════════
# Protocol runtime check
# ═══════════════════════════════════════════════════════════════


class TestProtocolRuntimeCheck:
    def test_memory_adapter_is_knowledge_source(self):
        adapter = MemoryAdapter(MagicMock())
        assert isinstance(adapter, KnowledgeSource)

    def test_random_object_not_knowledge_source(self):
        assert not isinstance("string", KnowledgeSource)
        assert not isinstance(object(), KnowledgeSource)

    def test_all_builtin_adapters_satisfy_protocol(self):
        for cls in (MemoryAdapter, KnowledgeGraphAdapter, RagAdapter, NoteAdapter, WordbookAdapter):
            adapter = cls(MagicMock())
            assert isinstance(adapter, KnowledgeSource), f"{cls.__name__} not KnowledgeSource"


# ═══════════════════════════════════════════════════════════════
# _coerce_items
# ═══════════════════════════════════════════════════════════════


class TestCoerceItems:
    def test_none(self):
        assert _coerce_items(None) == []

    def test_dict_with_items(self):
        assert _coerce_items({"items": [{"id": "a"}]}) == [{"id": "a"}]

    def test_dict_without_items(self):
        assert _coerce_items({"foo": "bar"}) == []

    def test_list_filters_non_dict(self):
        assert _coerce_items([{"id": "a"}, "junk", 42, None]) == [{"id": "a"}]

    def test_other_types(self):
        assert _coerce_items("not a list") == []
        assert _coerce_items(42) == []


# ═══════════════════════════════════════════════════════════════
# MemoryAdapter
# ═══════════════════════════════════════════════════════════════


class TestMemoryAdapter:
    def test_recall_preferred(self):
        mem = MagicMock()
        mem.recall.return_value = {"items": [{"id": "m1"}]}
        adapter = MemoryAdapter(mem)
        assert adapter.applies_to("general", "q") is True
        items = adapter.fetch("q", limit=5)
        mem.recall.assert_called_once_with("q", top_k=5)
        assert items[0]["_source"] == "memory"
        assert items[0]["_label"] == "📝 记忆"

    def test_search_fallback(self):
        mem = MagicMock(spec=["search"])
        mem.search.return_value = [{"id": "m1"}]
        adapter = MemoryAdapter(mem)
        assert adapter.applies_to("general", "q") is True
        items = adapter.fetch("q", limit=5)
        mem.search.assert_called_once_with("q", top_k=5, search_mode="hybrid")
        assert len(items) == 1

    def test_neither_method_disables(self):
        mem = MagicMock(spec=[])  # no recall, no search
        adapter = MemoryAdapter(mem)
        assert adapter.applies_to("general", "q") is False
        assert adapter.fetch("q", limit=5) == []


# ═══════════════════════════════════════════════════════════════
# KnowledgeGraphAdapter
# ═══════════════════════════════════════════════════════════════


class TestKnowledgeGraphAdapter:
    def test_search_preferred(self):
        kg = MagicMock()
        kg.search.return_value = [{"id": "k1"}]
        adapter = KnowledgeGraphAdapter(kg)
        items = adapter.fetch("q", limit=5)
        kg.search.assert_called_once_with("q")
        assert items[0]["_source"] == "knowledge"

    def test_query_fallback(self):
        kg = MagicMock(spec=["query"])
        kg.query.return_value = [{"id": "k1"}]
        adapter = KnowledgeGraphAdapter(kg)
        adapter.fetch("q", limit=5)
        kg.query.assert_called_once_with("q", limit=5)

    def test_intent_filter(self):
        adapter = KnowledgeGraphAdapter(MagicMock())
        assert adapter.applies_to("factual", "q") is True
        assert adapter.applies_to("learning", "q") is True
        assert adapter.applies_to("general", "q") is True
        assert adapter.applies_to("task", "q") is False

    def test_no_method_disables(self):
        adapter = KnowledgeGraphAdapter(MagicMock(spec=[]))
        assert adapter.applies_to("general", "q") is False


# ═══════════════════════════════════════════════════════════════
# RagAdapter
# ═══════════════════════════════════════════════════════════════


class TestRagAdapter:
    def test_method_present(self):
        rag = MagicMock()
        rag.query_knowledge_base.return_value = [{"id": "r1"}]
        adapter = RagAdapter(rag)
        items = adapter.fetch("q", limit=5)
        rag.query_knowledge_base.assert_called_once_with("q", top_k=3)
        assert items[0]["_source"] == "rag"

    def test_intent_filter(self):
        adapter = RagAdapter(MagicMock())
        assert adapter.applies_to("learning", "q") is True
        assert adapter.applies_to("general", "q") is True
        assert adapter.applies_to("factual", "q") is False
        assert adapter.applies_to("task", "q") is False

    def test_no_method_disables(self):
        adapter = RagAdapter(MagicMock(spec=[]))
        assert adapter.applies_to("learning", "q") is False


# ═══════════════════════════════════════════════════════════════
# NoteAdapter
# ═══════════════════════════════════════════════════════════════


class TestNoteAdapter:
    def test_filters_by_title_and_content(self):
        notes = MagicMock()
        notes.list_tasks.return_value = [
            {"id": "n1", "title": "Python 学习", "content": ""},
            {"id": "n2", "title": "其他", "content": "Python 实践"},
            {"id": "n3", "title": "其他", "content": "其他"},
        ]
        adapter = NoteAdapter(notes)
        items = adapter.fetch("python", limit=5)
        ids = {it["id"] for it in items}
        assert ids == {"n1", "n2"}
        for it in items:
            assert it["_source"] == "note"

    def test_intent_filter(self):
        adapter = NoteAdapter(MagicMock())
        assert adapter.applies_to("factual", "q") is True
        assert adapter.applies_to("task", "q") is True
        assert adapter.applies_to("general", "q") is True
        assert adapter.applies_to("learning", "q") is False

    def test_no_method_disables(self):
        adapter = NoteAdapter(MagicMock(spec=[]))
        assert adapter.applies_to("general", "q") is False


# ═══════════════════════════════════════════════════════════════
# WordbookAdapter
# ═══════════════════════════════════════════════════════════════


class TestWordbookAdapter:
    def test_alpha_triggers(self):
        wb = MagicMock()
        wb.search_words.return_value = [{"id": "w1"}]
        adapter = WordbookAdapter(wb)
        assert adapter.applies_to("general", "hello") is True
        items = adapter.fetch("hello", limit=5)
        wb.search_words.assert_called_once_with("hello")
        assert items[0]["_source"] == "wordbook"

    def test_pure_punctuation_skips(self):
        adapter = WordbookAdapter(MagicMock())
        assert adapter.applies_to("general", "。。。") is False

    def test_no_method_disables(self):
        adapter = WordbookAdapter(MagicMock(spec=[]))
        assert adapter.applies_to("general", "hello") is False


# ═══════════════════════════════════════════════════════════════
# UnifiedKnowledgeService 注入扩展
# ═══════════════════════════════════════════════════════════════


class TestUnifiedServiceSources:
    def test_sources_listed_in_order(self):
        svc = UnifiedKnowledgeService(
            memory_search=MagicMock(),
            knowledge_graph=MagicMock(),
        )
        keys = [s.source_key for s in svc.sources]
        assert keys == ["memory", "knowledge"]

    def test_extra_sources_appended(self):
        class FakeSource:
            label = "🧪 fake"
            source_key = "fake"

            def applies_to(self, intent, query):
                return True

            def fetch(self, query, *, limit):
                return [{"id": "x1", "_source": "fake", "_label": "🧪 fake"}]

        svc = UnifiedKnowledgeService(extra_sources=[FakeSource()])
        result = svc.query("anything")
        assert "fake" in result["sources"]
        assert result["sources"]["fake"][0]["id"] == "x1"

    def test_applies_to_exception_isolated(self):
        class BoomSource:
            label = "boom"
            source_key = "boom"

            def applies_to(self, intent, query):
                raise RuntimeError("boom")

            def fetch(self, query, *, limit):
                return []

        memory = MagicMock()
        memory.recall.return_value = {"items": [{"id": "m1"}]}
        svc = UnifiedKnowledgeService(memory_search=memory, extra_sources=[BoomSource()])
        result = svc.query("q")
        assert "boom" not in result["sources"]
        assert "memory" in result["sources"]
