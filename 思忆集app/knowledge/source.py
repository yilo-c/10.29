"""KnowledgeSource Protocol + 内置 Adapter（P2-5 批次 A）。

把 ``UnifiedKnowledgeService`` 中 5 处 ``hasattr`` 能力探测收敛为统一接口：

    ┌──────────────────────────────────┐
    │   UnifiedKnowledgeService.query  │
    └──────────────┬───────────────────┘
                   │ 遍历已注册 sources
                   ▼
    ┌──────────────────────────────────┐
    │   KnowledgeSource (Protocol)     │
    │     label: str                   │
    │     source_key: str              │
    │     applies_to(intent, query)    │
    │     fetch(query, limit)          │
    └──────────────┬───────────────────┘
                   ▲
       ┌───────────┴────────┬──────────────┬────────────┬────────────┐
       │                    │              │            │            │
   MemoryAdapter   KnowledgeGraphAdapter   RagAdapter  NoteAdapter  WordbookAdapter

接口约束：
* ``applies_to`` 廉价、无副作用，决定当前 intent/query 是否触发此源。
* ``fetch`` 返回已带 ``_source``/``_label`` 字段的 dict 列表；不抛异常时由调用方收集。
* 调用方负责把多个 source 的列表交给 RRF 融合。

引入 Adapter 的好处：
* ``hasattr(self._memory, "recall")`` 等鸭子类型探测在 Adapter 构造期固化为函数引用，
  主流程不再每次 query 都做能力判断。
* 新增数据源（如 web_archive、emails）只需实现 Protocol 并注册到 service。
"""
from __future__ import annotations

import logging
from typing import Any, Callable, Optional, Protocol, runtime_checkable

logger = logging.getLogger("siyi.knowledge.source")


# ═══════════════════════════════════════════════════════════════════
# Protocol
# ═══════════════════════════════════════════════════════════════════


@runtime_checkable
class KnowledgeSource(Protocol):
    """统一知识来源接口。实现者通常包装一个底层服务对象。"""

    label: str
    source_key: str

    def applies_to(self, intent: str, query: str) -> bool: ...

    def fetch(self, query: str, *, limit: int) -> list[dict[str, Any]]: ...


# ═══════════════════════════════════════════════════════════════════
# 内置 Adapter
# ═══════════════════════════════════════════════════════════════════


def _coerce_items(raw: Any) -> list[dict[str, Any]]:
    """统一把 dict-with-items / list / None 归一化为 list[dict]。"""
    if raw is None:
        return []
    if isinstance(raw, dict):
        items = raw.get("items", [])
        return list(items) if isinstance(items, list) else []
    if isinstance(raw, list):
        return [it for it in raw if isinstance(it, dict)]
    return []


def _tag(items: list[dict[str, Any]], source_key: str, label: str) -> list[dict[str, Any]]:
    for item in items:
        item["_source"] = source_key
        item["_label"] = label
    return items


class MemoryAdapter:
    """memory_search：始终触发；优先 ``recall``，回退 ``search``。"""

    label = "📝 记忆"
    source_key = "memory"

    def __init__(self, memory: Any) -> None:
        self._memory = memory
        # 一次性探测，固化函数引用，避免 query 时反复 hasattr
        if hasattr(memory, "recall"):
            self._call: Optional[Callable[[str, int], Any]] = lambda q, k: memory.recall(q, top_k=k)
        elif hasattr(memory, "search"):
            self._call = lambda q, k: memory.search(q, top_k=k, search_mode="hybrid")
        else:
            self._call = None

    def applies_to(self, intent: str, query: str) -> bool:
        return self._call is not None

    def fetch(self, query: str, *, limit: int) -> list[dict[str, Any]]:
        if self._call is None:
            return []
        return _tag(_coerce_items(self._call(query, 5)), self.source_key, self.label)


class KnowledgeGraphAdapter:
    """knowledge_graph：``factual`` / ``learning`` / ``general`` 意图；search 优先 query 回退。"""

    label = "🔗 知识"
    source_key = "knowledge"
    _INTENTS = frozenset({"factual", "learning", "general"})

    def __init__(self, kg: Any) -> None:
        self._kg = kg
        if hasattr(kg, "search"):
            self._call: Optional[Callable[[str, int], Any]] = lambda q, k: kg.search(q)
        elif hasattr(kg, "query"):
            self._call = lambda q, k: kg.query(q, limit=k)
        else:
            self._call = None

    def applies_to(self, intent: str, query: str) -> bool:
        return self._call is not None and intent in self._INTENTS

    def fetch(self, query: str, *, limit: int) -> list[dict[str, Any]]:
        if self._call is None:
            return []
        return _tag(_coerce_items(self._call(query, 5)), self.source_key, self.label)


class RagAdapter:
    """rag_pipeline：``learning`` / ``general`` 意图；要求 ``query_knowledge_base``。"""

    label = "📚 知识库"
    source_key = "rag"
    _INTENTS = frozenset({"learning", "general"})

    def __init__(self, rag: Any) -> None:
        self._rag = rag
        self._call: Optional[Callable[[str, int], Any]]
        if hasattr(rag, "query_knowledge_base"):
            self._call = lambda q, k: rag.query_knowledge_base(q, top_k=k)
        else:
            self._call = None

    def applies_to(self, intent: str, query: str) -> bool:
        return self._call is not None and intent in self._INTENTS

    def fetch(self, query: str, *, limit: int) -> list[dict[str, Any]]:
        if self._call is None:
            return []
        return _tag(_coerce_items(self._call(query, 3)), self.source_key, self.label)


class NoteAdapter:
    """note_service：``factual`` / ``task`` / ``general`` 意图；list_tasks + 标题/正文包含过滤。"""

    label = "📄 笔记"
    source_key = "note"
    _INTENTS = frozenset({"factual", "task", "general"})

    def __init__(self, notes: Any) -> None:
        self._notes = notes
        self._call: Optional[Callable[[int], Any]]
        if hasattr(notes, "list_tasks"):
            self._call = lambda k: notes.list_tasks(limit=k)
        else:
            self._call = None

    def applies_to(self, intent: str, query: str) -> bool:
        return self._call is not None and intent in self._INTENTS

    def fetch(self, query: str, *, limit: int) -> list[dict[str, Any]]:
        if self._call is None:
            return []
        raw = _coerce_items(self._call(5))
        needle = query.lower()
        filtered = [
            n for n in raw
            if needle in str(n.get("title", "")).lower()
            or needle in str(n.get("content", "")).lower()
        ]
        return _tag(filtered, self.source_key, self.label)


class WordbookAdapter:
    """wordbook_repo：query 含字母时触发；要求 ``search_words``。"""

    label = "📖 词库"
    source_key = "wordbook"

    def __init__(self, wordbook: Any) -> None:
        self._wordbook = wordbook
        self._call: Optional[Callable[[str], Any]]
        if hasattr(wordbook, "search_words"):
            self._call = lambda q: wordbook.search_words(q)
        else:
            self._call = None

    def applies_to(self, intent: str, query: str) -> bool:
        if self._call is None:
            return False
        return any(c.isalpha() for c in query)

    def fetch(self, query: str, *, limit: int) -> list[dict[str, Any]]:
        if self._call is None:
            return []
        return _tag(_coerce_items(self._call(query)), self.source_key, self.label)


class InfoItemAdapter:
    """info_item_repo：``factual`` / ``learning`` / ``general`` 意图；搜索历史资讯条目。"""

    label = "📰 资讯"
    source_key = "info"
    _INTENTS = frozenset({"factual", "learning", "general"})

    def __init__(self, repo: Any) -> None:
        self._repo = repo
        self._call: Optional[Callable[[str, int], Any]]
        if hasattr(repo, "list_items"):
            self._call = lambda q, k: repo.list_items(search=q, limit=k)
        else:
            self._call = None

    def applies_to(self, intent: str, query: str) -> bool:
        return self._call is not None and intent in self._INTENTS

    def fetch(self, query: str, *, limit: int) -> list[dict[str, Any]]:
        if self._call is None:
            return []
        items, _total = self._call(query, limit)
        return _tag(_coerce_items(items), self.source_key, self.label)


__all__ = [
    "KnowledgeSource",
    "MemoryAdapter",
    "KnowledgeGraphAdapter",
    "RagAdapter",
    "NoteAdapter",
    "WordbookAdapter",
    "InfoItemAdapter",
]
