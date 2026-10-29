# -*- coding: utf-8 -*-
"""
RAG 检索增强生成管道 - 核心编排层

职责：文档摄入 → 混合检索 → 上下文组装 → (输出给 LLM)
"""
from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from .cache import QueryCache
from .document_parser import DocumentParser
from .embedding_service import EmbeddingService
from .retriever import HybridRetriever
from .vector_store import ChromaVectorStore

logger = logging.getLogger(__name__)


class RAGPipeline:
    """RAG 检索增强生成管道"""

    # 默认检索参数（设计规范 Phase 1）
    DEFAULT_CHUNK_TOKENS = 512
    DEFAULT_CHUNK_OVERLAP_TOKENS = 64
    DEFAULT_TOP_K = 5

    # 上下文组装参数
    MAX_CHUNKS_IN_CONTEXT = 5
    MAX_CHARS_PER_CHUNK = 1200

    def __init__(
        self,
        vector_store: ChromaVectorStore,
        document_parser: DocumentParser,
        embedding_service: EmbeddingService,
        knowledge_base_dir: Path,
    ):
        """
        Args:
            vector_store: 向量存储实例
            document_parser: 文档解析器实例
            embedding_service: 嵌入服务实例
            knowledge_base_dir: 知识库目录
        """
        self.vector_store = vector_store
        self.document_parser = document_parser
        self.embedding_service = embedding_service
        self.knowledge_base_dir = knowledge_base_dir

        # 确保知识库目录存在
        self.knowledge_base_dir.mkdir(parents=True, exist_ok=True)

        # 混合检索器
        self._retriever = HybridRetriever(vector_store)

        # 查询缓存（24h TTL）
        self._cache = QueryCache(ttl_seconds=86400)

        # 线程池用于异步操作
        self._executor = ThreadPoolExecutor(max_workers=2)

    # ══════════════════════════════════════════════════════════════════
    # 文档摄入
    # ══════════════════════════════════════════════════════════════════

    async def add_documents_from_directory(
        self,
        directory: Path,
        chunk_tokens: int = DEFAULT_CHUNK_TOKENS,
        chunk_overlap_tokens: int = DEFAULT_CHUNK_OVERLAP_TOKENS,
        recursive: bool = True,
    ) -> int:
        """
        从目录添加文档到知识库（使用 token 级分块）

        Args:
            directory: 源目录
            chunk_tokens: 每个块的 token 数（默认 512）
            chunk_overlap_tokens: 块间重叠 token 数（默认 64）
            recursive: 是否递归子目录

        Returns:
            添加的文档数量（chunk 数）
        """
        if not directory.exists():
            raise FileNotFoundError(f"Directory not found: {directory}")

        # 解析目录中的所有文档
        parsed_documents = self.document_parser.parse_directory(directory, recursive)

        documents: List[str] = []
        metadatas: List[Dict[str, Any]] = []
        ids: List[str] = []

        doc_id = 0

        for parsed_doc in parsed_documents:
            content = parsed_doc["content"]
            metadata = parsed_doc["metadata"]

            # token 级分块
            chunks = self.document_parser.chunk_by_tokens(
                content,
                chunk_tokens=chunk_tokens,
                overlap_tokens=chunk_overlap_tokens,
            )

            for chunk in chunks:
                documents.append(chunk)
                m = dict(metadata)
                # 记录分块序号，方便溯源
                m["chunk_index"] = doc_id
                metadatas.append(m)
                ids.append(f"kb_{doc_id}")
                doc_id += 1

        if documents:
            self.vector_store.add_documents(
                documents=documents,
                metadatas=metadatas,
                ids=ids,
            )
            # 让 BM25 索引在下次检索时重建
            self._retriever.mark_index_dirty()
            # 知识库更新 → 缓存失效
            self._cache.clear()
            logger.info(
                "Added %d chunks from %s to knowledge base (token params: %d/%d)",
                len(documents), directory, chunk_tokens, chunk_overlap_tokens,
            )

        return len(documents)

    async def add_document(
        self,
        content: str,
        metadata: Optional[Dict[str, Any]] = None,
        chunk_tokens: int = DEFAULT_CHUNK_TOKENS,
        chunk_overlap_tokens: int = DEFAULT_CHUNK_OVERLAP_TOKENS,
    ) -> int:
        """
        直接添加文本内容到知识库

        Args:
            content: 文本内容
            metadata: 元数据（来源、标签等）
            chunk_tokens: 每个块的 token 数
            chunk_overlap_tokens: 块间重叠 token 数

        Returns:
            添加的 chunk 数量
        """
        metadata = dict(metadata or {})
        chunks = self.document_parser.chunk_by_tokens(
            content,
            chunk_tokens=chunk_tokens,
            overlap_tokens=chunk_overlap_tokens,
        )
        from datetime import datetime
        now = datetime.now(timezone.utc).isoformat()
        metadata.setdefault("added_at", now)

        documents: List[str] = []
        metadatas: List[Dict[str, Any]] = []
        ids: List[str] = []

        for i, chunk in enumerate(chunks):
            documents.append(chunk)
            m = dict(metadata)
            m["chunk_index"] = i
            metadatas.append(m)
            ids.append(f"kb_{abs(hash(chunk))}")

        if documents:
            self.vector_store.add_documents(
                documents=documents,
                metadatas=metadatas,
                ids=ids,
            )
            self._retriever.mark_index_dirty()
            self._cache.clear()
            logger.info("Added %d chunks directly to knowledge base", len(documents))

        return len(documents)

    # ══════════════════════════════════════════════════════════════════
    # 检索
    # ══════════════════════════════════════════════════════════════════

    async def query_knowledge_base(
        self,
        query: str,
        n_results: int = DEFAULT_TOP_K,
        where: Optional[Dict[str, Any]] = None,
        use_cache: bool = True,
        alpha: float = 0.7,
    ) -> Dict[str, Any]:
        """
        知识库检索（混合检索 + 缓存）

        Args:
            query: 查询文本
            n_results: 返回结果数量
            where: 元数据过滤条件
            use_cache: 是否使用缓存（默认 True）
            alpha: 向量分数权重（0~1），0.7=向量为主，0.3=BM25

        Returns:
            {
                "documents": [...],
                "metadatas": [...],
                "distances": [...],
                "scores": [...],
                "query": "...",
                "cached": bool,
            }
        """
        # 1. 尝试缓存
        if use_cache:
            cached = self._cache.get(query)
            if cached is not None:
                cached["cached"] = True
                # 按 n_results 截取
                cached["documents"] = cached.get("documents", [])[:n_results]
                cached["metadatas"] = cached.get("metadatas", [])[:n_results]
                cached["distances"] = cached.get("distances", [])[:n_results]
                cached["scores"] = cached.get("scores", [])[:n_results]
                return cached

        # 2. 混合检索
        try:
            results = self._retriever.query(
                query_text=query,
                top_k=n_results,
                where=where,
                alpha=alpha,
            )
        except Exception as e:
            logger.error("Knowledge base query failed: %s", e)
            raise

        # 3. 组装返回结构
        output = {
            "documents": results.get("documents", []),
            "metadatas": results.get("metadatas", []),
            "distances": results.get("distances", []),
            "scores": results.get("scores", []),
            "query": query,
            "cached": False,
        }

        # 4. 写缓存
        if use_cache:
            self._cache.set(query, output)

        logger.debug(
            "Query '%s' returned %d results (cached=%s)",
            query[:60], len(output["documents"]), output["cached"]
        )
        return output

    # ══════════════════════════════════════════════════════════════════
    # 上下文组装（核心输出给 LLM）
    # ══════════════════════════════════════════════════════════════════

    def assemble_context(
        self,
        query: str,
        results: Dict[str, Any],
        max_chunks: int = MAX_CHUNKS_IN_CONTEXT,
    ) -> str:
        """
        将检索结果组装为 LLM 友好的上下文文本

        格式:
        [知识库: 文件名]
        > 内容...
        — 相关度: 0.xx

        Args:
            query: 原始查询
            results: query_knowledge_base 的返回
            max_chunks: 最多使用多少个 chunk

        Returns:
            格式化后的上下文文本（空字符串 = 无结果）
        """
        documents = results.get("documents", [])[:max_chunks]
        metadatas = results.get("metadatas", [])[:max_chunks]
        distances = results.get("distances", [])[:max_chunks]
        scores = results.get("scores", [])

        if not documents:
            return ""

        lines: List[str] = [
            "以下是从用户知识库中检索到的相关内容：",
            "",
        ]

        for i, doc in enumerate(documents):
            # 来源标签
            meta = metadatas[i] if i < len(metadatas) else {}
            source = (
                meta.get("file_name") or meta.get("source") or meta.get("file_type", "未知来源")
            )
            score = scores[i] if i < len(scores) else (1.0 - distances[i] if i < len(distances) else 0.0)

            # 截断过长 chunk
            display = doc[:self.MAX_CHARS_PER_CHUNK]
            if len(doc) > self.MAX_CHARS_PER_CHUNK:
                display += "…"

            lines.append(f"[知识库: {source}]")
            lines.append(f"> {display}")
            lines.append(f"— 相关度: {score:.3f}")
            lines.append("")

        context = "\n".join(lines)
        logger.debug(
            "Assembled context: %d chunks, %d chars", len(documents), len(context)
        )
        return context

    def format_as_source_citation(self, results: Dict[str, Any]) -> str:
        """
        格式化检索结果为来源引用列表

        用于在 LLM 回复末尾展示来源。
        """
        metadatas = results.get("metadatas", [])
        scores = results.get("scores", [])

        citations: List[str] = []
        seen_sources: set = set()

        for i, meta in enumerate(metadatas):
            source = (
                meta.get("file_name") or meta.get("source") or ""
            )
            if not source or source in seen_sources:
                continue
            seen_sources.add(source)
            score = scores[i] if i < len(scores) else 0.0
            citations.append(f"- {source} (相关度: {score:.2f})")

        return "\n".join(citations) if citations else ""

    # ══════════════════════════════════════════════════════════════════
    # 管理方法
    # ══════════════════════════════════════════════════════════════════

    def get_all_documents(self) -> List[Dict[str, Any]]:
        """获取所有文档"""
        try:
            results = self.vector_store.get_all_documents()
            documents = []
            for i in range(len(results.get("documents", []))):
                doc = {
                    "id": results["ids"][i],
                    "content": results["documents"][i],
                    "metadata": results["metadatas"][i] if results["metadatas"] else {},
                }
                documents.append(doc)
            logger.debug("Retrieved %d total documents from knowledge base", len(documents))
            return documents
        except Exception as e:
            logger.error("Failed to get all documents: %s", e)
            raise

    def clear_knowledge_base(self) -> None:
        """清空知识库"""
        try:
            results = self.vector_store.get_all_documents()
            if results.get("ids"):
                self.vector_store.delete(ids=results["ids"])
                self._retriever.mark_index_dirty()
                self._cache.clear()
                logger.info("Cleared %d documents from knowledge base", len(results["ids"]))
        except Exception as e:
            logger.error("Failed to clear knowledge base: %s", e)
            raise

    def get_cache_stats(self) -> Dict[str, Any]:
        """获取缓存统计"""
        return {
            "cache_size": self._cache.size,
            "cache_ttl_seconds": 86400,
        }

    def close(self) -> None:
        """释放资源（执行器、向量存储等）"""
        self._executor.shutdown(wait=False)
        if hasattr(self, 'vector_store') and self.vector_store is not None:
            try:
                self.vector_store.close()
            except Exception:
                logger.warning("failed to close vector store")
