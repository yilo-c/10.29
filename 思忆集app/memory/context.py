from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING, Any

from assistant.tag_service import AssistantTagService
from assistant.user_profile import UserProfileService
from knowledge.graph_extractor import KnowledgeGraphExtractor
from knowledge.graph_store import KnowledgeGraphStore
from data.local_storage import LocalDataStore

from .extractor import MemoryExtractor
from .index import AssistantMemoryIndexService
from .linker import MemoryLinker
from .scheduler import MemoryScheduler
from .structured_store import StructuredMemoryStore

if TYPE_CHECKING:
    from ai.client.base import OpenAICompatibleClient
    from repositories import (
        PersonalInboxRepository,
        PersonalNoteRepository,
        PersonalTaskRepository,
        SettingsRepository,
    )
    from services.hybrid_search_service import HybridSearchService

logger = logging.getLogger("siyi.memory_context")


class MemoryContext:
    """记忆与知识上下文锛氱?鐞嗚?忆提鍙栥€佺储寮曘€佸叧鑱斻€佽皟度和知识图谱。?

    鐢?AppContext 组合使用，保持属性向后兼瀹广€?
    """

    def __init__(
        self,
        data_store: LocalDataStore,
        data_base_dir: str | None,
        ai_client: OpenAICompatibleClient,
        settings_repository: SettingsRepository,
        user_profile_service: UserProfileService,
        note_repository: PersonalNoteRepository,
        task_repository: PersonalTaskRepository,
        inbox_repository: PersonalInboxRepository,
        info_item_repository=None,
        hybrid_search_service: HybridSearchService | None = None,
        ai_text_service=None,
    ) -> None:
        """P2-26: 拆出按域聚合的私有构造步骤，原入参保留兼容。"""
        from app_runtime.service_registry import service_registry

        # R2-C1：显式 data_base_dir 优先；未传时 honor SIYI_DATA_DIR（隔离实例），
        # 缺省回落相对路径 "data"（env 未设时行为不变）。
        base_dir = str(data_base_dir or os.environ.get("SIYI_DATA_DIR", "").strip() or "data")

        # ① 混合搜索 — 路径解析 + 索引服务
        self._init_hybrid_search(
            base_dir=base_dir,
            hybrid_search_service=hybrid_search_service,
            service_registry=service_registry,
        )

        # ② 索引/标签
        self._init_index_and_tags(
            data_store=data_store,
            settings_repository=settings_repository,
            user_profile_service=user_profile_service,
            note_repository=note_repository,
            task_repository=task_repository,
            inbox_repository=inbox_repository,
            info_item_repository=info_item_repository,
            service_registry=service_registry,
        )

        # ③ 知识图谱（图存储 + 抽取器）
        self._init_knowledge_graph(
            base_dir=base_dir,
            ai_client=ai_client,
            service_registry=service_registry,
        )

        # ④ 记忆抽取 / 调度 / 关联
        self._init_memory_services(
            ai_client=ai_client,
            data_store=data_store,
            service_registry=service_registry,
            ai_text_service=ai_text_service,
        )

        # ⑤ Structured memory + EventBus 订阅
        self._init_structured_store(
            data_store=data_store,
            service_registry=service_registry,
        )

    # ── 私有构造步骤（P2-26） ─────────────────────────────

    def _init_hybrid_search(
        self,
        *,
        base_dir: str,
        hybrid_search_service: HybridSearchService | None,
        service_registry,
    ) -> None:
        from .search import HybridMemorySearch

        fts_db_path = os.path.join(base_dir, "memory_fts.db")
        chroma_path = os.path.join(base_dir, "chroma", "memories")

        self._hybrid_memory_search = HybridMemorySearch(
            db_path=fts_db_path,
            chroma_path=chroma_path,
        )
        self._hybrid_search_service = hybrid_search_service
        if self._hybrid_search_service is not None:
            service_registry.register(type(self._hybrid_search_service), self._hybrid_search_service)

    def _init_index_and_tags(
        self,
        *,
        data_store: LocalDataStore,
        settings_repository: SettingsRepository,
        user_profile_service: UserProfileService,
        note_repository: PersonalNoteRepository,
        task_repository: PersonalTaskRepository,
        inbox_repository: PersonalInboxRepository,
        info_item_repository=None,
        service_registry,
    ) -> None:
        self.memory_index_service = AssistantMemoryIndexService(
            data_store=data_store,
            settings_repository=settings_repository,
            user_profile_service=user_profile_service,
            note_repository=note_repository,
            task_repository=task_repository,
            inbox_repository=inbox_repository,
            info_item_repository=info_item_repository,
            memory_search=self._hybrid_search_service,
        )
        service_registry.register(AssistantMemoryIndexService, self.memory_index_service)
        self.tag_service = AssistantTagService(data_store=data_store)
        service_registry.register(AssistantTagService, self.tag_service)

    def _init_knowledge_graph(
        self,
        *,
        base_dir: str,
        ai_client: OpenAICompatibleClient,
        service_registry,
    ) -> None:
        self.knowledge_graph_store = KnowledgeGraphStore(
            path=os.path.join(base_dir, "knowledge_graph.json"),
        )
        service_registry.register(KnowledgeGraphStore, self.knowledge_graph_store)
        self.knowledge_graph_extractor = KnowledgeGraphExtractor(
            graph_store=self.knowledge_graph_store,
            ai_client=ai_client,
        )
        service_registry.register(KnowledgeGraphExtractor, self.knowledge_graph_extractor)

    def _init_memory_services(
        self,
        *,
        ai_client: OpenAICompatibleClient,
        data_store: LocalDataStore,
        service_registry,
        ai_text_service=None,
    ) -> None:
        self.memory_extractor = MemoryExtractor(
            ai_client=ai_client,
            memory_index_service=self.memory_index_service,
            knowledge_graph_extractor=self.knowledge_graph_extractor,
            ai_text_service=ai_text_service,
        )
        service_registry.register(MemoryExtractor, self.memory_extractor)
        self.memory_scheduler = MemoryScheduler(data_store=data_store)
        service_registry.register(MemoryScheduler, self.memory_scheduler)
        self.memory_linker = MemoryLinker(data_store=data_store)
        service_registry.register(MemoryLinker, self.memory_linker)

    def _init_structured_store(
        self,
        *,
        data_store: LocalDataStore,
        service_registry,
    ) -> None:
        from core.events import event_bus

        self.structured_memory_store = StructuredMemoryStore(
            data_store=data_store,
            event_bus=event_bus,
        )
        service_registry.register(StructuredMemoryStore, self.structured_memory_store)
        event_bus.on("supertag.created", self._on_supertag_event)
        event_bus.on("supertag.updated", self._on_supertag_event)
        event_bus.on("supertag.deleted", self._on_supertag_event)

        logger.info("StructuredMemoryStore initialized and registered")

    # 鈹€鈹€ Supertag event handler 鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€

    def _on_supertag_event(self, event) -> None:
        """Handle supertag events from EventBus (logging / future reactions)."""
        logger.debug(
            "Supertag event: %s | payload: %s",
            event.event_type,
            {k: v for k, v in event.payload.items() if k != "password"},
        )

    # 鈹€鈹€ 记忆提取 鈹€鈹€

    def extract_memories_from_conversation(self, messages: list[dict[str, Any]]) -> Any:
        from core.error_grading import logger as eg_logger

        result = self.memory_extractor.extract(messages)
        if result and result.auto_write:
            try:
                import json as _json

                # Store via memory_index_service rather than directly
                for m in result.auto_write:
                    self.memory_index_service.index_memory(m.to_dict())
            except Exception as exc:
                logger.warning("保存鑷?姩提取记忆失败: %s", exc, exc_info=True)
        return result

    # 鈹€鈹€ 间隔閲嶅?调度 鈹€鈹€

    def schedule_memory_review(self, memory_id: str, content: str, memory_type: str = "factual", tags: list[str] | None = None):
        return self.memory_scheduler.add_memory(memory_id, content, memory_type, tags)

    def get_due_memories(self, limit: int = 20):
        return self.memory_scheduler.get_due_memories(limit)

    def review_memory(self, memory_id: str, quality: int):
        return self.memory_scheduler.review(memory_id, quality)

    def get_daily_review(self, limit: int = 10):
        return self.memory_scheduler.get_daily_review(limit)

    def get_memory_scheduler_stats(self):
        return self.memory_scheduler.get_stats()

    def quick_review_memory(self, memory_id: str, remembered: bool):
        return self.memory_scheduler.quick_review(memory_id, remembered)

    def get_on_this_day(self, limit: int = 20):
        return self.memory_scheduler.get_on_this_day(limit)

    def get_memory_schedule(self, memory_id: str):
        return self.memory_scheduler.get_memory(memory_id)

    def load_review_settings(self):
        return self.memory_scheduler.load_settings()

    def save_review_settings(self, settings):
        return self.memory_scheduler.save_settings(settings)

    def get_review_event(self):
        return self.memory_scheduler.to_review_event()

    # 鈹€鈹€ 记忆关联 鈹€鈹€

    def link_memory(self, memory_entry: dict, existing_memories: list[dict] | None = None):
        if existing_memories is None:
            existing = self.memory_index_service.recall("", top_k=50)
            existing_memories = existing.get("items", [])
        return self.memory_linker.link(memory_entry, existing_memories)

    def find_duplicate_memories(self, content: str, threshold: float = 0.85):
        existing = self.memory_index_service.recall("", top_k=50)
        return self.memory_linker.find_duplicates(
            content,
            existing.get("items", []),
            threshold,
        )

    def get_linked_memories(self, memory_id: str, link_type: str | None = None):
        return self.memory_linker.get_linked_memories(memory_id, link_type)

    def get_memory_backlinks(self, memory_id: str, limit: int = 20):
        return self.memory_linker.get_backlinks(memory_id, limit)

    def get_memory_link_stats(self):
        return self.memory_linker.get_link_stats()

    def cleanup_memory_links(self, memory_id: str):
        return self.memory_linker.cleanup_on_delete(memory_id)

    def get_weak_link_extensions(self, content: str, top_k: int = 5):
        existing = self.memory_index_service.recall("", top_k=50)
        return self.memory_linker.get_weak_extension_results(
            content,
            existing.get("items", []),
            top_k,
        )

    def record_memory_co_access(self, memory_id_a: str, memory_id_b: str):
        return self.memory_linker.record_co_access(memory_id_a, memory_id_b)

    def get_memory_co_access_count(self, memory_id_a: str, memory_id_b: str):
        return self.memory_linker.get_co_access_count(memory_id_a, memory_id_b)

    def reindex_memory_links(self, memory_id: str, new_content: str):
        existing = self.memory_index_service.recall("", top_k=50)
        return self.memory_linker.reindex_memory(
            memory_id,
            new_content,
            existing.get("items", []),
        )

    def merge_memory_link_networks(self, keep_id: str, remove_id: str):
        return self.memory_linker.merge_link_networks(keep_id, remove_id)

    def get_linked_memories_with_detail(self, memory_id: str, link_type: str | None = None):
        existing = self.memory_index_service.recall("", top_k=200)
        items = existing.get("items", [])
        lookup = {}
        for item in items:
            eid = item.get("entry_id") or item.get("id", "")
            if eid:
                lookup[eid] = item
        return self.memory_linker.get_linked_memories_with_details(
            memory_id,
            lookup,
            link_type,
        )

    def periodic_recalculate_links(self, max_pairs: int = 200):
        existing = self.memory_index_service.recall("", top_k=200)
        items = existing.get("items", [])
        return self.memory_linker.periodic_recalculate(items, max_pairs=max_pairs)

    def close(self) -> None:
        """Close the memory context manager and cleanup."""

        try:
            from core.events import event_bus

            event_bus.off("supertag.created", self._on_supertag_event)
            event_bus.off("supertag.updated", self._on_supertag_event)
            event_bus.off("supertag.deleted", self._on_supertag_event)
        except Exception:
            pass
        if hasattr(self, "_hybrid_memory_search") and self._hybrid_memory_search is not None:
            try:
                self._hybrid_memory_search.close()
            except Exception:
                logger.debug("hybrid memory search close skipped", exc_info=True)
