"""Sync & merge operations for LocalPersonalNoteRepository.

Extracted from _note.py to reduce its size from ~860 lines.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from sync.note_sync import build_note_sync_payload as _build_note_sync_module, ensure_note_sync_metadata, get_or_create_local_device_id


class NoteSyncMixin:
    """Sync & merge operations for note repository.

    Intended as a mixin with LocalPersonalNoteRepository.
    Requires self.data_store and access to CRUD helpers (_load_notes, _save_notes, etc.).
    """

    if TYPE_CHECKING:
        # 宿主契约（LocalPersonalNoteRepository 提供/注入）—— DI mixin 模式，
        # 运行时不产生实际属性，仅供 mypy 类型检查。
        data_store: Any
        def _load_notes(self) -> list[dict[str, Any]]: ...
        def _save_notes(self, notes: list[dict[str, Any]]) -> None: ...

    # ── Sync payload ──

    def build_note_sync_payload(self, note_id: int | str) -> dict[str, Any] | None:
        for note in self._load_notes():
            if note.get("id") == note_id:
                ensure_note_sync_metadata(note, self.data_store)
                return _build_note_sync_module(note)
        return None

    def list_pending_sync_notes(self) -> list[dict[str, Any]]:
        pending_notes = []
        for note in self._load_notes():
            ensure_note_sync_metadata(note, self.data_store)
            if note.get("sync_state") in {"local_only", "dirty"}:
                pending_notes.append(dict(note))
        return pending_notes

    def mark_note_as_synced(self, note_id: int | str, remote_note: dict[str, Any]) -> None:
        notes = self._load_notes()
        changed = False
        for note in notes:
            if note.get("id") != note_id:
                continue
            resolved_remote_key = self._resolve_remote_note_key(remote_note)
            if resolved_remote_key:
                note["remote_id"] = resolved_remote_key
            note["sync_state"] = "synced"
            remote_note = remote_note or {}
            note["last_synced_at"] = remote_note.get("last_synced_at") or remote_note.get("updated_at") or datetime.now(timezone.utc).isoformat()
            if remote_note.get("version") is not None:
                note["version"] = max(int(remote_note.get("version") or 1), 1)
            if remote_note.get("updated_at"):
                note["updated_at"] = remote_note.get("updated_at")
            if "deleted_at" in remote_note:
                note["deleted_at"] = remote_note.get("deleted_at")
            changed = True
            break
        if changed:
            self._save_notes(notes)

    def resolve_local_only_deleted_note(self, note_id: int | str) -> None:
        notes = self._load_notes()
        changed = False
        for note in notes:
            if note.get("id") != note_id:
                continue
            note["sync_state"] = "synced"
            note["last_synced_at"] = datetime.now(timezone.utc).isoformat()
            changed = True
            break
        if changed:
            self._save_notes(notes)

    # ── Remote lookup ──

    def find_remote_snapshot_note_by_remote_id(self, remote_id: str) -> dict[str, Any] | None:
        remote_key = str(remote_id or "").strip()
        if not remote_key:
            return None
        for note in self.get_remote_snapshot_notes():
            if not isinstance(note, dict):
                continue
            if self._resolve_remote_note_key(note) == remote_key:
                return dict(note)
        return None

    def find_cloud_note_by_remote_id(self, remote_id: str) -> dict[str, Any] | None:
        return self.find_remote_snapshot_note_by_remote_id(remote_id)

    def overwrite_note_with_remote(self, note_id: int | str, remote_note: dict[str, Any]) -> bool:
        notes = self._load_notes()
        for index, note in enumerate(notes):
            if note.get("id") != note_id:
                continue
            notes[index] = self._build_local_note_from_remote(
                remote_note, local_id=note_id, existing_note=note,
            )
            self._save_notes(notes)
            return True
        return False

    # ── Merge ──

    def merge_remote_notes(self, remote_notes: list[dict[str, Any]], prefer_remote: bool = False) -> dict[str, Any]:
        notes = self._load_notes()
        remote_index = {
            str(note.get("remote_id")): note
            for note in notes
            if isinstance(note.get("remote_id"), str) and note.get("remote_id")
        }
        changed = False
        added = 0
        updated = 0
        skipped = 0
        conflicts: list[dict[str, Any]] = []
        next_id = self.data_store.next_id(notes) if self.data_store is not None else 1

        for remote_note in remote_notes or []:
            if not isinstance(remote_note, dict):
                continue
            remote_key = self._resolve_remote_note_key(remote_note)
            if not remote_key:
                continue

            existing_note = remote_index.get(remote_key)
            if existing_note is None:
                notes.append(self._build_local_note_from_remote(remote_note, local_id=next_id))
                remote_index[remote_key] = notes[-1]
                next_id += 1
                added += 1
                changed = True
                continue

            if not self._is_remote_note_different(existing_note, remote_note):
                if existing_note.get("sync_state") != "synced":
                    existing_note["sync_state"] = "synced"
                    existing_note["last_synced_at"] = remote_note.get("last_synced_at") or remote_note.get("updated_at")
                    updated += 1
                    changed = True
                continue

            if existing_note.get("sync_state") in {"local_only", "dirty"} and not prefer_remote:
                skipped += 1
                conflicts.append({
                    "local_id": existing_note.get("id"),
                    "remote_id": remote_key,
                    "title": existing_note.get("title") or remote_note.get("title") or "未命名笔记",
                    "local_title": existing_note.get("title") or "未命名笔记",
                    "remote_title": remote_note.get("title") or "未命名笔记",
                    "local_updated_at": existing_note.get("updated_at") or existing_note.get("created_at"),
                    "remote_updated_at": remote_note.get("updated_at") or remote_note.get("created_at"),
                })
                continue

            merged_note = self._build_local_note_from_remote(
                remote_note,
                # existing_note 来自 _load_notes() 的既有笔记，id 必然在场
                # （用 [] 而非 .get() 显式化该不变量，避免静默把 None 当 id）
                local_id=existing_note["id"],
                existing_note=existing_note,
            )
            existing_index = notes.index(existing_note)
            notes[existing_index] = merged_note
            remote_index[remote_key] = merged_note
            updated += 1
            changed = True

        if changed:
            self._save_notes(notes)

        return {
            "added": added,
            "updated": updated,
            "skipped": skipped,
            "conflicts": conflicts,
            "total_remote": len([item for item in (remote_notes or []) if isinstance(item, dict)]),
        }

    # ── Internal helpers ──

    def _resolve_remote_note_key(self, remote_note: dict[str, Any]) -> str | None:
        for candidate in (remote_note.get("remote_id"), remote_note.get("id")):
            if isinstance(candidate, str) and candidate.strip():
                return candidate.strip()
        return None

    def _normalize_remote_tags(self, remote_note: dict[str, Any]) -> list[str]:
        tags = remote_note.get("tags")
        if not isinstance(tags, list):
            return []
        return [str(tag).strip() for tag in tags if str(tag).strip()]

    def _build_local_note_from_remote(
        self,
        remote_note: dict[str, Any],
        *,
        local_id: int | str,
        existing_note: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        now_iso = datetime.now(timezone.utc).isoformat()
        remote_key = self._resolve_remote_note_key(remote_note)
        merged_note = dict(existing_note or {})

        if existing_note and self._is_remote_note_different(existing_note, remote_note):
            previous_version = {
                "title": existing_note.get("title"),
                "content": existing_note.get("content"),
                "tags": list(existing_note.get("tags") or []),
                "category": existing_note.get("category"),
                "updated_at": existing_note.get("updated_at"),
            }
            previous_versions = list(existing_note.get("versions") or [])
            merged_note["versions"] = [previous_version] + previous_versions[:4]
        else:
            merged_note["versions"] = list((existing_note or {}).get("versions") or [])

        merged_note.update({
            "id": local_id,
            "remote_id": remote_key,
            "title": remote_note.get("title") or "未命名笔记",
            "content": remote_note.get("content") or "",
            "tags": self._normalize_remote_tags(remote_note),
            "category": remote_note.get("category") or "笔记",
            "folder_path": remote_note.get("folder_path") or "/",
            "favorite": bool(remote_note.get("favorite", False)),
            "related_news_url": remote_note.get("related_news_url"),
            "linked_task_id": remote_note.get("linked_task_id"),
            "created_at": remote_note.get("created_at") or merged_note.get("created_at") or now_iso,
            "updated_at": remote_note.get("updated_at") or merged_note.get("updated_at") or now_iso,
            "deleted_at": remote_note.get("deleted_at"),
            "version": max(int(remote_note.get("version") or 1), 1),
            "device_id": remote_note.get("device_id") or merged_note.get("device_id") or get_or_create_local_device_id(self.data_store),
            "sync_state": "synced",
            "last_synced_at": remote_note.get("last_synced_at") or remote_note.get("updated_at") or now_iso,
        })
        ensure_note_sync_metadata(merged_note, self.data_store)
        merged_note["sync_state"] = "synced"
        return merged_note

    def _is_remote_note_different(self, local_note: dict[str, Any], remote_note: dict[str, Any]) -> bool:
        comparable_fields = (
            "title", "content", "category", "folder_path",
            "updated_at", "deleted_at", "version",
            "related_news_url", "linked_task_id",
        )
        for field_name in comparable_fields:
            if local_note.get(field_name) != remote_note.get(field_name):
                return True
        if bool(local_note.get("favorite", False)) != bool(remote_note.get("favorite", False)):
            return True
        if list(local_note.get("tags") or []) != self._normalize_remote_tags(remote_note):
            return True
        return False

    # ── Remote snapshot helpers ──

    def get_remote_snapshot_notes(self) -> list[dict[str, Any]]:
        if self.data_store is None:
            return []
        data = self.data_store.load_json("remote_snapshot_notes", [])
        return data or []

    def set_remote_snapshot_notes(self, notes: list[dict[str, Any]]) -> None:
        if self.data_store is None:
            return
        self.data_store.save_json("remote_snapshot_notes", notes)

    def get_cloud_notes(self) -> list[dict[str, Any]]:
        return self.get_remote_snapshot_notes()

    def set_cloud_notes(self, notes: list[dict[str, Any]]) -> None:
        self.set_remote_snapshot_notes(notes)
