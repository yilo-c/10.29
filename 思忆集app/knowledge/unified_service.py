"""UnifiedKnowledgeService — 统一知识查询服务。

一次提问，同时搜索记忆/知识图谱/RAG/笔记/词库。
RRF 融合排序返回合并结果。

P2-5 批次 A：5 个数据源的能力探测与触发条件由 ``knowledge.source`` 中
``KnowledgeSource`` Protocol + 5 个 Adapter 收敛；本模块只负责意图分类、
按 Adapter 列表收集结果、RRF 融合排序。
"""

from __future__ import annotations

import logging
from typing import Any

from .source import (
    InfoItemAdapter,
    KnowledgeGraphAdapter,
    KnowledgeSource,
    MemoryAdapter,
    NoteAdapter,
    RagAdapter,
    WordbookAdapter,
)

logger = logging.getLogger("siyi.knowledge.unified")

# ── 意图分类 ──

_INTENT_KEYWORDS: dict[str, list[str]] = {
    "factual": ["是什么", "什么时候", "哪里", "谁", "怎么", "为什么", "原因", "什么时间"],
    "learning": ["学习", "了解", "学一下", "怎么用", "教程", "用法", "如何"],
    "task": ["待办", "进度", "完成", "做了", "任务", "还没", "还有"],
}


def _classify_intent(query: str) -> str:
    """根据关键词快速判断意图。"""
    q = query.lower()
    for intent, keywords in _INTENT_KEYWORDS.items():
        if any(kw in q for kw in keywords):
            return intent
    return "general"


# ── RRF 融合 ──

def _rrf_fuse(
    results: list[list[dict[str, Any]]],
    *,
    k: int = 60,
    limit: int = 15,
) -> list[dict[str, Any]]:
    """Reciprocal Rank Fusion: 合并多个排序结果。"""
    scores: dict[str, float] = {}
    items: dict[str, dict[str, Any]] = {}

    for rank_list in results:
        for rank, item in enumerate(rank_list):
            item_id = item.get("id") or str(hash(str(item.get("title", "") + str(item.get("content", "")[:100]))))
            if item_id not in items:
                items[item_id] = {**item, "_source": item.get("_source", "unknown")}
            scores[item_id] = scores.get(item_id, 0.0) + 1.0 / (k + rank + 1)

    sorted_ids = sorted(scores, key=scores.get, reverse=True)[:limit]
    return [items[i] for i in sorted_ids]


class UnifiedKnowledgeService:
    """统一知识查询：整合记忆/知识图谱/RAG/笔记/词库。

    构造时把传入的依赖逐一包装成 ``KnowledgeSource`` Adapter，能力探测
    （``hasattr`` 等鸭子类型判断）在 Adapter 构造期一次性完成；``query``
    主流程只按列表顺序遍历，无任何运行时类型分支。
    """

    def __init__(
        self,
        memory_search=None,
        knowledge_graph=None,
        rag_pipeline=None,
        note_service=None,
        wordbook_repo=None,
        info_item_repo=None,
        *,
        extra_sources: list[KnowledgeSource] | None = None,
    ):
        self._sources: list[KnowledgeSource] = []
        if memory_search is not None:
            self._sources.append(MemoryAdapter(memory_search))
        if knowledge_graph is not None:
            self._sources.append(KnowledgeGraphAdapter(knowledge_graph))
        if rag_pipeline is not None:
            self._sources.append(RagAdapter(rag_pipeline))
        if note_service is not None:
            self._sources.append(NoteAdapter(note_service))
        if wordbook_repo is not None:
            self._sources.append(WordbookAdapter(wordbook_repo))
        if info_item_repo is not None:
            self._sources.append(InfoItemAdapter(info_item_repo))
        if extra_sources:
            self._sources.extend(extra_sources)

    @property
    def sources(self) -> list[KnowledgeSource]:
        """已注册的知识源（顺序敏感）。"""
        return list(self._sources)

    def query(
        self,
        text: str,
        limit: int = 15,
    ) -> dict[str, Any]:
        """统一查询，返回融合结果 + 各来源明细。"""
        intent = _classify_intent(text)
        logger.info("unified_query: '%s' intent=%s", text[:60], intent)

        collected: list[tuple[str, list[dict[str, Any]]]] = []
        for source in self._sources:
            try:
                if not source.applies_to(intent, text):
                    continue
            except Exception:
                logger.debug("KnowledgeSource.applies_to failed: %s", source.source_key, exc_info=True)
                continue
            try:
                items = source.fetch(text, limit=limit)
            except Exception as exc:
                logger.debug("%s search failed: %s", source.source_key, exc)
                continue
            collected.append((source.source_key, items))

        all_lists = [items for _, items in collected]
        fused = _rrf_fuse(all_lists, limit=limit)

        return {
            "intent": intent,
            "results": fused,
            "sources": {name: items[:5] for name, items in collected},
            "total": len(fused),
        }
