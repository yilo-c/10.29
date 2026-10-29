from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from sync.note_sync import ensure_note_sync_metadata

from ._note_sync_mixin import NoteSyncMixin

logger = logging.getLogger("siyi.repositories")


def _sanitize_note_content(content: Any) -> Any:
    """清洗笔记 content 字段：移除 null 字节（\\x00 / \\u0000）。

    历史 bug：AI 流式响应（尤其本地 Ollama）或剪贴板粘贴可能携带 null 字节，
    全链路无过滤，导致数据库中出现内容全是 \\u0000 的"伪空笔记"。
    null 字节在 JSON 往返中完整存活，且对用户不可见，污染数据。
    """
    if not isinstance(content, str):
        return content
    if "\x00" not in content:
        return content
    cleaned = content.replace("\x00", "")
    logger.debug("sanitized note content: removed null bytes (len %d → %d)", len(content), len(cleaned))
    return cleaned


def _sanitize_note_fields(note: dict[str, Any]) -> dict[str, Any]:
    """统一清洗笔记字段（content / title）。"""
    if "content" in note and isinstance(note["content"], str):
        note["content"] = _sanitize_note_content(note["content"])
    if "title" in note and isinstance(note["title"], str):
        note["title"] = _sanitize_note_content(note["title"])
    return note


class LocalPersonalNoteRepository(NoteSyncMixin):
    """鏈?湴笔记?仓储，直鎺ヨ?鍐?notes.json、folders.json 与远绔?揩照文浠躲€"""

    def __init__(self, data_store):
        self.data_store = data_store
        self.content_created_callback = None  # (content_type, id, title) -> None

    # 鈹€鈹€ Data access helpers 鈹€鈹€

    def _load_notes(self) -> list[dict[str, Any]]:
        return self.data_store.load_json("notes", []) if self.data_store is not None else []

    def _save_notes(self, notes: list[dict[str, Any]]) -> None:
        if self.data_store is None:
            return
        self.data_store.save_json("notes", notes)

    # 鈹€鈹€ Notes CRUD 鈹€鈹€

    def get_note(self, note_id: int | str) -> dict[str, Any] | None:
        """按 ID 直接查找单条笔记，跳过 list_notes 的全量规范化，性能更优。"""
        for note in self._load_notes():
            if note.get("id") == note_id:
                if note.get("deleted_at"):
                    return None
                return note
        return None

    def list_notes(self, include_deleted: bool = False) -> list[dict[str, Any]]:
        notes = self._load_notes()
        changed = False
        for note in notes:
            if "folder_path" not in note:
                note["folder_path"] = "/"
                changed = True
            if "favorite" not in note:
                note["favorite"] = False
                changed = True
            if ensure_note_sync_metadata(note, self.data_store):
                changed = True
        if changed:
            self._save_notes(notes)
        if include_deleted:
            return notes
        return [note for note in notes if not note.get("deleted_at")]

    def add_note(self, note_info: dict[str, Any]) -> int | str:
        if self.data_store is None:
            return 0
        notes = self._load_notes()
        desired_id = note_info.get("id")

        # 去重：相同 title + content 的不再新增（走更新）
        title = (note_info.get("title") or "").strip()
        content = (note_info.get("content") or "").strip()
        if title and content:
            for existing in notes:
                if existing.get("deleted_at"):
                    continue
                if (existing.get("title") or "").strip() == title and (existing.get("content") or "").strip() == content:
                    logger.info("duplicate note detected (title=%r), updating instead", title[:30])
                    self.update_note(existing["id"], note_info, keep_version=False)
                    return existing["id"]

        if desired_id and all(note.get("id") != desired_id for note in notes):
            new_id = desired_id
        else:
            new_id = self.data_store.next_id(notes)
        note = dict(note_info)
        _sanitize_note_fields(note)  # 清洗 null 字节等脏数据
        note["id"] = new_id
        now = datetime.now(timezone.utc).isoformat()
        note.setdefault("created_at", now)
        note.setdefault("updated_at", now)
        note.setdefault("deleted_at", None)
        note.setdefault("category", "新闻收藏")
        note.setdefault("tags", [])
        note.setdefault("versions", [])
        note.setdefault("favorite", False)
        note.setdefault("folder_path", "/")
        ensure_note_sync_metadata(note, self.data_store, mark_dirty=True)
        notes.append(note)
        self._save_notes(notes)
        if self.content_created_callback is not None:
            title = note_info.get("title", "") or note_info.get("text", "")
            self.content_created_callback("note", new_id, title)
        return new_id

    def update_note(
        self,
        note_id: int | str,
        new_info: dict[str, Any],
        keep_version: bool = True,
        max_versions: int = 5,
    ) -> bool:
        """更新笔记。S0-1：返回命中信号 —— 真实更新为 True，id 不存在为 False。

        旧实现未命中时静默返回 None，调用方无从分辨「写成功」与「空写」，
        ghost update 因此能一路冒充成功（假审计 + 假广播）。
        """
        notes = self._load_notes()
        for note in notes:
            if note.get("id") == note_id:
                if keep_version:
                    previous = {
                        "content": note.get("content"),
                        "title": note.get("title"),
                        "tags": note.get("tags", []),
                        "category": note.get("category"),
                        "updated_at": note.get("updated_at"),
                    }
                    note.setdefault("versions", []).insert(0, previous)
                    note["versions"] = note["versions"][:max_versions]
                note.update(new_info)
                _sanitize_note_fields(note)  # 清洗 null 字节等脏数据
                if "version" not in new_info:
                    note["version"] = max(int(note.get("version") or 0), 1) + 1
                # V2.1 批次3（P1-3）：仅当 new_info 未提供 updated_at 时回填 now——
                # 同步合并（sync_state 来自远端）必须保留远端时间戳，避免本地 now 覆盖。
                if "updated_at" not in new_info:
                    note["updated_at"] = datetime.now(timezone.utc).isoformat()
                note.setdefault("folder_path", "/")
                note.setdefault("favorite", False)
                ensure_note_sync_metadata(note, self.data_store, mark_dirty="sync_state" not in new_info)
                self._save_notes(notes)
                return True
        # S0-1：未命中——不落盘、返回 False（杜绝 ghost update 静默空写仍被上层记为成功）
        return False

    def delete_note(self, note_id: int | str, soft: bool = True) -> bool:
        notes = self._load_notes()
        if soft:
            for note in notes:
                if note.get("id") == note_id:
                    note["deleted_at"] = datetime.now(timezone.utc).isoformat()
                    note["version"] = max(int(note.get("version") or 0), 1) + 1
                    ensure_note_sync_metadata(note, self.data_store, mark_dirty=True)
                    break
        else:
            notes = [note for note in notes if note.get("id") != note_id]
        self._save_notes(notes)

    def restore_note(self, note_id: int | str) -> None:
        notes = self._load_notes()
        for note in notes:
            if note.get("id") == note_id:
                note["deleted_at"] = None
                note["updated_at"] = datetime.now(timezone.utc).isoformat()
                note["version"] = max(int(note.get("version") or 0), 1) + 1
                ensure_note_sync_metadata(note, self.data_store, mark_dirty=True)
                break
        self._save_notes(notes)

    # 鈹€鈹€ Folder management 鈹€鈹€

    def get_folders(self) -> list[str]:
        if self.data_store is None:
            return ["/"]
        folders = self.data_store.load_json("folders", [])
        if "/" not in folders:
            folders.insert(0, "/")
            self.data_store.save_json("folders", folders)
        return folders

    def add_folder(self, path: str) -> None:
        if self.data_store is None:
            return

        def _apply(folders: Any) -> list[str]:
            if not isinstance(folders, list):
                folders = []
            if "/" not in folders:
                folders.insert(0, "/")
            if path not in folders:
                folders.append(path)
            return folders

        self.data_store.update_json("folders", [], _apply)

    def rename_folder(self, old_path: str, new_path: str) -> None:
        if self.data_store is None:
            return
        folders = self.get_folders()
        updated = []
        for folder in folders:
            if folder == old_path or folder.startswith(old_path.rstrip("/") + "/"):
                suffix = folder[len(old_path) :]
                updated.append(new_path + suffix)
            else:
                updated.append(folder)
        self.data_store.save_json("folders", updated)
        notes = self._load_notes()
        for note in notes:
            folder_path = note.get("folder_path", "/")
            if folder_path == old_path or folder_path.startswith(old_path.rstrip("/") + "/"):
                note["folder_path"] = new_path + folder_path[len(old_path) :]
        self._save_notes(notes)

    def delete_folder(self, path: str) -> None:
        if self.data_store is None or path == "/":
            return
        folders = self.get_folders()
        folders = [folder for folder in folders if not (folder == path or folder.startswith(path.rstrip("/") + "/"))]
        self.data_store.save_json("folders", folders)
        notes = self._load_notes()
        for note in notes:
            folder_path = note.get("folder_path", "/")
            if folder_path == path or folder_path.startswith(path.rstrip("/") + "/"):
                note["folder_path"] = "/"
        self._save_notes(notes)

    def search_notes(self, query: str, max_results: int = 50) -> list[dict[str, Any]]:
        """Search notes by query text using FTS."""
        from data.local_storage import LocalDataStore

        all_notes = self.list_notes(include_deleted=False)
        return LocalDataStore.fts_search_items(
            all_notes,
            query,
            ["title", "content", "tags", "category"],
            max_results=max_results,
        )
