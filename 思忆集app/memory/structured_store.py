"""StructuredMemoryStore — JSON-based structured storage for memory entries.

Provides CRUD operations for entries with supertag-typed structured data,
supports indexing by supertag type / entity type / timestamps, and
emits EventBus events on mutation (when event_bus is available).
"""
from __future__ import annotations

import copy
import logging
import threading
import uuid
from datetime import datetime, timezone
from typing import Any

from .entry_schema import (
    SUPERTAG_SEPARATOR,
    SOURCE_CONVERSATION,
    SOURCE_MANUAL,
    SOURCE_IMPORT,
    SOURCE_SYNC,
    SOURCE_SYSTEM,
    make_structured_entry,
    update_structured_entry,
    validate_relation,
)
from core.events import AppEvent

logger = logging.getLogger("siyi.structured_memory.store")

# ── Store object name for LocalDataStore ──────────────────────────────

_STORE_KEY = "structured_memory_entries"
_AUTO_TAG_RULES_KEY = "supertag_auto_tag_rules"


class StructuredMemoryStore:
    """Persistent structured memory store backed by LocalDataStore JSON.

    Each entry has:
        - id: str (unique, auto-generated)
        - supertag_type: str (e.g. "@任务")
        - entity_type: str (person | task | project | ...)
        - source: str (origin)
        - created_at: str (ISO)
        - updated_at: str (ISO)
        - relations: list[dict]  (type, target_id, label)
        - field_values: dict      (schema-specific fields)
    """

    def __init__(self, data_store, event_bus=None):
        self._data_store = data_store
        self._event_bus = event_bus
        self._entries: list[dict[str, Any]] = []
        # 保护 _entries 与 _save/_load 的并发访问（CRUD + 后台刷新均可能并发）
        self._lock = threading.RLock()
        self._load()

    # ── Public CRUD API ───────────────────────────────────────────────

    def create_entry(
        self,
        *,
        supertag_type: str,
        field_values: dict[str, Any] | None = None,
        entity_type: str = "",
        relations: list[dict[str, str]] | None = None,
        source: str = SOURCE_MANUAL,
        tags: list[str] | None = None,
    ) -> dict[str, Any]:
        """Create a new structured memory entry.

        Returns the created entry dict with auto-generated id.
        Emits ``supertag.created`` on success.
        """
        entry_id = _generate_entry_id()
        base = make_structured_entry(
            supertag_type=supertag_type,
            field_values=field_values,
            entity_type=entity_type,
            relations=relations,
            source=source,
        )
        entry: dict[str, Any] = {
            "id": entry_id,
            "supertag_type": supertag_type,
            "entity_type": base.get("entity_type", ""),
            "source": base.get("source", SOURCE_MANUAL),
            "created_at": base.get("created_at", ""),
            "updated_at": base.get("updated_at", ""),
            "relations": base.get("relations", []),
            "tags": tags or [],
            "field_values": field_values or {},
        }
        with self._lock:
            self._entries.append(entry)
            self._save()
        self._emit("supertag.created", entry_id=entry_id, supertag_type=supertag_type)
        logger.debug("Created entry %s (%s)", entry_id, supertag_type)
        return dict(entry)

    def get_entry(self, entry_id: str) -> dict[str, Any] | None:
        """Get a single entry by id. Returns None if not found."""
        for entry in self._entries:
            if entry.get("id") == entry_id:
                return dict(entry)
        return None

    def update_entry(
        self,
        entry_id: str,
        *,
        field_values: dict[str, Any] | None = None,
        entity_type: str | None = None,
        relations: list[dict[str, str]] | None = None,
        source: str | None = None,
        tags: list[str] | None = None,
    ) -> dict[str, Any] | None:
        """Update an existing entry. Returns updated entry or None if not found.

        Emits ``supertag.updated`` on success.
        """
        with self._lock:
            idx = self._find_index(entry_id)
            if idx < 0:
                return None

            entry = dict(self._entries[idx])
            now = datetime.now(timezone.utc).isoformat()

            if field_values is not None:
                current_fields = dict(entry.get("field_values", {}))
                current_fields.update(field_values)
                entry["field_values"] = current_fields
            if entity_type is not None:
                entry["entity_type"] = entity_type
            if relations is not None:
                entry["relations"] = list(relations)
            if source is not None:
                entry["source"] = source
            if tags is not None:
                entry["tags"] = list(tags)
            entry["updated_at"] = now

            self._entries[idx] = entry
            self._save()
        self._emit("supertag.updated", entry_id=entry_id, supertag_type=entry.get("supertag_type", ""))
        logger.debug("Updated entry %s", entry_id)
        return dict(entry)

    def delete_entry(self, entry_id: str) -> bool:
        """Delete an entry by id. Returns True if deleted.

        Emits ``supertag.deleted`` on success.
        """
        with self._lock:
            idx = self._find_index(entry_id)
            if idx < 0:
                return False
            removed = self._entries.pop(idx)
            self._save()
        self._emit("supertag.deleted", entry_id=entry_id, supertag_type=removed.get("supertag_type", ""))
        logger.debug("Deleted entry %s", entry_id)
        return True

    def list_entries(
        self,
        *,
        supertag_type: str | None = None,
        entity_type: str | None = None,
        source: str | None = None,
        limit: int = 100,
        offset: int = 0,
        sort_by: str = "updated_at",
        sort_desc: bool = True,
    ) -> list[dict[str, Any]]:
        """List entries with optional filters and pagination."""
        filtered = list(self._entries)

        if supertag_type:
            filtered = [e for e in filtered if e.get("supertag_type") == supertag_type]
        if entity_type:
            filtered = [e for e in filtered if e.get("entity_type") == entity_type]
        if source:
            filtered = [e for e in filtered if e.get("source") == source]

        # Sort by field
        reverse = sort_desc
        if sort_by in ("created_at", "updated_at"):
            filtered.sort(key=lambda e: e.get(sort_by, ""), reverse=reverse)
        elif sort_by == "supertag_type":
            filtered.sort(key=lambda e: e.get("supertag_type", ""), reverse=reverse)

        filtered = filtered[offset:offset + limit]
        return [dict(e) for e in filtered]

    def count_entries(
        self,
        *,
        supertag_type: str | None = None,
        entity_type: str | None = None,
    ) -> int:
        """Count entries matching optional filters."""
        filtered = list(self._entries)
        if supertag_type:
            filtered = [e for e in filtered if e.get("supertag_type") == supertag_type]
        if entity_type:
            filtered = [e for e in filtered if e.get("entity_type") == entity_type]
        return len(filtered)

    # ── Search / query ───────────────────────────────────────────────

    def search_by_relation(
        self,
        *,
        target_id: str | None = None,
        rel_type: str | None = None,
    ) -> list[dict[str, Any]]:
        """Search entries by relation target and/or relation type."""
        results: list[dict[str, Any]] = []
        for entry in self._entries:
            for rel in entry.get("relations", []):
                if target_id and rel.get("target_id") != target_id:
                    continue
                if rel_type and rel.get("type") != rel_type:
                    continue
                results.append(dict(entry))
                break
        return results

    def cleanup_relations_for_target(self, target_id: str) -> int:
        """P1.5: 清理所有结构化记忆条目中指向 ``target_id`` 的 relations。

        笔记/任务删除后，其它结构化记忆条目中可能仍保留指向它的 relations，
        导致 ``search_by_relation(target_id=...)`` 命中悬空引用。本方法
        原地移除这些 relations，并触发持久化。

        Args:
            target_id: 被删除对象的 ID（例如 "42" 或 "note:42"）。
                本方法同时匹配 ``target_id`` 与 ``f"note:{target_id}"`` 等带前缀形式。

        Returns:
            实际移除的 relation 数。
        """
        if not target_id:
            return 0
        tid = str(target_id)
        # 兼容多种格式：纯 ID、带 source_type 前缀
        candidates = {tid, f"note:{tid}", f"task:{tid}", f"inbox:{tid}", f"info:{tid}"}
        removed = 0
        with self._lock:
            for entry in self._entries:
                rels = entry.get("relations") or []
                if not rels:
                    continue
                kept: list[dict[str, Any]] = []
                for rel in rels:
                    if str(rel.get("target_id") or "") in candidates:
                        removed += 1
                        continue
                    kept.append(rel)
                if len(kept) != len(rels):
                    entry["relations"] = kept
                    entry["updated_at"] = datetime.now(timezone.utc).isoformat()
            if removed > 0:
                self._save()
        if removed > 0:
            logger.info(
                "StructuredMemoryStore: cleaned up %d relations pointing to %s",
                removed, tid,
            )
        return removed

    def search_by_field(
        self,
        field_name: str,
        field_value: Any,
        *,
        exact: bool = True,
    ) -> list[dict[str, Any]]:
        """Search entries by a specific field value in field_values."""
        results: list[dict[str, Any]] = []
        for entry in self._entries:
            val = entry.get("field_values", {}).get(field_name)
            if exact and val == field_value:
                results.append(dict(entry))
            elif not exact and val is not None and str(field_value).lower() in str(val).lower():
                results.append(dict(entry))
        return results

    def search_by_tags(self, tags: list[str], *, match_all: bool = False) -> list[dict[str, Any]]:
        """Search entries by tags.

        Args:
            tags: List of tags to match.
            match_all: If True, entry must have ALL tags. If False, any match suffices.

        Returns:
            Matching entry dicts.
        """
        tag_set = set(tags)
        results: list[dict[str, Any]] = []
        for entry in self._entries:
            entry_tags = set(entry.get("tags", []))
            if match_all:
                if tag_set.issubset(entry_tags):
                    results.append(dict(entry))
            else:
                if tag_set & entry_tags:
                    results.append(dict(entry))
        return results

    # ── Bulk operations ──────────────────────────────────────────────

    def import_entries(self, entries: list[dict[str, Any]]) -> int:
        """Bulk import entries. Each entry should have at minimum ``supertag_type``.

        Returns count of imported entries.
        """
        now = datetime.now(timezone.utc).isoformat()
        imported = 0
        with self._lock:
            for raw in entries:
                entry_id = raw.get("id") or _generate_entry_id()
                entry = {
                    "id": entry_id,
                    "supertag_type": str(raw.get("supertag_type", "")),
                    "entity_type": str(raw.get("entity_type", "")),
                    "source": str(raw.get("source", SOURCE_IMPORT)),
                    "created_at": str(raw.get("created_at", now)),
                    "updated_at": str(raw.get("updated_at", now)),
                    "relations": list(raw.get("relations", [])),
                    "tags": list(raw.get("tags", [])),
                    "field_values": dict(raw.get("field_values", {})),
                }
                # Avoid duplicates by id
                existing = self._find_index(entry_id)
                if existing >= 0:
                    self._entries[existing] = entry
                else:
                    self._entries.append(entry)
                imported += 1

            if imported > 0:
                self._save()
        if imported > 0:
            self._emit("supertag.updated", action="import", count=imported)
            logger.info("Imported %d entries", imported)
        return imported

    def clear_all(self) -> int:
        """Delete all entries. Returns count removed."""
        with self._lock:
            count = len(self._entries)
            self._entries.clear()
            self._save()
        self._emit("supertag.updated", action="clear", count=count)
        logger.info("Cleared all %d entries", count)
        return count

    # ── Internal ─────────────────────────────────────────────────────

    def _find_index(self, entry_id: str) -> int:
        for i, entry in enumerate(self._entries):
            if entry.get("id") == entry_id:
                return i
        return -1

    def _load(self) -> None:
        try:
            data = self._data_store.load_json(_STORE_KEY, [])
            if isinstance(data, list):
                with self._lock:
                    self._entries = [dict(e) for e in data if isinstance(e, dict) and e.get("id")]
        except Exception as exc:
            logger.warning("Failed to load structured entries: %s", exc, exc_info=True)

    def _save(self) -> None:
        try:
            with self._lock:
                self._data_store.save_json(_STORE_KEY, self._entries)
        except Exception as exc:
            logger.warning("Failed to save structured entries: %s", exc, exc_info=True)

    def _emit(self, event_type: str, **payload: Any) -> None:
        if self._event_bus is not None:
            try:
                self._event_bus.emit(AppEvent(event_type, payload=payload, source="structured_memory_store"))
            except Exception as exc:
                logger.warning("Event emit failed for %s: %s", event_type, exc, exc_info=True)

    def get_all_entries_raw(self) -> list[dict[str, Any]]:
        """Return raw entry list (for introspection / testing)."""
        return [dict(e) for e in self._entries]


def _generate_entry_id() -> str:
    """生成全局唯一条目 ID。

    用 ``uuid4`` 替代旧的 ``id(datetime)`` —— 后者在同一进程中恒定，
    且进程重启后仍可能冲突，造成条目 ID 碰撞。
    """
    return f"mem_{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S%f')}_{uuid.uuid4().hex[:8]}"
