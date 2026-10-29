from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from sync.vocabulary_sync import (
    build_wordbook_key,
    ensure_word_sync_metadata,
    ensure_wordbook_sync_metadata,
)

from ._vocab_sync_mixin import VocabSyncMixin

logger = logging.getLogger("siyi.repositories")


class LocalVocabularyRepository(VocabSyncMixin):
    """本地词汇仓储，读写 words.json 与 wordbooks.json。"""

    def __init__(self, data_store):
        self.data_store = data_store

    # ── Data access helpers ──

    def _load_words(self) -> list[dict[str, Any]]:
        return self.data_store.load_json("words", []) if self.data_store is not None else []

    def _save_words(self, words: list[dict[str, Any]]) -> None:
        if self.data_store is None:
            return
        self.data_store.save_json("words", words)

    def _load_wordbooks(self) -> list[dict[str, Any]]:
        if self.data_store is None:
            return []
        wordbooks = self.data_store.load_json("wordbooks", [])
        if not wordbooks:
            wordbooks = [{"id": 1, "name": "默认单词本", "created_at": datetime.now(timezone.utc).isoformat()}]
            self.data_store.save_json("wordbooks", wordbooks)
        return wordbooks

    def _save_wordbooks(self, wordbooks: list[dict[str, Any]]) -> None:
        if self.data_store is None:
            return
        self.data_store.save_json("wordbooks", wordbooks)

    # ── Wordbook CRUD ──

    def list_wordbooks(self, include_deleted: bool = False) -> list[dict[str, Any]]:
        wordbooks = self._load_wordbooks()
        changed = False
        max_id = max((wordbook.get("id", 0) or 0 for wordbook in wordbooks), default=0)
        for wordbook in wordbooks:
            if not wordbook.get("id"):
                max_id += 1
                wordbook["id"] = max_id
                changed = True
            if not wordbook.get("created_at"):
                wordbook["created_at"] = datetime.now(timezone.utc).isoformat()
                changed = True
            if not wordbook.get("updated_at"):
                wordbook["updated_at"] = wordbook.get("created_at")
                changed = True
            if "description" not in wordbook:
                wordbook["description"] = ""
                changed = True
            if ensure_wordbook_sync_metadata(wordbook, self.data_store):
                changed = True
        if changed:
            self._save_wordbooks(wordbooks)
        normalized = sorted(wordbooks, key=lambda item: str(item.get("updated_at") or item.get("created_at") or ""))
        if include_deleted:
            return normalized
        return [wordbook for wordbook in normalized if not wordbook.get("deleted_at")]

    def list_words(self, include_deleted: bool = False) -> list[dict[str, Any]]:
        words = self._load_words()
        changed = False
        max_id = max((word.get("id", 0) or 0 for word in words), default=0)
        wordbook_map = {wordbook.get("id"): wordbook for wordbook in self.list_wordbooks(include_deleted=True)}
        for word in words:
            if not word.get("id"):
                max_id += 1
                word["id"] = max_id
                changed = True
            if not word.get("created_at"):
                word["created_at"] = datetime.now(timezone.utc).isoformat()
                changed = True
            if not word.get("updated_at"):
                word["updated_at"] = word.get("created_at")
                changed = True
            wordbook = wordbook_map.get(word.get("wordbook_id"))
            desired_wordbook_key = wordbook.get("book_key") if isinstance(wordbook, dict) else None
            desired_wordbook_remote_id = wordbook.get("remote_id") if isinstance(wordbook, dict) else None
            desired_wordbook_name = wordbook.get("name") if isinstance(wordbook, dict) else None
            if word.get("wordbook_key") != desired_wordbook_key:
                word["wordbook_key"] = desired_wordbook_key
                changed = True
            if desired_wordbook_remote_id and word.get("wordbook_remote_id") != desired_wordbook_remote_id:
                word["wordbook_remote_id"] = desired_wordbook_remote_id
                changed = True
            if desired_wordbook_name and word.get("wordbook_name") != desired_wordbook_name:
                word["wordbook_name"] = desired_wordbook_name
                changed = True
            if ensure_word_sync_metadata(word, self.data_store):
                changed = True
        if changed:
            self._save_words(words)
        normalized = sorted(words, key=lambda item: str(item.get("updated_at") or item.get("created_at") or ""), reverse=True)
        if include_deleted:
            return normalized
        return [word for word in normalized if not word.get("deleted_at")]

    def get_all_wordbooks(self) -> list[dict[str, Any]]:
        return self.list_wordbooks()

    def get_all_words(self) -> list[dict[str, Any]]:
        return self.list_words()

    def search_words(self, keyword: str) -> list[dict[str, Any]]:
        keyword_lower = keyword.lower()
        return [
            word for word in self.list_words()
            if keyword_lower in str(word.get("word", "")).lower()
            or keyword_lower in str(word.get("meaning", "")).lower()
        ]

    def add_wordbook(self, name: str) -> int:
        wordbooks = self._load_wordbooks()
        for wordbook in wordbooks:
            if wordbook.get("name") == name:
                raise ValueError(f"单词本 '{name}' 已存在")
        new_id = max([wordbook.get("id", 0) for wordbook in wordbooks], default=0) + 1
        wordbooks.append({
            "id": new_id,
            "name": name,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "description": "",
            "deleted_at": None,
        })
        ensure_wordbook_sync_metadata(wordbooks[-1], self.data_store, mark_dirty=True)
        self._save_wordbooks(wordbooks)
        return new_id

    def rename_wordbook(self, wordbook_id: int, new_name: str) -> None:
        wordbooks = self._load_wordbooks()
        words = self._load_words()
        for wordbook in wordbooks:
            if wordbook.get("id") == wordbook_id:
                wordbook["name"] = new_name
                wordbook["version"] = max(int(wordbook.get("version") or 0), 1) + 1
                wordbook["updated_at"] = datetime.now(timezone.utc).isoformat()
                ensure_wordbook_sync_metadata(wordbook, self.data_store, mark_dirty=True)
                for word in words:
                    if word.get("wordbook_id") != wordbook_id:
                        continue
                    word["wordbook_name"] = new_name
                    word["updated_at"] = datetime.now(timezone.utc).isoformat()
                    word["version"] = max(int(word.get("version") or 0), 1) + 1
                    ensure_word_sync_metadata(word, self.data_store, mark_dirty=True)
                self._save_words(words)
                self._save_wordbooks(wordbooks)
                return
        raise ValueError(f"找不到ID为 {wordbook_id} 的单词本")

    def delete_wordbook(self, wordbook_id: int) -> None:
        if wordbook_id == 1:
            raise ValueError("不允许删除默认单词本")
        wordbooks = self._load_wordbooks()
        words = self._load_words()
        deleted_at = datetime.now(timezone.utc).isoformat()
        for wordbook in wordbooks:
            if wordbook.get("id") != wordbook_id:
                continue
            wordbook["deleted_at"] = deleted_at
            wordbook["updated_at"] = deleted_at
            wordbook["version"] = max(int(wordbook.get("version") or 0), 1) + 1
            ensure_wordbook_sync_metadata(wordbook, self.data_store, mark_dirty=True)
            break
        self._save_wordbooks(wordbooks)
        for word in words:
            if word.get("wordbook_id", 1) != wordbook_id:
                continue
            word["deleted_at"] = deleted_at
            word["updated_at"] = deleted_at
            word["version"] = max(int(word.get("version") or 0), 1) + 1
            ensure_word_sync_metadata(word, self.data_store, mark_dirty=True)
        self._save_words(words)

    # ── Word CRUD ──

    def get_words_by_wordbook(self, wordbook_id: int) -> list[dict[str, Any]]:
        return [word for word in self.list_words() if word.get("wordbook_id", 1) == wordbook_id]

    def search_words_in_wordbook(self, keyword: str, wordbook_id: int) -> list[dict[str, Any]]:
        keyword_lower = keyword.lower()
        return [
            word for word in self.get_words_by_wordbook(wordbook_id)
            if keyword_lower in str(word.get("word", "")).lower()
            or keyword_lower in str(word.get("meaning", "")).lower()
        ]

    def add_word(self, word_data: dict[str, Any]) -> int:
        if self.data_store is None:
            return 0
        words = self._load_words()
        new_id = self.data_store.next_id(words)
        entry = dict(word_data)
        entry["id"] = new_id
        now = datetime.now(timezone.utc).isoformat()
        entry.setdefault("created_at", now)
        entry.setdefault("updated_at", now)
        entry.setdefault("deleted_at", None)
        wordbook = next(
            (item for item in self.list_wordbooks(include_deleted=True) if item.get("id") == entry.get("wordbook_id")),
            None,
        )
        if wordbook is not None:
            entry["wordbook_key"] = wordbook.get("book_key") or build_wordbook_key(wordbook)
            entry["wordbook_remote_id"] = wordbook.get("remote_id")
            entry["wordbook_name"] = wordbook.get("name")
        ensure_word_sync_metadata(entry, self.data_store, mark_dirty=True)
        words.append(entry)
        self._save_words(words)
        return new_id

    def update_word(self, word_id: int, updated_data: dict[str, Any]) -> None:
        words = self._load_words()
        for word in words:
            if word.get("id") == word_id:
                word.update(updated_data)
                word["updated_at"] = datetime.now(timezone.utc).isoformat()
                word["version"] = max(int(word.get("version") or 0), 1) + 1
                wordbook = next(
                    (item for item in self.list_wordbooks(include_deleted=True) if item.get("id") == word.get("wordbook_id")),
                    None,
                )
                if wordbook is not None:
                    word["wordbook_key"] = wordbook.get("book_key") or build_wordbook_key(wordbook)
                    word["wordbook_remote_id"] = wordbook.get("remote_id")
                    word["wordbook_name"] = wordbook.get("name")
                ensure_word_sync_metadata(word, self.data_store, mark_dirty=True)
                break
        self._save_words(words)

    def delete_word(self, word_id: int) -> None:
        words = self._load_words()
        for word in words:
            if word.get("id") != word_id:
                continue
            deleted_at = datetime.now(timezone.utc).isoformat()
            word["deleted_at"] = deleted_at
            word["updated_at"] = deleted_at
            word["version"] = max(int(word.get("version") or 0), 1) + 1
            ensure_word_sync_metadata(word, self.data_store, mark_dirty=True)
            break
        self._save_words(words)

    # ── Review (SM-2 + FSRS dual-write) ──

    def record_review(self, word_id: int, quality: int) -> tuple[int, float, int, str] | None:
        """Apply a review score to a word, dual-writing SM-2 and FSRS fields.

        Args:
          word_id: 词条 ID
          quality: SM-2 0-5 评分

        Returns:
          (repetitions, efactor, interval, next_review) 4-tuple，或 None（词条不存在）。
          next_review 取 SM-2 与 FSRS due 较早者（"宁紧勿松"）。
        """
        from ._fsrs_scheduler import review_with_dual_write

        words = self._load_words()
        target = next((w for w in words if w.get("id") == word_id), None)
        if target is None:
            return None

        updates = review_with_dual_write(target, quality)
        target.update(updates)
        target["updated_at"] = datetime.now(timezone.utc).isoformat()
        target["version"] = max(int(target.get("version") or 0), 1) + 1
        ensure_word_sync_metadata(target, self.data_store, mark_dirty=True)
        self._save_words(words)

        return (
            updates["repetitions"],
            updates["efactor"],
            updates["interval"],
            updates["next_review"],
        )

    def rollback_all_fsrs(self) -> int:
        """Strip all FSRS fields from every word, keeping SM-2 fields intact.

        Returns the count of words that had FSRS fields removed.
        """
        from ._fsrs_scheduler import rollback_fsrs

        words = self._load_words()
        cleaned_count = 0
        for word in words:
            if not any(k in word for k in (
                "fsrs_state", "fsrs_stability", "fsrs_difficulty",
                "fsrs_step", "fsrs_due", "fsrs_last_review",
                "fsrs_enabled", "fsrs_last_rating",
            )):
                continue
            cleaned = rollback_fsrs(word)
            word.clear()
            word.update(cleaned)
            word["updated_at"] = datetime.now(timezone.utc).isoformat()
            word["version"] = max(int(word.get("version") or 0), 1) + 1
            ensure_word_sync_metadata(word, self.data_store, mark_dirty=True)
            cleaned_count += 1
        if cleaned_count > 0:
            self._save_words(words)
        return cleaned_count
