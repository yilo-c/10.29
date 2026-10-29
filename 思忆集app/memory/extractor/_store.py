"""Pending memory store, dedup engine, and conflict tracker for memory extraction.

Extracted from memory/extractor.py — PendingMemoryStore, DedupEngine, ConflictTracker.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from ..types import (
    SIMILARITY_DUPLICATE,
    SIMILARITY_MERGE,
    MemoryCandidate,
    PendingMemory,
    ConflictRecord,
)

logger = logging.getLogger("siyi.memory.extractor.store")


class PendingMemoryStore:
    """Manages the pending confirmation queue for medium-confidence memories."""

    def __init__(self, data_store):
        self._data_store = data_store

    def add(self, candidate: MemoryCandidate, conversation_id: str = "") -> None:
        """Add a candidate to the pending confirmation queue."""
        now = datetime.now(timezone.utc).isoformat()
        pending = PendingMemory(
            candidate=candidate,
            created_at=now,
            conversation_id=conversation_id,
        )

        def _updater(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
            normalized = list(items) if isinstance(items, list) else []
            normalized.append(pending.to_dict())
            return normalized[-200:]

        self._data_store.update_json("assistant_memory_pending", [], _updater)

    def get_pending(self) -> list[PendingMemory]:
        """Get all pending memories awaiting confirmation."""
        items = self._data_store.load_json("assistant_memory_pending", [])
        if not isinstance(items, list):
            return []
        return [PendingMemory.from_dict(item) for item in items if isinstance(item, dict)]

    def remove(self, index: int) -> bool:
        """Remove a pending memory by index."""
        def _updater(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
            normalized = list(items) if isinstance(items, list) else []
            if 0 <= index < len(normalized):
                normalized.pop(index)
            return normalized

        self._data_store.update_json("assistant_memory_pending", [], _updater)
        return True

    def pop_confirmed(self, index: int) -> PendingMemory | None:
        """Remove and return a confirmed pending memory."""
        items = self._data_store.load_json("assistant_memory_pending", [])
        if not isinstance(items, list) or index < 0 or index >= len(items):
            return None
        item = items.pop(index)
        self._data_store.save_json("assistant_memory_pending", items)
        return PendingMemory.from_dict(item)

    def get_pending_count(self) -> int:
        """Get the number of pending confirmations."""
        items = self._data_store.load_json("assistant_memory_pending", [])
        if isinstance(items, list):
            return len(items)
        return 0

    def record_prompt(self, index: int) -> None:
        """Record that user was prompted about this pending memory."""
        now = datetime.now(timezone.utc).isoformat()

        def _updater(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
            normalized = list(items) if isinstance(items, list) else []
            if 0 <= index < len(normalized):
                item = dict(normalized[index])
                item["confirm_count"] = int(item.get("confirm_count", 0)) + 1
                item["last_prompted_at"] = now
                normalized[index] = item
            return normalized

        self._data_store.update_json("assistant_memory_pending", [], _updater)


class DedupEngine:
    """Semantic dedup and merging for memory entries.

    Uses token-overlap similarity as lightweight approximation.
    """

    @staticmethod
    def _token_similarity(a: str, b: str) -> float:
        """Compute token-overlap (Jaccard) similarity between two strings."""
        if not a or not b:
            return 0.0
        a_tokens = set(a.lower().split())
        b_tokens = set(b.lower().split())
        if not a_tokens or not b_tokens:
            return 0.0
        intersection = a_tokens & b_tokens
        union = a_tokens | b_tokens
        return len(intersection) / len(union)

    def check(
        self,
        candidate: MemoryCandidate,
        existing_memories: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Check if a candidate duplicates or supplements existing memories."""
        best: dict[str, Any] = {
            "status": "unique",
            "matched_entry": None,
            "similarity": 0.0,
        }

        for entry in existing_memories:
            content = str(entry.get("content") or "")
            if not content:
                continue

            sim = self._token_similarity(candidate.content, content)
            if sim <= best["similarity"]:
                continue

            best["similarity"] = sim
            best["matched_entry"] = entry

            if sim >= SIMILARITY_DUPLICATE:
                best["status"] = "duplicate"
            elif sim >= SIMILARITY_MERGE:
                best["status"] = "merge"

        return best

    @staticmethod
    def merge(
        candidate: MemoryCandidate,
        existing: dict[str, Any],
    ) -> dict[str, Any]:
        """Merge candidate details into existing memory entry."""
        existing_content = str(existing.get("content") or "")
        existing_type = str(existing.get("type") or candidate.type)
        existing_tags = list(existing.get("tags") or []) + [
            t for t in candidate.tags if t not in existing.get("tags", [])
        ]

        merged_content = existing_content
        new_text = candidate.content.lower()
        existing_lower = existing_content.lower()
        if new_text not in existing_lower and len(new_text) > 10:
            merged_content = existing_content.rstrip(".,;") + "; " + candidate.content

        return {
            "content": merged_content,
            "type": existing_type,
            "tags": existing_tags,
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "source": candidate.source,
            "merged_from": candidate.content[:100],
        }


class ConflictTracker:
    """Tracks memory conflicts with history and resolution support."""

    def __init__(self, data_store):
        self._data_store = data_store

    def record_conflict(self, record: ConflictRecord) -> None:
        """Save a conflict record."""
        def _updater(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
            normalized = list(items) if isinstance(items, list) else []
            normalized.append(record.to_dict())
            return normalized[-100:]

        self._data_store.update_json("assistant_memory_conflicts", [], _updater)

    def get_unresolved_conflicts(self) -> list[ConflictRecord]:
        """Get all unresolved conflict records."""
        items = self._data_store.load_json("assistant_memory_conflicts", [])
        if not isinstance(items, list):
            return []
        return [
            ConflictRecord.from_dict(item)
            for item in items
            if isinstance(item, dict) and not item.get("resolved", False)
        ]

    def get_all_conflicts(self) -> list[ConflictRecord]:
        """Get all conflict records."""
        items = self._data_store.load_json("assistant_memory_conflicts", [])
        if not isinstance(items, list):
            return []
        return [
            ConflictRecord.from_dict(item)
            for item in items
            if isinstance(item, dict)
        ]

    def resolve_conflict(self, index: int, resolution: str) -> bool:
        """Mark a conflict as resolved."""
        items = self._data_store.load_json("assistant_memory_conflicts", [])
        if not isinstance(items, list):
            return False

        unresolved_indices = [
            i for i, item in enumerate(items)
            if isinstance(item, dict) and not item.get("resolved", False)
        ]
        if index < 0 or index >= len(unresolved_indices):
            return False

        target = unresolved_indices[index]
        now = datetime.now(timezone.utc).isoformat()
        items[target]["resolved"] = True
        items[target]["resolution"] = resolution
        items[target]["resolved_at"] = now
        self._data_store.save_json("assistant_memory_conflicts", items)
        return True


__all__ = [
    "PendingMemoryStore",
    "DedupEngine",
    "ConflictTracker",
]
