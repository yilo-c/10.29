"""ChromaDB operations for memory search — sync, add, update, results conversion.

Extracted from memory/search.py — ChromaDB-related HybridMemorySearch methods.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Protocol

logger = logging.getLogger("siyi.memory_search.chroma")


class ChromaHost(Protocol):
    """ChromaMixin 依赖的宿主契约 — 由 HybridMemorySearch 提供。"""

    _chroma_available: bool
    _chroma_collection: Any
    def _ensure_chroma(self) -> bool: ...


class ChromaMixin:
    """Mixin providing ChromaDB sync and operations.

    Expects self._chroma_available, self._chroma_collection set by __init__.
    """

    # 宿主契约：由 HybridMemorySearch 提供（见 ChromaHost Protocol）
    if TYPE_CHECKING:
        _chroma_available: bool
        _chroma_collection: Any
        def _ensure_chroma(self) -> bool: ...

    def _sync_chromadb(self, entries: list[dict[str, Any]]) -> None:
        """增量同步条目到 ChromaDB。

        P3 优化：通过比较 updated_at 时间戳跳过未变更条目，
        避免每次全量 upsert（1000 条记忆时从 ~2s 降至 ~50ms）。
        """
        if not self._chroma_available or not self._ensure_chroma():
            return

        try:
            existing = self._chroma_collection.get()
            existing_ids = set(existing.get("ids") or [])
            new_ids = {str(e.get("entry_id") or e.get("id") or "") for e in entries}

            ids_to_remove = existing_ids - new_ids
            if ids_to_remove:
                self._chroma_collection.delete(ids=list(ids_to_remove))

            # P3 增量优化：构建已有条目的 updated_at 索引，只 upsert 变更项
            existing_meta = existing.get("metadatas") or []
            existing_updated: dict[str, str] = {}
            for i, eid in enumerate(existing.get("ids") or []):
                if i < len(existing_meta):
                    meta = existing_meta[i] or {}
                    existing_updated[eid] = str(meta.get("updated_at") or "")

            if entries:
                ids = []
                documents = []
                metadatas = []
                skipped = 0
                for entry in entries:
                    eid = str(entry.get("entry_id") or entry.get("id") or "")
                    title = str(entry.get("title") or "")
                    content = str(entry.get("content") or "")
                    document = f"{title}\n{content}" if title else content
                    if not document.strip():
                        continue
                    entry_updated = str(entry.get("updated_at") or "")
                    # 跳过未变更条目（updated_at 相同）
                    if eid in existing_updated and existing_updated[eid] == entry_updated:
                        skipped += 1
                        continue
                    ids.append(eid)
                    documents.append(document)
                    metadatas.append({
                        "title": title,
                        "source_type": str(entry.get("source_type") or ""),
                        "updated_at": entry_updated,
                        "tags": str(entry.get("tags_text") or entry.get("tags") or ""),
                    })

                if ids:
                    self._chroma_collection.upsert(
                        ids=ids,
                        documents=documents,
                        metadatas=metadatas,
                    )
                    logger.debug(
                        "ChromaDB synced %d entries (%d skipped, %d total)",
                        len(ids), skipped, len(entries),
                    )
        except Exception as exc:
            logger.warning("ChromaDB sync failed: %s", exc)

    def _add_to_chromadb(self, entry: dict[str, Any]) -> None:
        """添加单条条目到 ChromaDB。"""
        if not self._chroma_available or not self._ensure_chroma():
            return
        try:
            eid = str(entry.get("id") or "")
            title = str(entry.get("title") or "")
            content = str(entry.get("content") or "")
            document = f"{title}\n{content}" if title else content
            if not document.strip():
                return
            self._chroma_collection.upsert(
                ids=[eid],
                documents=[document],
                metadatas=[{
                    "title": title,
                    "source_type": str(entry.get("source_type") or ""),
                    "updated_at": str(entry.get("updated_at") or ""),
                    "tags": str(entry.get("tags_text") or entry.get("tags") or ""),
                }],
            )
        except Exception as exc:
            logger.warning("ChromaDB add failed: %s", exc, exc_info=True)

    def _update_in_chromadb(self, entry_id: str, entry: dict[str, Any]) -> None:
        """更新 ChromaDB 中的条目。"""
        if not self._chroma_available or not self._ensure_chroma():
            return
        try:
            if "title" in entry or "content" in entry:
                title = str(entry.get("title") or "")
                content = str(entry.get("content") or "")
                document = f"{title}\n{content}" if title else content
                if document.strip():
                    self._chroma_collection.update(
                        ids=[entry_id],
                        documents=[document],
                        metadatas=[{
                            "title": title,
                            "source_type": str(entry.get("source_type") or ""),
                            "updated_at": str(entry.get("updated_at") or ""),
                            "tags": str(entry.get("tags_text") or entry.get("tags") or ""),
                        }],
                    )
        except Exception as exc:
            logger.warning("ChromaDB update failed: %s", exc, exc_info=True)

    @staticmethod
    def _chroma_results_to_items(
        results: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """将 ChromaDB 查询结果转换为统一格式。"""
        items: list[dict[str, Any]] = []
        ids = results.get("ids", [[]])[0] or []
        documents = results.get("documents", [[]])[0] or []
        metadatas = results.get("metadatas", [[]])[0] or []
        distances = results.get("distances", [[]])[0] or []

        for i in range(len(ids)):
            similarity = 1.0 - (distances[i] if i < len(distances) else 0.0)
            meta = metadatas[i] if i < len(metadatas) else {}
            items.append({
                "id": ids[i],
                "title": str(meta.get("title") or ""),
                "content": documents[i] if i < len(documents) else "",
                "tags": str(meta.get("tags") or ""),
                "source_type": str(meta.get("source_type") or ""),
                "updated_at": str(meta.get("updated_at") or ""),
                "vector_score": similarity,
            })
        return items
