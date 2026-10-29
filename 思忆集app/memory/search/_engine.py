"""Hybrid memory search engine — HybridMemorySearch main class.

Extracted from memory/search.py — HybridMemorySearch (public API, search,
graph search, sync operations).
"""

from __future__ import annotations

import logging
import os
import re as _re
from datetime import datetime
from typing import Any

from ._parsing import parse_supertag_query, parse_time_range, get_source_label, get_source_color
from ._ranking import RankingMixin
from ._filtering import FilteringMixin
from ._chroma import ChromaMixin

logger = logging.getLogger("siyi.memory_search")


class HybridMemorySearch(RankingMixin, FilteringMixin, ChromaMixin):
    """混合记忆搜索：ChromaDB 向量检索 + FTS5 BM25 关键词检索 + RRF 融合。

    用法:
        search = HybridMemorySearch(
            db_path="data/memory_fts.db",
            chroma_path="data/chroma/memories",
        )
        results = search.search("上周关于数据库的记忆")
    """

    DEFAULT_RRF_K = 60
    SEARCH_MODES = ("hybrid", "vector", "keyword")

    def __init__(
        self,
        db_path: str | os.PathLike,
        chroma_path: str | os.PathLike | None = None,
        embedding_function: Any = None,
        chroma_collection_name: str = "memory_entries",
    ):
        from memory.local_index_engine import LocalIndexEngine

        self._local_index = LocalIndexEngine(db_path)
        self._chroma_path = chroma_path
        self._chroma_collection_name = chroma_collection_name
        self._chroma_collection: Any = None
        self._chroma_client: Any = None
        self._embedding_function = embedding_function
        # 启动优化：延迟初始化 ChromaDB，避免构造 HybridMemorySearch 时即创建
        # PersistentClient，也避免在模块顶层 import chromadb。向量搜索首次被
        # 调用时，才会通过 _ensure_chroma 尝试导入 chromadb 并创建客户端和
        # collection。FTS5 关键词搜索不受影响。
        self._chroma_available = bool(chroma_path)

        if chroma_path:
            # 仅创建目录，不初始化 PersistentClient
            try:
                os.makedirs(str(chroma_path), exist_ok=True)
            except Exception as exc:
                logger.warning("ChromaDB path creation failed: %s", exc)
                self._chroma_available = False
        else:
            logger.info("ChromaDB path not set, vector search disabled")

    def _ensure_chroma(self) -> bool:
        """按需初始化 ChromaDB 客户端和 collection。

        返回 True 表示可用。初始化成功后缓存结果，失败时禁用向量搜索。
        """
        if not self._chroma_available:
            return False
        if self._chroma_collection is not None:
            return True
        if self._chroma_path is None:
            return False
        try:
            import chromadb

            self._chroma_client = chromadb.PersistentClient(path=str(self._chroma_path))
            self._chroma_collection = self._chroma_client.get_or_create_collection(
                name=self._chroma_collection_name,
                embedding_function=self._embedding_function,
                metadata={"hnsw:space": "cosine"},
            )
            logger.info(
                "ChromaDB memory collection '%s' ready at %s",
                self._chroma_collection_name,
                self._chroma_path,
            )
            return True
        except ImportError:
            logger.info("chromadb not installed, vector search disabled")
            self._chroma_available = False
            return False
        except Exception as exc:
            logger.warning("ChromaDB lazy init failed, vector search disabled: %s", exc)
            self._chroma_available = False
            return False

    # ── 公开接口 ──

    def find_related(
        self,
        text: str,
        limit: int = 5,
        min_score: float = 0.3,
    ) -> list[dict[str, Any]]:
        """根据文本内容查找相关记忆（记忆浮现）。

        返回简化列表，适合前端侧栏非模态展示：
        - 仅返回 vector search 结果（语义匹配）
        - 结果按相关性降序排列
        - 每条含 title + summary + source_type + id
        """
        if not text or not text.strip():
            return []

        # 用向量搜索找语义相关内容
        self._ensure_chroma()
        vector_items, _ = self._vector_search(text, top_k=max(limit * 2, 10))

        if not vector_items:
            return []

        seen = set()
        results: list[dict[str, Any]] = []
        for item in vector_items:
            item_id = item.get("entry_id") or item.get("id", "")
            if item_id in seen:
                continue
            seen.add(item_id)

            score = float(item.get("score", 0) or 0)
            if score < min_score:
                continue

            content = item.get("content", "") or item.get("text", "") or ""
            results.append(
                {
                    "id": item_id,
                    "title": item.get("title", "") or content[:60],
                    "summary": content[:200],
                    "source_type": item.get("source_type", ""),
                    "source_label": get_source_label(item.get("source_type", "")),
                    "score": round(score, 3),
                    "created_at": item.get("created_at", ""),
                }
            )

            if len(results) >= limit:
                break

        return results

    def search(
        self,
        query: str,
        top_k: int = 10,
        search_mode: str = "hybrid",
        source_filters: list[str] | None = None,
        time_range: tuple[datetime | None, datetime | None] | None = None,
        supertag_filters: dict[str, Any] | None = None,
        tag_service=None,
    ) -> dict[str, Any]:
        """执行混合检索。"""
        if search_mode not in self.SEARCH_MODES:
            search_mode = "hybrid"

        nl_supertag_type, nl_supertag_filters, nl_cleaned = parse_supertag_query(query)

        merged_supertag_filters: dict[str, Any] = {}
        if nl_supertag_type:
            merged_supertag_filters["supertag"] = nl_supertag_type
        if nl_supertag_filters:
            merged_supertag_filters.update(nl_supertag_filters)
        if supertag_filters:
            merged_supertag_filters.update(supertag_filters)

        effective_search_query = (supertag_filters or {}).get("_query_override") or nl_cleaned or query
        start_time, end_time, effective_query = parse_time_range(effective_search_query)

        if time_range is not None:
            start_time, end_time = time_range

        if not effective_query and search_mode != "vector":
            all_results, sources_used = self._keyword_search(
                effective_query or "",
                top_k=top_k,
            )
        elif search_mode == "keyword":
            all_results, sources_used = self._keyword_search(
                effective_query,
                top_k=top_k,
            )
        elif search_mode == "vector":
            self._ensure_chroma()
            all_results, sources_used = self._vector_search(
                effective_query,
                top_k=top_k,
            )
        else:
            all_results, sources_used = self._hybrid_search(
                effective_query,
                top_k=top_k,
            )

        if source_filters:
            allowed = {s.strip() for s in source_filters if s.strip()}
            all_results = [item for item in all_results if item.get("source_type") in allowed]

        if start_time or end_time:
            all_results = self._filter_by_time(all_results, start_time, end_time)

        if merged_supertag_filters:
            all_results = self._filter_by_supertag(
                all_results,
                merged_supertag_filters,
                tag_service=tag_service,
            )

        items = []
        for item in all_results[:top_k]:
            enriched = self._add_source_label(item)
            if tag_service is not None and merged_supertag_filters:
                enriched = tag_service.enrich_entry_with_supertag_data(enriched)
            items.append(enriched)

        src_types = list(dict.fromkeys(item.get("source_type", "") for item in items))

        result = {
            "query": query,
            "effective_query": effective_query,
            "mode": search_mode,
            "count": len(items),
            "items": items,
            "sources": src_types,
            "time_range": ({"start": start_time.isoformat() if start_time else None, "end": end_time.isoformat() if end_time else None}),
        }
        if merged_supertag_filters:
            result["supertag_filters"] = merged_supertag_filters
        return result

    def sync_entries(self, entries: list[dict[str, Any]]) -> int:
        """全量同步记忆条目到 FTS5 和 ChromaDB 索引。"""
        count = self._local_index.sync_entries(entries)
        if self._chroma_available:
            self._sync_chromadb(entries)
        return count

    def add_entry(self, entry: dict[str, Any]) -> None:
        """添加单条记忆条目到索引。"""
        self._local_index.add_entry(entry)
        if self._chroma_available:
            self._add_to_chromadb(entry)

    def update_entry(self, entry_id: str, entry: dict[str, Any]) -> None:
        """更新索引中的记忆条目。"""
        self._local_index.update_entry(entry_id, entry)
        if self._chroma_available:
            self._update_in_chromadb(entry_id, entry)

    def delete_entry(self, entry_id: str) -> None:
        """从索引中删除记忆条目。"""
        self._local_index.delete_entry(entry_id)
        if self._chroma_available and self._ensure_chroma():
            try:
                self._chroma_collection.delete(ids=[entry_id])
            except Exception as exc:
                logger.warning("ChromaDB delete failed: %s", exc, exc_info=True)

    def get_entry_count(self) -> int:
        """返回索引中的条目总数。"""
        return self._local_index.get_entry_count()

    def close(self) -> None:
        """释放资源，关闭索引引擎和 ChromaDB 连接。"""
        self._local_index.close()
        if self._chroma_client is not None:
            try:
                self._chroma_client.close()
            except Exception as e:
                logger.warning("Error closing ChromaDB client in memory search: %s", e)

    # ── 知识图谱增强搜索 ──

    def graph_search(
        self,
        query: str,
        kg_store=None,
        kg_extractor=None,
        top_k: int = 10,
    ) -> dict[str, Any]:
        """知识图谱增强搜索：先查图谱实体，再关联到记忆条目。"""
        if kg_store is None:
            return self.search(query, top_k=top_k, search_mode="hybrid")

        candidate_words = set(_re.findall(r"[一-鿿]{2,6}", query))
        matched_entities = []
        for word in candidate_words:
            # PD-5b（2026-09-18）：图谱增强搜索属**召回**场景（滑窗词 → 相关
            # 实体），显式开启子串折叠；find_by_name 默认已收紧为规范化精确。
            matches = kg_store.find_by_name(word, allow_substring=True)
            matched_entities.extend(matches)

        seen_ids = set()
        unique_entities = []
        for e in matched_entities:
            eid = e.get("id", "")
            if eid not in seen_ids:
                seen_ids.add(eid)
                unique_entities.append(e)

        entity_paths = []
        traversal_data = []
        for ent in unique_entities[:5]:
            eid = ent.get("id", "")
            paths = kg_store.traverse(eid, max_hops=2)
            entity_paths.extend(paths)
            for path in paths:
                for item in path:
                    if isinstance(item, dict) and item.get("id") != eid:
                        traversal_data.append(item)

        entity_names = [e.get("name", "") for e in unique_entities if e.get("name")]
        if traversal_data:
            entity_names.extend(
                e.get("name", "") for e in traversal_data if isinstance(e, dict) and e.get("name") and e.get("name") not in entity_names
            )

        augmented_query = query
        if entity_names:
            extra_context = " ".join(entity_names[:10])
            augmented_query = f"{query} {extra_context}"

        search_result = self.search(
            query=augmented_query,
            top_k=top_k,
            search_mode="hybrid",
        )

        enriched_items = []
        seen_entry_ids = set()
        for item in search_result.get("items", []):
            entry_id = item.get("entry_id") or item.get("id", "")
            if entry_id not in seen_entry_ids:
                seen_entry_ids.add(entry_id)
                enriched_items.append(item)

        entity_context = ""
        if kg_extractor is not None:
            entity_context = kg_extractor.query_for_prompt_context(query)

        result = {
            "query": query,
            "effective_query": augmented_query,
            "mode": "graph_enhanced",
            "count": len(enriched_items),
            "items": enriched_items[:top_k],
            "entities": unique_entities,
            "entity_paths": entity_paths[:5],
            "entity_context": entity_context,
            "sources": search_result.get("sources", []),
        }
        return result

    # ── 检索引擎 ──

    def _hybrid_search(
        self,
        query: str,
        top_k: int = 10,
    ) -> tuple[list[dict[str, Any]], list[str]]:
        """执行混合检索（向量 + 关键词），使用 RRF 融合。"""
        self._ensure_chroma()
        vector_results, _ = self._vector_search(query, top_k=max(20, top_k * 2))
        keyword_results, _ = self._keyword_search(query, top_k=max(20, top_k * 2))

        if not vector_results:
            return keyword_results[:top_k], []
        if not keyword_results:
            return vector_results[:top_k], []

        fused = self._rrf_fuse(
            vector_results,
            keyword_results,
            k=self.DEFAULT_RRF_K,
            top_k=top_k,
        )

        sources = list(dict.fromkeys(item.get("source_type", "") for item in fused))
        return fused, sources

    def _vector_search(
        self,
        query: str,
        top_k: int = 10,
    ) -> tuple[list[dict[str, Any]], list[str]]:
        """ChromaDB 向量检索。"""
        if not self._chroma_available or not query.strip():
            return [], []

        try:
            results = self._chroma_collection.query(
                query_texts=[query],
                n_results=top_k,
            )
        except Exception as exc:
            logger.warning("Vector search failed, returning empty: %s", exc, exc_info=True)
            return [], []

        items = self._chroma_results_to_items(results)
        sources = list(dict.fromkeys(item.get("source_type", "") for item in items))
        return items, sources

    def _keyword_search(
        self,
        query: str,
        top_k: int = 10,
    ) -> tuple[list[dict[str, Any]], list[str]]:
        """FTS5 BM25 关键词检索。"""
        if not query.strip():
            return [], []

        results = self._local_index.search(query, top_k=top_k)
        sources = list(dict.fromkeys(item.get("source_type", "") for item in results))
        return results, sources

    # ── 工具方法 ──

    @staticmethod
    def _add_source_label(item: dict[str, Any]) -> dict[str, Any]:
        """为条目添加来源标签。"""
        source_type = str(item.get("source_type") or "")
        item["source_label"] = get_source_label(source_type)
        item["source_color"] = get_source_color(source_type)
        return item
