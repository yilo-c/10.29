# -*- coding: utf-8 -*-
"""
知识库桥接层 - 为思忆集助手系统提供 RAG 能力

职责：
1. 在应用启动时初始化知识库组件
2. 为 PromptContext 提供异步知识查询接口
3. 将检索结果格式化为 LLM 上下文文本
4. 确保知识库查询不阻塞主线程
"""
from __future__ import annotations

import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Dict, List, Optional

from .cache import QueryCache
from .document_parser import DocumentParser
from .embedding_service import EmbeddingService
from .rag_pipeline import RAGPipeline
from .retriever import HybridRetriever
from .vector_store import ChromaVectorStore

logger = logging.getLogger(__name__)

# 默认持久化路径
DEFAULT_PERSIST_DIR = Path(__file__).resolve().parent.parent / "data" / "chroma"
DEFAULT_KNOWLEDGE_BASE_DIR = Path(__file__).resolve().parent.parent / "data" / "knowledge_base"


class KnowledgeBaseBridge:
    """
    知识库桥接层

    用法:
        bridge = KnowledgeBaseBridge()
        bridge.ensure_initialized()
        context = await bridge.query_for_context("什么是 RAG?")
    """

    def __init__(
        self,
        persist_directory: Optional[Path] = None,
        knowledge_base_dir: Optional[Path] = None,
        offline_embedding: bool = False,
    ):
        self._persist_directory = persist_directory or DEFAULT_PERSIST_DIR
        self._knowledge_base_dir = knowledge_base_dir or DEFAULT_KNOWLEDGE_BASE_DIR
        self._offline_embedding = offline_embedding

        self._pipeline: Optional[RAGPipeline] = None
        self._initialized = False
        self._executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="kb_bridge")

    # ── 生命周期 ──────────────────────────────────────────────────────

    def ensure_initialized(self) -> bool:
        """
        确保所有组件已初始化（线程安全，可重复调用）

        Returns:
            True 表示成功初始化，False 表示已初始化过
        """
        if self._initialized and self._pipeline is not None:
            return False

        try:
            embedding_service = EmbeddingService(offline_mode=self._offline_embedding)
            vector_store = ChromaVectorStore(persist_directory=self._persist_directory)
            document_parser = DocumentParser()

            self._pipeline = RAGPipeline(
                vector_store=vector_store,
                document_parser=document_parser,
                embedding_service=embedding_service,
                knowledge_base_dir=self._knowledge_base_dir,
            )

            self._initialized = True
            logger.info(
                "KnowledgeBaseBridge initialized (persist=%s, offline=%s)",
                self._persist_directory, self._offline_embedding
            )
            return True

        except Exception as e:
            logger.error("KnowledgeBaseBridge initialization failed: %s", e)
            self._initialized = False
            self._pipeline = None
            return False

    @property
    def is_available(self) -> bool:
        """桥接层是否可用"""
        return self._initialized and self._pipeline is not None

    @property
    def pipeline(self) -> Optional[RAGPipeline]:
        """获取 RAGPipeline 实例"""
        return self._pipeline

    # ── 同步接口 ──────────────────────────────────────────────────────

    def query_sync(
        self,
        query: str,
        n_results: int = 5,
        where: Optional[Dict[str, Any]] = None,
        use_cache: bool = True,
        alpha: float = 0.7,
    ) -> Optional[Dict[str, Any]]:
        """
        同步查询知识库（用于非 async 上下文）

        Args:
            query: 查询文本
            n_results: 返回结果数
            where: 元数据过滤
            use_cache: 是否使用缓存
            alpha: 检索混合权重

        Returns:
            query_knowledge_base 的结果，或 None（不可用/出错）
        """
        if not self.is_available:
            return None
        try:
            # RAGPipeline.query_knowledge_base 是 async，用事件循环调用
            loop = asyncio.new_event_loop()
            try:
                result = loop.run_until_complete(
                    self._pipeline.query_knowledge_base(
                        query=query, n_results=n_results,
                        where=where, use_cache=use_cache, alpha=alpha,
                    )
                )
            finally:
                loop.close()
            return result
        except Exception as e:
            logger.warning("Knowledge base sync query failed: %s", e)
            return None

    # ── 异步接口（供 PromptContext 使用） ──────────────────────────────

    async def query_for_context(
        self,
        query: str,
        n_results: int = 3,
    ) -> str:
        """
        查询知识库并返回格式化的 LLM 上下文文本

        这是供 PromptContext 使用的主要接口。
        非阻塞：在 ThreadPoolExecutor 中执行 ChromaDB 查询。

        Args:
            query: 用户查询
            n_results: 返回结果数（默认 3）

        Returns:
            格式化的上下文文本。
            知识库不可用时返回降级提示（而非空字符串），
            让 LLM 知晓知识库状态而非静默缺失。
        """
        if not self.is_available:
            return "[知识库未初始化，无法提供知识库上下文]"

        try:
            loop = asyncio.get_running_loop()
            results = await loop.run_in_executor(
                self._executor,
                self._query_sync_impl,
                query,
                n_results,
            )
            if results is None:
                return "[知识库查询异常，跳过知识库上下文]"

            context = self._pipeline.assemble_context(
                query=query,
                results=results,
                max_chunks=n_results,
            )
            if not context:
                return "[知识库未找到相关内容]"

            return context

        except Exception as e:
            logger.warning("Knowledge base query for context failed: %s", e)
            return "[知识库查询失败，已跳过]"

    async def add_text(
        self,
        content: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> int:
        """
        异步添加文本到知识库

        Returns:
            添加的 chunk 数，0 表示失败
        """
        if not self.is_available:
            return 0
        try:
            loop = asyncio.get_running_loop()
            chunk_count = await loop.run_in_executor(
                self._executor,
                self._add_text_sync,
                content,
                metadata,
            )
            return chunk_count
        except Exception as e:
            logger.warning("Add text to knowledge base failed: %s", e)
            return 0

    # ── 内部 ──────────────────────────────────────────────────────────

    def _query_sync_impl(
        self,
        query: str,
        n_results: int,
    ) -> Optional[Dict[str, Any]]:
        """同步执行查询（在 executor 中运行）"""
        if not self.is_available:
            return None
        try:
            loop = asyncio.new_event_loop()
            try:
                return loop.run_until_complete(
                    self._pipeline.query_knowledge_base(
                        query=query, n_results=n_results
                    )
                )
            finally:
                loop.close()
        except Exception as e:
            logger.debug("Sync query impl failed: %s", e)
            return None

    def _add_text_sync(
        self,
        content: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> int:
        """同步添加文本"""
        if not self.is_available:
            return 0
        try:
            loop = asyncio.new_event_loop()
            try:
                count = loop.run_until_complete(
                    self._pipeline.add_document(content=content, metadata=metadata)
                )
                return count
            finally:
                loop.close()
        except Exception as e:
            logger.debug("Sync add text failed: %s", e)
            return 0

    def close(self) -> None:
        """释放资源"""
        if self._pipeline is not None:
            try:
                self._pipeline.close()
            except Exception:
                logger.warning("failed to close embedding pipeline")
        self._executor.shutdown(wait=False)
        self._initialized = False
        self._pipeline = None
