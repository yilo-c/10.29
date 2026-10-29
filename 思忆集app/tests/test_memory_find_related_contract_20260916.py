# -*- coding: utf-8 -*-
"""P0 案 H 回归：memory.related_text 恒 -32603（HybridSearchService 缺 find_related 契约）。

覆盖两层修复：
1. 补契约：HybridSearchService 新增 find_related，注入 MemoryIndex 后调用不抛、
   返回与 HybridMemorySearch.find_related 一致的精简契约形状。
2. 兜降级：即便底层对象仍缺 find_related（AttributeError），MemoryIndex.find_related
   也必须落 self.recall 真降级，而不是把异常抛给上层吞成 -32603。
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from memory.index import AssistantMemoryIndexService
from services.hybrid_search_service import HybridSearchService


CONTRACT_KEYS = {"id", "title", "summary", "source_type", "source_label", "score", "created_at"}


def _memory_entries() -> list[dict]:
    now = datetime.now(timezone.utc).isoformat()
    return [
        {
            "entry_id": "note:1",
            "source_type": "note",
            "source_id": 1,
            "title": "多代理检索方案",
            "content": "记录了向量检索与 BM25 混合召回的设计要点。",
            "summary": "",
            "tags_text": "agent retrieval",
            "updated_at": now,
            "metadata": {},
        },
        {
            "entry_id": "task:2",
            "source_type": "task",
            "source_id": 2,
            "title": "整理记忆浮现链路",
            "content": "修复 memory.related_text 面板报错：装配契约缺失。",
            "summary": "",
            "tags_text": "memory",
            "updated_at": now,
            "metadata": {},
        },
    ]


class _FakeEngine:
    """最小 HybridMemorySearch 替身：只提供 find_related 所需的 search 通路。"""

    def search(self, **kwargs):
        raise AssertionError("HybridSearchService.search should normalize args")

    def get_entry_count(self):
        return 0


# ── 1. 补契约：真实 HybridSearchService 注入后 find_related 不抛且形状正确 ──


def test_find_related_with_real_hybrid_search_service(tmp_path):
    """注入 HybridSearchService 真实实例（tmp 隔离）→ MemoryIndex.find_related 不抛、契约形状。

    这正是 P0 案 H 的复现装配：memory/context.py 把 HybridSearchService 塞给
    AssistantMemoryIndexService(memory_search=...)。修复前此处必抛 AttributeError。
    """
    from memory.search._engine import HybridMemorySearch

    engine = HybridMemorySearch(db_path=tmp_path / "memory_fts.db")  # 无 chroma_path → 纯 FTS
    try:
        service = HybridSearchService(memory_search=engine, config=None)
        service.update_config(enable_event=False)

        index = AssistantMemoryIndexService(data_store=None, memory_search=service)
        # 把索引条目灌进搜索引擎（模拟 rebuild_index 后的同步）
        service.sync_entries(_memory_entries())

        # FTS5 多词按 AND 组合，用单词确保确定性命中（「记忆」出现在任务条目标题）
        results = index.find_related("记忆", limit=5)

        assert isinstance(results, list)
        for item in results:
            assert isinstance(item, dict)
            assert CONTRACT_KEYS.issubset(item.keys()), f"契约键缺失: {item}"
            assert isinstance(item["id"], str) and item["id"]
            assert isinstance(item["score"], (int, float))
            assert 0.0 <= float(item["score"]) <= 1.0
            assert isinstance(item["source_label"], str)
        # BM25 检索应能命中含「记忆」的条目
        assert results, "关键词命中的相关记忆不应为空列表"
        assert any("记忆" in (i["title"] + i["summary"]) for i in results)
        assert len(results) <= 5
    finally:
        engine.close()


def test_find_related_service_level_contract(tmp_path):
    """直接调 HybridSearchService.find_related —— 契约方法本身存在且形状一致。"""
    from memory.search._engine import HybridMemorySearch

    engine = HybridMemorySearch(db_path=tmp_path / "mem2.db")
    try:
        service = HybridSearchService(memory_search=engine)
        service.update_config(enable_event=False)
        service.sync_entries(_memory_entries())

        assert hasattr(service, "find_related"), "HybridSearchService 必须提供 find_related 契约方法"
        results = service.find_related("记忆浮现 装配", limit=3, min_score=0.0)
        assert isinstance(results, list)
        for item in results:
            assert CONTRACT_KEYS.issubset(item.keys())
    finally:
        engine.close()


def test_find_related_empty_text_returns_empty(tmp_path):
    from memory.search._engine import HybridMemorySearch

    engine = HybridMemorySearch(db_path=tmp_path / "mem3.db")
    try:
        service = HybridSearchService(memory_search=engine)
        service.update_config(enable_event=False)
        index = AssistantMemoryIndexService(data_store=None, memory_search=service)
        assert index.find_related("   ") == []
        assert service.find_related("") == []
    finally:
        engine.close()


# ── 2. 兜降级：底层对象存在但缺 find_related → 落 recall 真降级 ──


class _NoContractEngine:
    """模拟「对象存在但契约缺失」：有 search（recall 通路可用）但没有 find_related。"""

    def search(self, **kwargs):
        return {"items": [], "count": 0, "mode": "keyword"}

    def get_entry_count(self):
        return 0


def test_missing_find_related_falls_back_to_recall(monkeypatch):
    """P0 案 H 原景重现：_memory_search 缺 find_related → 必须走 self.recall 降级而非抛。"""
    store_calls = {"recall": 0}

    index = AssistantMemoryIndexService(data_store=None, memory_search=_NoContractEngine())

    def _fake_recall(query, **kwargs):
        store_calls["recall"] += 1
        return {"items": [{"id": "mem:1", "title": "降级条目", "source_type": "memory"}], "count": 1}

    monkeypatch.setattr(index, "recall", _fake_recall)

    results = index.find_related("随便查点什么", limit=3)

    assert store_calls["recall"] == 1, "find_related 缺契约时必须且只落 recall 一次"
    assert results == [{"id": "mem:1", "title": "降级条目", "source_type": "memory"}]


def test_hybrid_service_search_attributeerror_also_falls_back(monkeypatch):
    """monkeypatch HybridSearchService.find_related 抛 AttributeError → 仍落 recall 降级。"""

    service = HybridSearchService(memory_search=None)
    service.update_config(enable_event=False)

    def _boom(*args, **kwargs):
        raise AttributeError("simulated missing find_related on engine object")

    monkeypatch.setattr(service, "find_related", _boom, raising=False)
    index = AssistantMemoryIndexService(data_store=None, memory_search=service)

    def _fake_recall(query, **kwargs):
        return {"items": [{"id": "note:9", "title": "recall 兜底"}], "count": 1}

    monkeypatch.setattr(index, "recall", _fake_recall)

    results = index.find_related("任何文本", limit=2)
    assert results and results[0]["id"] == "note:9"


def test_rpc_path_no_longer_raises(tmp_path):
    """终局验证：_chat_memory.memory_related_text 同款调用链不再抛 AttributeError。"""
    from memory.search._engine import HybridMemorySearch

    engine = HybridMemorySearch(db_path=tmp_path / "mem4.db")
    try:
        service = HybridSearchService(memory_search=engine)
        service.update_config(enable_event=False)
        service.sync_entries(_memory_entries())
        index = AssistantMemoryIndexService(data_store=None, memory_search=service)

        # handler 的调用方式：position text + keyword limit（_chat_memory.py:859-863）
        out = index.find_related("明天要做什么", limit=8)
        assert isinstance(out, list)
    finally:
        engine.close()


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
