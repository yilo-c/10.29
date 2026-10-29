"""Sync & merge operations for LocalVocabularyRepository.

Extracted from _vocab.py to reduce its size from 1435 lines.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Protocol

from sync.note_sync import get_or_create_local_device_id
from sync.vocabulary_sync import (
    build_word_sync_payload as _build_word_sync_module,
    build_wordbook_key,
    build_wordbook_sync_payload as _build_wordbook_sync_module,
    ensure_word_sync_metadata,
    ensure_wordbook_sync_metadata,
)

if TYPE_CHECKING:
    from data.local_storage import LocalDataStore


class VocabHost(Protocol):
    """VocabSyncMixin 依赖的宿主契约 — 由 LocalVocabularyRepository 提供。"""

    data_store: Any  # LocalDataStore
    def list_wordbooks(self, include_deleted: bool = False) -> list[dict[str, Any]]: ...
    def list_words(self, include_deleted: bool = False) -> list[dict[str, Any]]: ...
    def add_wordbook(self, name: str) -> int: ...
    def _load_wordbooks(self) -> list[dict[str, Any]]: ...
    def _load_words(self) -> list[dict[str, Any]]: ...
    def _save_wordbooks(self, items: list[dict[str, Any]]) -> None: ...
    def _save_words(self, items: list[dict[str, Any]]) -> None: ...


class VocabSyncMixin:
    """Sync & merge operations for vocabulary repository.

    Intended to be used as a mixin with LocalVocabularyRepository.
    Requires self.data_store and access to _load_*/_save_* CRUD methods.
    """

    # 宿主契约：由 LocalVocabularyRepository 提供（见 VocabHost Protocol）
    if TYPE_CHECKING:
        data_store: LocalDataStore
        def list_wordbooks(self, include_deleted: bool = False) -> list[dict[str, Any]]: ...
        def list_words(self, include_deleted: bool = False) -> list[dict[str, Any]]: ...
        def add_wordbook(self, name: str) -> int: ...
        def _load_wordbooks(self) -> list[dict[str, Any]]: ...
        def _load_words(self) -> list[dict[str, Any]]: ...
        def _save_wordbooks(self, items: list[dict[str, Any]]) -> None: ...
        def _save_words(self, items: list[dict[str, Any]]) -> None: ...

    # ── Remote snapshot helpers ──

    def get_remote_snapshot_words(self) -> list[dict[str, Any]]:
        if self.data_store is None:
            return []
        return self.data_store.load_json("remote_snapshot_words", []) or []

    def set_remote_snapshot_words(self, words: list[dict[str, Any]]) -> None:
        if self.data_store is None:
            return
        self.data_store.save_json("remote_snapshot_words", words)

    def get_cloud_words(self) -> list[dict[str, Any]]:
        return self.get_remote_snapshot_words()

    def set_cloud_words(self, words: list[dict[str, Any]]) -> None:
        self.set_remote_snapshot_words(words)

    def get_remote_snapshot_wordbooks(self) -> list[dict[str, Any]]:
        if self.data_store is None:
            return []
        return self.data_store.load_json("remote_snapshot_wordbooks", []) or []

    def set_remote_snapshot_wordbooks(self, wordbooks: list[dict[str, Any]]) -> None:
        if self.data_store is None:
            return
        self.data_store.save_json("remote_snapshot_wordbooks", wordbooks)

    def get_cloud_wordbooks(self) -> list[dict[str, Any]]:
        return self.get_remote_snapshot_wordbooks()

    def set_cloud_wordbooks(self, wordbooks: list[dict[str, Any]]) -> None:
        self.set_remote_snapshot_wordbooks(wordbooks)

    # ── Sync payload builders ──

    def build_wordbook_sync_payload(self, wordbook_id: int) -> dict[str, Any] | None:
        for wordbook in self.list_wordbooks(include_deleted=True):
            if wordbook.get("id") == wordbook_id:
                return _build_wordbook_sync_module(wordbook)
        return None

    def build_word_sync_payload(self, word_id: int) -> dict[str, Any] | None:
        for word in self.list_words(include_deleted=True):
            if word.get("id") == word_id:
                return _build_word_sync_module(word)
        return None

    def list_pending_sync_wordbooks(self) -> list[dict[str, Any]]:
        return [
            dict(wordbook)
            for wordbook in self.list_wordbooks(include_deleted=True)
            if wordbook.get("sync_state") in {"local_only", "dirty"}
        ]

    def list_pending_sync_words(self) -> list[dict[str, Any]]:
        return [
            dict(word)
            for word in self.list_words(include_deleted=True)
            if word.get("sync_state") in {"local_only", "dirty"}
        ]

    # ── Mark as synced ──

    def mark_wordbook_as_synced(self, wordbook_id: int, remote_wordbook: dict[str, Any]) -> None:
        wordbooks = self._load_wordbooks()
        changed = False
        resolved_remote_id = None
        resolved_book_key = None
        resolved_name = None
        for wordbook in wordbooks:
            if wordbook.get("id") != wordbook_id:
                continue
            remote_id = remote_wordbook.get("remote_id") or remote_wordbook.get("id")
            if isinstance(remote_id, str) and remote_id.strip():
                wordbook["remote_id"] = remote_id.strip()
                resolved_remote_id = remote_id.strip()
            if remote_wordbook.get("book_key"):
                wordbook["book_key"] = remote_wordbook.get("book_key")
                resolved_book_key = remote_wordbook.get("book_key")
            resolved_name = remote_wordbook.get("name") or wordbook.get("name")
            wordbook["sync_state"] = "synced"
            wordbook["last_synced_at"] = (
                remote_wordbook.get("last_synced_at")
                or remote_wordbook.get("updated_at")
                or datetime.now(timezone.utc).isoformat()
            )
            if remote_wordbook.get("updated_at"):
                wordbook["updated_at"] = remote_wordbook.get("updated_at")
            if remote_wordbook.get("created_at"):
                wordbook["created_at"] = remote_wordbook.get("created_at")
            if remote_wordbook.get("name"):
                wordbook["name"] = remote_wordbook.get("name")
            if "description" in remote_wordbook:
                wordbook["description"] = remote_wordbook.get("description") or ""
            if remote_wordbook.get("version") is not None:
                wordbook["version"] = max(int(remote_wordbook.get("version") or 1), 1)
            if "deleted_at" in remote_wordbook:
                wordbook["deleted_at"] = remote_wordbook.get("deleted_at")
            changed = True
            break
        if changed:
            self._save_wordbooks(wordbooks)
            self._sync_child_words_for_wordbook(
                wordbook_id,
                wordbook_name=resolved_name,
                wordbook_key=resolved_book_key,
                wordbook_remote_id=resolved_remote_id,
            )

    def mark_word_as_synced(self, word_id: int, remote_word: dict[str, Any]) -> None:
        words = self._load_words()
        changed = False
        for word in words:
            if word.get("id") != word_id:
                continue
            remote_id = remote_word.get("remote_id") or remote_word.get("id")
            if isinstance(remote_id, str) and remote_id.strip():
                word["remote_id"] = remote_id.strip()
            if remote_word.get("word_key"):
                word["word_key"] = remote_word.get("word_key")
            if "wordbook_remote_id" in remote_word:
                word["wordbook_remote_id"] = remote_word.get("wordbook_remote_id")
            if "wordbook_key" in remote_word:
                word["wordbook_key"] = remote_word.get("wordbook_key")
            if "wordbook_name" in remote_word:
                word["wordbook_name"] = remote_word.get("wordbook_name")
            word["sync_state"] = "synced"
            word["last_synced_at"] = (
                remote_word.get("last_synced_at")
                or remote_word.get("updated_at")
                or datetime.now(timezone.utc).isoformat()
            )
            if remote_word.get("updated_at"):
                word["updated_at"] = remote_word.get("updated_at")
            if remote_word.get("created_at"):
                word["created_at"] = remote_word.get("created_at")
            if remote_word.get("version") is not None:
                word["version"] = max(int(remote_word.get("version") or 1), 1)
            if "deleted_at" in remote_word:
                word["deleted_at"] = remote_word.get("deleted_at")
            changed = True
            break
        if changed:
            self._save_words(words)

    def resolve_local_only_deleted_wordbook(self, wordbook_id: int) -> None:
        self._save_wordbooks([
            wordbook for wordbook in self._load_wordbooks()
            if wordbook.get("id") != wordbook_id
        ])

    def resolve_local_only_deleted_word(self, word_id: int) -> None:
        self._save_words([
            word for word in self._load_words()
            if word.get("id") != word_id
        ])

    # ── Remote ID lookup ──

    def find_remote_snapshot_wordbook_by_remote_id(self, remote_id: str) -> dict[str, Any] | None:
        remote_key = str(remote_id or "").strip()
        if not remote_key:
            return None
        for wordbook in self.get_remote_snapshot_wordbooks():
            candidate = str(wordbook.get("remote_id") or wordbook.get("id") or "").strip()
            if candidate == remote_key:
                return dict(wordbook)
        return None

    def find_remote_snapshot_word_by_remote_id(self, remote_id: str) -> dict[str, Any] | None:
        remote_key = str(remote_id or "").strip()
        if not remote_key:
            return None
        for word in self.get_remote_snapshot_words():
            candidate = str(word.get("remote_id") or word.get("id") or "").strip()
            if candidate == remote_key:
                return dict(word)
        return None

    def find_cloud_wordbook_by_remote_id(self, remote_id: str) -> dict[str, Any] | None:
        return self.find_remote_snapshot_wordbook_by_remote_id(remote_id)

    def find_cloud_word_by_remote_id(self, remote_id: str) -> dict[str, Any] | None:
        return self.find_remote_snapshot_word_by_remote_id(remote_id)

    # ── Remote overwrite ──

    def overwrite_wordbook_with_remote(self, wordbook_id: int, remote_wordbook: dict[str, Any]) -> bool:
        if self.data_store is None:
            return False
        wordbooks = self._load_wordbooks()
        for index, wordbook in enumerate(wordbooks):
            if wordbook.get("id") != wordbook_id:
                continue
            merged_wordbook = self._build_local_wordbook_from_remote(
                remote_wordbook,
                local_id=wordbook_id,
                existing_wordbook=wordbook,
            )
            wordbooks[index] = merged_wordbook
            self._save_wordbooks(wordbooks)
            self._sync_child_words_for_wordbook(
                wordbook_id,
                wordbook_name=merged_wordbook.get("name"),
                wordbook_key=merged_wordbook.get("book_key"),
                wordbook_remote_id=merged_wordbook.get("remote_id"),
            )
            return True
        return False

    def overwrite_word_with_remote(self, word_id: int, remote_word: dict[str, Any]) -> bool:
        if self.data_store is None:
            return False
        words = self._load_words()
        for index, word in enumerate(words):
            if word.get("id") != word_id:
                continue
            words[index] = self._build_local_word_from_remote(
                remote_word,
                local_id=word_id,
                existing_word=word,
            )
            self._save_words(words)
            return True
        return False

    # ── Merge ──

    def merge_remote_wordbooks(self, remote_wordbooks: list[dict[str, Any]], prefer_remote: bool = False) -> dict[str, Any]:
        wordbooks = self.list_wordbooks(include_deleted=True)
        remote_index = {}
        for wordbook in wordbooks:
            remote_id = wordbook.get("remote_id")
            book_key = wordbook.get("book_key")
            if isinstance(remote_id, str) and remote_id:
                remote_index[remote_id] = wordbook
            if isinstance(book_key, str) and book_key and book_key not in remote_index:
                remote_index[book_key] = wordbook
        changed = False
        added = 0
        updated = 0
        skipped = 0
        conflicts: list[dict[str, Any]] = []
        next_id = self.data_store.next_id(wordbooks) if self.data_store is not None else 1

        for remote_wordbook in remote_wordbooks or []:
            if not isinstance(remote_wordbook, dict):
                continue
            remote_key = str(
                remote_wordbook.get("remote_id")
                or remote_wordbook.get("id")
                or remote_wordbook.get("book_key")
                or ""
            ).strip()
            if not remote_key:
                continue
            existing_wordbook = (
                remote_index.get(remote_key)
                or remote_index.get(str(remote_wordbook.get("book_key") or "").strip())
            )
            if existing_wordbook is None:
                local_wordbook = self._build_local_wordbook_from_remote(remote_wordbook, next_id)
                wordbooks.append(local_wordbook)
                remote_index[remote_key] = local_wordbook
                book_key = local_wordbook.get("book_key")
                if book_key:
                    remote_index[book_key] = local_wordbook
                next_id += 1
                added += 1
                changed = True
                continue

            if not self._is_remote_wordbook_different(existing_wordbook, remote_wordbook):
                if existing_wordbook.get("sync_state") != "synced":
                    existing_wordbook["sync_state"] = "synced"
                    existing_wordbook["last_synced_at"] = (
                        remote_wordbook.get("last_synced_at") or remote_wordbook.get("updated_at")
                    )
                    updated += 1
                    changed = True
                continue

            if existing_wordbook.get("sync_state") in {"local_only", "dirty"} and not prefer_remote:
                skipped += 1
                conflicts.append({
                    "entity_type": "wordbook",
                    "local_id": existing_wordbook.get("id"),
                    "remote_id": str(remote_wordbook.get("remote_id") or remote_wordbook.get("id") or remote_key),
                    "title": existing_wordbook.get("name") or remote_wordbook.get("name") or "未命名单词本",
                    "local_title": existing_wordbook.get("name") or "未命名单词本",
                    "remote_title": remote_wordbook.get("name") or "未命名单词本",
                    "local_updated_at": existing_wordbook.get("updated_at") or existing_wordbook.get("created_at"),
                    "remote_updated_at": remote_wordbook.get("updated_at") or remote_wordbook.get("created_at"),
                    "local_description": existing_wordbook.get("description") or "",
                    "remote_description": remote_wordbook.get("description") or "",
                    "local_deleted_at": existing_wordbook.get("deleted_at"),
                    "remote_deleted_at": remote_wordbook.get("deleted_at"),
                })
                continue

            existing_id = existing_wordbook.get("id")
            if existing_id is None:
                continue
            merged_wordbook = self._build_local_wordbook_from_remote(
                remote_wordbook, existing_id, existing_wordbook=existing_wordbook
            )
            wordbooks[wordbooks.index(existing_wordbook)] = merged_wordbook
            remote_index[remote_key] = merged_wordbook
            book_key = merged_wordbook.get("book_key")
            if book_key:
                remote_index[book_key] = merged_wordbook
            updated += 1
            changed = True

        if changed:
            self._save_wordbooks(wordbooks)
        return {
            "added": added,
            "updated": updated,
            "skipped": skipped,
            "conflicts": conflicts,
            "total_remote": len([item for item in (remote_wordbooks or []) if isinstance(item, dict)]),
        }

    def merge_remote_words(self, remote_words: list[dict[str, Any]], prefer_remote: bool = False) -> dict[str, Any]:
        words = self.list_words(include_deleted=True)
        remote_index = {}
        for word in words:
            remote_id = word.get("remote_id")
            word_key = word.get("word_key")
            if isinstance(remote_id, str) and remote_id:
                remote_index[remote_id] = word
            if isinstance(word_key, str) and word_key and word_key not in remote_index:
                remote_index[word_key] = word
        changed = False
        added = 0
        updated = 0
        skipped = 0
        conflicts: list[dict[str, Any]] = []
        next_id = self.data_store.next_id(words) if self.data_store is not None else 1

        for remote_word in remote_words or []:
            if not isinstance(remote_word, dict):
                continue
            remote_key = str(
                remote_word.get("remote_id")
                or remote_word.get("id")
                or remote_word.get("word_key")
                or ""
            ).strip()
            if not remote_key:
                continue
            existing_word = (
                remote_index.get(remote_key)
                or remote_index.get(str(remote_word.get("word_key") or "").strip())
            )
            if existing_word is None:
                local_word = self._build_local_word_from_remote(remote_word, next_id)
                words.append(local_word)
                remote_index[remote_key] = local_word
                word_key = local_word.get("word_key")
                if word_key:
                    remote_index[word_key] = local_word
                next_id += 1
                added += 1
                changed = True
                continue

            if not self._is_remote_word_different(existing_word, remote_word):
                if existing_word.get("sync_state") != "synced":
                    existing_word["sync_state"] = "synced"
                    existing_word["last_synced_at"] = (
                        remote_word.get("last_synced_at") or remote_word.get("updated_at")
                    )
                    updated += 1
                    changed = True
                continue

            if existing_word.get("sync_state") in {"local_only", "dirty"} and not prefer_remote:
                skipped += 1
                conflicts.append({
                    "entity_type": "word",
                    "local_id": existing_word.get("id"),
                    "remote_id": str(remote_word.get("remote_id") or remote_word.get("id") or remote_key),
                    "title": existing_word.get("word") or remote_word.get("word") or "未命名单词",
                    "local_title": existing_word.get("word") or "未命名单词",
                    "remote_title": remote_word.get("word") or "未命名单词",
                    "local_updated_at": existing_word.get("updated_at") or existing_word.get("created_at"),
                    "remote_updated_at": remote_word.get("updated_at") or remote_word.get("created_at"),
                    "local_wordbook_name": existing_word.get("wordbook_name") or "默认单词本",
                    "remote_wordbook_name": remote_word.get("wordbook_name") or "默认单词本",
                    "local_meaning": existing_word.get("meaning") or "",
                    "remote_meaning": remote_word.get("meaning") or "",
                    "local_deleted_at": existing_word.get("deleted_at"),
                    "remote_deleted_at": remote_word.get("deleted_at"),
                })
                continue

            existing_word_id = existing_word.get("id")
            if existing_word_id is None:
                continue
            merged_word = self._build_local_word_from_remote(
                remote_word, existing_word_id, existing_word=existing_word
            )
            words[words.index(existing_word)] = merged_word
            remote_index[remote_key] = merged_word
            word_key = merged_word.get("word_key")
            if word_key:
                remote_index[word_key] = merged_word
            updated += 1
            changed = True

        if changed:
            self._save_words(words)
        return {
            "added": added,
            "updated": updated,
            "skipped": skipped,
            "conflicts": conflicts,
            "total_remote": len([item for item in (remote_words or []) if isinstance(item, dict)]),
        }

    # ── Internal sync helpers ──

    def _sync_child_words_for_wordbook(
        self,
        wordbook_id: int,
        *,
        wordbook_name: str | None = None,
        wordbook_key: str | None = None,
        wordbook_remote_id: str | None = None,
    ) -> None:
        words = self._load_words()
        changed = False
        for word in words:
            if word.get("wordbook_id") != wordbook_id:
                continue
            if wordbook_name is not None and word.get("wordbook_name") != wordbook_name:
                word["wordbook_name"] = wordbook_name
                changed = True
            if wordbook_key is not None and word.get("wordbook_key") != wordbook_key:
                word["wordbook_key"] = wordbook_key
                changed = True
            if wordbook_remote_id is not None and word.get("wordbook_remote_id") != wordbook_remote_id:
                word["wordbook_remote_id"] = wordbook_remote_id
                changed = True
        if changed:
            self._save_words(words)

    def _build_local_wordbook_from_remote(
        self,
        remote_wordbook: dict[str, Any],
        local_id: int,
        existing_wordbook: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        now_iso = datetime.now(timezone.utc).isoformat()
        base = dict(existing_wordbook or {})
        base.update({
            "id": local_id,
            "remote_id": remote_wordbook.get("remote_id") or remote_wordbook.get("id") or base.get("remote_id"),
            "book_key": remote_wordbook.get("book_key") or base.get("book_key"),
            "name": remote_wordbook.get("name") or "默认单词本",
            "description": remote_wordbook.get("description") or "",
            "created_at": remote_wordbook.get("created_at") or base.get("created_at") or now_iso,
            "updated_at": remote_wordbook.get("updated_at") or base.get("updated_at") or now_iso,
            "deleted_at": remote_wordbook.get("deleted_at"),
            "version": max(int(remote_wordbook.get("version") or base.get("version") or 1), 1),
            "device_id": remote_wordbook.get("device_id") or base.get("device_id") or get_or_create_local_device_id(self.data_store),
            "sync_state": "synced",
            "last_synced_at": remote_wordbook.get("last_synced_at") or remote_wordbook.get("updated_at") or now_iso,
        })
        ensure_wordbook_sync_metadata(base, self.data_store)
        base["sync_state"] = "synced"
        return base

    def _build_local_word_from_remote(
        self,
        remote_word: dict[str, Any],
        local_id: int,
        existing_word: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        now_iso = datetime.now(timezone.utc).isoformat()
        base = dict(existing_word or {})
        resolved_wordbook_id = self._resolve_local_wordbook_id(remote_word)
        base.update({
            "id": local_id,
            "remote_id": remote_word.get("remote_id") or remote_word.get("id") or base.get("remote_id"),
            "word_key": remote_word.get("word_key") or base.get("word_key"),
            "word": remote_word.get("word") or "",
            "phonetic": remote_word.get("phonetic") or "",
            "meaning": remote_word.get("meaning") or "",
            "example": remote_word.get("example") or "",
            "wordbook_id": resolved_wordbook_id,
            "wordbook_remote_id": remote_word.get("wordbook_remote_id"),
            "wordbook_key": remote_word.get("wordbook_key"),
            "wordbook_name": remote_word.get("wordbook_name"),
            "created_at": remote_word.get("created_at") or base.get("created_at") or now_iso,
            "updated_at": remote_word.get("updated_at") or base.get("updated_at") or now_iso,
            "deleted_at": remote_word.get("deleted_at"),
            "version": max(int(remote_word.get("version") or base.get("version") or 1), 1),
            "device_id": remote_word.get("device_id") or base.get("device_id") or get_or_create_local_device_id(self.data_store),
            "sync_state": "synced",
            "last_synced_at": remote_word.get("last_synced_at") or remote_word.get("updated_at") or now_iso,
        })
        ensure_word_sync_metadata(base, self.data_store)
        base["sync_state"] = "synced"
        return base

    def _resolve_local_wordbook_id(self, remote_word: dict[str, Any]) -> int:
        remote_wordbook_id = str(remote_word.get("wordbook_remote_id") or "").strip()
        wordbook_key = str(remote_word.get("wordbook_key") or "").strip()
        wordbook_name = str(remote_word.get("wordbook_name") or "").strip() or "默认单词本"
        for wordbook in self.list_wordbooks(include_deleted=True):
            candidate_remote_id = str(wordbook.get("remote_id") or "").strip()
            candidate_book_key = str(wordbook.get("book_key") or "").strip()
            if remote_wordbook_id and candidate_remote_id == remote_wordbook_id:
                return int(wordbook.get("id") or 1)
            if wordbook_key and candidate_book_key == wordbook_key:
                return int(wordbook.get("id") or 1)
        try:
            return self.add_wordbook(wordbook_name)
        except ValueError:
            for wordbook in self.list_wordbooks(include_deleted=True):
                if str(wordbook.get("name") or "").strip() == wordbook_name:
                    return int(wordbook.get("id") or 1)
        return 1

    def _is_remote_wordbook_different(self, local_wordbook: dict[str, Any], remote_wordbook: dict[str, Any]) -> bool:
        for field_name in ("name", "description", "deleted_at", "updated_at", "version"):
            if local_wordbook.get(field_name) != remote_wordbook.get(field_name):
                return True
        return False

    def _is_remote_word_different(self, local_word: dict[str, Any], remote_word: dict[str, Any]) -> bool:
        for field_name in (
            "word", "phonetic", "meaning", "example",
            "deleted_at", "updated_at", "version",
            "wordbook_remote_id", "wordbook_key", "wordbook_name",
        ):
            if local_word.get(field_name) != remote_word.get(field_name):
                return True
        return False
