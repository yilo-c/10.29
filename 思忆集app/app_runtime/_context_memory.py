"""MemoryContextMixin — memory/supertag delegation methods.

Extracted from AppContext to reduce file size.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from app_runtime.memory_manager import MemoryManager


class MemoryHost(Protocol):
    """MemoryContextMixin 依赖的宿主契约 — AppContext 由 builder 注入 memory_manager。"""

    memory_manager: Any  # MemoryManager


class MemoryContextMixin:
    """Memory and supertag delegation — extracted from AppContext."""

    # 宿主契约：memory_manager 由 AppContextBuilder 在运行时注入（见 _context_builder.py），
    # 此处仅作类型声明，供 mypy 识别 self.memory_manager。
    if TYPE_CHECKING:
        memory_manager: MemoryManager

    # ── 记忆方法（委托给 MemoryManager） ──

    @property
    def structured_memory_store(self):
        return self.memory_manager.structured_memory_store

    def extract_memories_from_conversation(self, messages: list[dict]):
        return self.memory_manager.extract_memories_from_conversation(messages)

    def schedule_memory_review(self, memory_id: str, content: str, memory_type: str = "factual",
                               tags: list[str] | None = None):
        return self.memory_manager.schedule_memory_review(memory_id, content, memory_type, tags)

    def get_due_memories(self, limit: int = 20):
        return self.memory_manager.get_due_memories(limit)

    def review_memory(self, memory_id: str, quality: int):
        return self.memory_manager.review_memory(memory_id, quality)

    def get_daily_review(self, limit: int = 10):
        return self.memory_manager.get_daily_review(limit)

    def get_memory_scheduler_stats(self):
        return self.memory_manager.get_memory_scheduler_stats()

    def quick_review_memory(self, memory_id: str, remembered: bool):
        return self.memory_manager.quick_review_memory(memory_id, remembered)

    def get_on_this_day(self, limit: int = 20):
        return self.memory_manager.get_on_this_day(limit)

    def get_memory_schedule(self, memory_id: str):
        return self.memory_manager.get_memory_schedule(memory_id)

    def load_review_settings(self):
        return self.memory_manager.load_review_settings()

    def save_review_settings(self, settings):
        return self.memory_manager.save_review_settings(settings)

    def get_review_event(self):
        return self.memory_manager.get_review_event()

    def link_memory(self, memory_entry: dict, existing_memories: list[dict] | None = None):
        return self.memory_manager.link_memory(memory_entry, existing_memories)

    def find_duplicate_memories(self, content: str, threshold: float = 0.85):
        return self.memory_manager.find_duplicate_memories(content, threshold)

    def get_linked_memories(self, memory_id: str, link_type: str | None = None):
        return self.memory_manager.get_linked_memories(memory_id, link_type)

    def get_memory_backlinks(self, memory_id: str, limit: int = 20):
        return self.memory_manager.get_memory_backlinks(memory_id, limit)

    def get_memory_link_stats(self):
        return self.memory_manager.get_memory_link_stats()

    def cleanup_memory_links(self, memory_id: str):
        return self.memory_manager.cleanup_memory_links(memory_id)

    def get_weak_link_extensions(self, content: str, top_k: int = 5):
        return self.memory_manager.get_weak_link_extensions(content, top_k)

    def record_memory_co_access(self, memory_id_a: str, memory_id_b: str):
        return self.memory_manager.record_memory_co_access(memory_id_a, memory_id_b)

    def get_memory_co_access_count(self, memory_id_a: str, memory_id_b: str):
        return self.memory_manager.get_memory_co_access_count(memory_id_a, memory_id_b)

    def reindex_memory_links(self, memory_id: str, new_content: str):
        return self.memory_manager.reindex_memory_links(memory_id, new_content)

    def merge_memory_link_networks(self, keep_id: str, remove_id: str):
        return self.memory_manager.merge_memory_link_networks(keep_id, remove_id)

    def get_linked_memories_with_detail(self, memory_id: str, link_type: str | None = None):
        return self.memory_manager.get_linked_memories_with_detail(memory_id, link_type)

    def periodic_recalculate_links(self, max_pairs: int = 200):
        return self.memory_manager.periodic_recalculate_links(max_pairs)

    # ── 超级标签（委托给 MemoryManager） ──

    def get_supertag_schemas(self) -> dict:
        return self.memory_manager.get_supertag_schemas()

    def get_supertag_schema(self, supertag_type: str) -> dict | None:
        return self.memory_manager.get_supertag_schema(supertag_type)

    def register_supertag_schema(self, schema: dict) -> dict:
        return self.memory_manager.register_supertag_schema(schema)

    def set_entry_supertag_data(self, entry_id: str, supertag_type: str, field_data: dict) -> dict:
        return self.memory_manager.set_entry_supertag_data(entry_id, supertag_type, field_data)

    def get_entry_supertag_data(self, entry_id: str) -> dict:
        return self.memory_manager.get_entry_supertag_data(entry_id)
