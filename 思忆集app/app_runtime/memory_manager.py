"""记忆系统域：封装 MemoryContext + 所有记忆操作。

P1-17 瘦身：删除 25+ 个 ``def x(...): return self._memory.x(...)`` 形式的
纯透传方法，改由 ``__getattr__`` 自动转发到内部 :class:`MemoryContext`。

保留的真实逻辑：
- ``__init__`` / ``_build_hybrid_search_service`` — 构造与连线
- ``update_ai_client`` — 同步刷新若干嵌套 extractor 引用
- ``close`` — 资源清理 + behavior extractor 收尾
- ``structured_memory_store`` 属性 — 文档化高频访问点
- ``get_supertag_schemas`` 等 5 个 supertag 方法 — 转发到 ``self.tag_service``
  而非 ``self._memory``，无法被 ``__getattr__`` 处理。
"""

from __future__ import annotations

import logging

from memory.context import MemoryContext
from memory.behavior_extractor import BehaviorMemoryExtractor
from memory.migration import run_migration as run_memory_migration

logger = logging.getLogger("siyi.memory_manager")


class MemoryManager:
    """管理记忆系统的生命周期和所有记忆操作。

    封装 :class:`MemoryContext` 和相关服务。除少数有真实逻辑的方法外，
    所有记忆操作（``extract_memories_from_conversation`` / ``review_memory`` /
    ``link_memory`` 等）通过 ``__getattr__`` 自动转发到 ``self._memory``，
    避免大量重复样板。
    """

    def __init__(
        self,
        data_store,
        data_base_dir: str | None,
        ai_client,
        settings_repository,
        user_profile_service,
        note_repository,
        task_repository,
        inbox_repository,
        behavior_logger,
        info_item_repository=None,
        ai_text_service=None,
    ) -> None:
        hybrid_search = self._build_hybrid_search_service(data_base_dir)
        self._memory = MemoryContext(
            data_store=data_store,
            data_base_dir=data_base_dir,
            ai_client=ai_client,
            settings_repository=settings_repository,
            user_profile_service=user_profile_service,
            note_repository=note_repository,
            task_repository=task_repository,
            inbox_repository=inbox_repository,
            info_item_repository=info_item_repository,
            hybrid_search_service=hybrid_search,
            ai_text_service=ai_text_service,
        )
        self.memory_index_service = self._memory.memory_index_service
        self.tag_service = self._memory.tag_service
        self.knowledge_graph_store = self._memory.knowledge_graph_store
        self.knowledge_graph_extractor = self._memory.knowledge_graph_extractor
        self.memory_extractor = self._memory.memory_extractor
        self.memory_scheduler = self._memory.memory_scheduler
        self.memory_linker = self._memory.memory_linker

        self.behavior_memory_extractor = BehaviorMemoryExtractor(
            behavior_logger=behavior_logger,
            memory_scheduler=self.memory_scheduler,
            memory_index_service=self.memory_index_service,
            data_store=data_store,
        )

        # 一次性数据迁移
        try:
            migration_result = run_memory_migration(
                data_store=data_store,
                user_profile_service=user_profile_service,
                memory_index_service=self.memory_index_service,
                data_dir=data_store.base_dir,
            )
            if migration_result.get("files_migrated"):
                logger.info(
                    "Memory migration: %s",
                    ", ".join(migration_result["files_migrated"]),
                )
        except Exception as exc:
            logger.warning("Memory migration failed (non-blocking): %s", exc)

    @staticmethod
    def _build_hybrid_search_service(data_base_dir: str | None):
        """构建混合搜索服务。

        启动优化：将 memory.search 和 hybrid_search_service 的导入延迟到本方法
        内部执行，避免 MemoryManager 模块加载时即触发 chromadb 等重型依赖的导入。
        HybridMemorySearch 本身也已实现 ChromaDB 延迟初始化，进一步减少启动阻塞。
        """
        import os

        from memory.search import HybridMemorySearch
        from services.hybrid_search_service import HybridSearchService, SearchConfig

        fts_db_path = os.path.join(data_base_dir or "data", "memory_fts.db")
        chroma_path = os.path.join(data_base_dir or "data", "chroma", "memories")
        memory_search = HybridMemorySearch(db_path=fts_db_path, chroma_path=chroma_path)
        return HybridSearchService(
            memory_search=memory_search,
            config=SearchConfig(alpha=0.7, rrf_k=60, top_k=10, enable_event=True),
        )

    def __getattr__(self, name: str):
        """未在本类显式定义的属性/方法自动转发到内部 ``MemoryContext``。

        P1-17：取代 25+ 个 ``def x(...): return self._memory.x(...)`` 样板。
        私有属性、dunder、以及 ``_memory`` 未就绪时拒绝转发，避免初始化期的
        无限递归与误判。
        """
        if name.startswith("_"):
            raise AttributeError(name)
        memory = self.__dict__.get("_memory")
        if memory is None:
            raise AttributeError(name)
        return getattr(memory, name)

    def update_ai_client(self, ai_client) -> None:
        """更新 MemoryContext 中的 AI 客户端引用。"""
        self._memory.ai_client = ai_client  # type: ignore[attr-defined]
        if hasattr(self, "knowledge_graph_extractor") and hasattr(self.knowledge_graph_extractor, "ai_client"):
            self.knowledge_graph_extractor.ai_client = ai_client
        if hasattr(self, "memory_extractor") and hasattr(self.memory_extractor, "ai_client"):
            self.memory_extractor.ai_client = ai_client

    @property
    def structured_memory_store(self):
        """StructuredMemoryStore 实例（supertag 条目的 CRUD 存储）。"""
        return self._memory.structured_memory_store

    # ── 超级标签（走 tag_service，不在 MemoryContext 暴露） ──

    def get_supertag_schemas(self) -> dict:
        return self.tag_service.schema_registry.list_schemas()

    def get_supertag_schema(self, supertag_type: str) -> dict | None:
        return self.tag_service.schema_registry.get_schema(supertag_type)

    def register_supertag_schema(self, schema: dict) -> dict:
        return self.tag_service.schema_registry.register_schema(schema)

    def set_entry_supertag_data(
        self, entry_id: str, supertag_type: str, field_data: dict
    ) -> dict:
        return self.tag_service.set_structured_data(entry_id, supertag_type, field_data)

    def get_entry_supertag_data(self, entry_id: str) -> dict:
        return self.tag_service.get_structured_data(entry_id)

    # ── 生命周期 ──

    def close(self) -> None:
        """清理记忆系统资源。"""
        try:
            self._memory.close()
        except Exception:
            logger.debug("memory context close skipped", exc_info=True)
        try:
            self.behavior_memory_extractor.extract(limit=200)
        except Exception:
            logger.debug("behavior memory extractor final pass skipped", exc_info=True)
