from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from ._protocols import *

logger = logging.getLogger("siyi.repositories")
from sync.note_sync import (
    build_note_sync_payload,
    ensure_note_sync_metadata,
    get_or_create_local_device_id,
)
from sync.translation_sync import build_translation_sync_payload, ensure_translation_sync_metadata
class LocalTranslationHistoryRepository:

    """本地翻译历史仓储，读写 translations.json。"""



    def __init__(self, data_store):

        self.data_store = data_store



    def _load_translation_records(self) -> list[dict[str, Any]]:

        return self.data_store.load_json("translations", []) if self.data_store is not None else []



    def _save_translation_records(self, records: list[dict[str, Any]]) -> None:

        if self.data_store is None:

            return

        self.data_store.save_json("translations", records)



    def list_translation_records(self, include_deleted: bool = False) -> list[dict[str, Any]]:

        records = self._load_translation_records()

        changed = False

        max_id = max((record.get("id", 0) or 0 for record in records), default=0)

        for record in records:

            if not record.get("id"):

                max_id += 1

                record["id"] = max_id

                changed = True

            if not record.get("updated_at"):

                record["updated_at"] = record.get("created_at") or datetime.now(timezone.utc).isoformat()

                changed = True

            if ensure_translation_sync_metadata(record, self.data_store):

                changed = True

        if changed:

            self._save_translation_records(records)

        normalized_records = sorted(

            records,

            key=lambda item: str(item.get("updated_at") or item.get("created_at") or ""),

            reverse=True,

        )

        if include_deleted:

            return normalized_records

        return [record for record in normalized_records if not record.get("deleted_at")]



    def add_translation_record(self, text: str, result: Any, from_code: str, to_code: str) -> int:

        if self.data_store is None:

            return 0

        records = self._load_translation_records()

        now = datetime.now(timezone.utc).isoformat()

        new_id = self.data_store.next_id(records)

        entry = {

            "id": new_id,

            "source_text": text,

            "translated_text": result,

            "from_lang": from_code,

            "to_lang": to_code,

            "created_at": now,

            "updated_at": now,

            "deleted_at": None,

            "raw_result": result,

        }

        ensure_translation_sync_metadata(entry, self.data_store, mark_dirty=True)

        records.append(entry)

        self._save_translation_records(records)

        return new_id



    def delete_translation_record(self, record_id: int, soft: bool = True) -> None:

        records = self._load_translation_records()

        if soft:

            for record in records:

                if record.get("id") != record_id:

                    continue

                record["deleted_at"] = datetime.now(timezone.utc).isoformat()

                record["updated_at"] = record["deleted_at"]

                record["version"] = max(int(record.get("version") or 0), 1) + 1

                ensure_translation_sync_metadata(record, self.data_store, mark_dirty=True)

                break

        else:

            records = [record for record in records if record.get("id") != record_id]

        self._save_translation_records(records)



    def build_translation_sync_payload(self, record_id: int) -> dict[str, Any] | None:

        for record in self.list_translation_records(include_deleted=True):

            if record.get("id") == record_id:

                return build_translation_sync_payload(record)

        return None



    def list_pending_sync_translations(self) -> list[dict[str, Any]]:

        return [

            dict(record)

            for record in self.list_translation_records(include_deleted=True)

            if record.get("sync_state") in {"local_only", "dirty"}

        ]



    def mark_translation_as_synced(self, record_id: int, remote_record: dict[str, Any]) -> None:

        records = self._load_translation_records()

        changed = False

        for record in records:

            if record.get("id") != record_id:

                continue

            remote_id = remote_record.get("remote_id") or remote_record.get("id")

            if isinstance(remote_id, str) and remote_id.strip():

                record["remote_id"] = remote_id.strip()

            if remote_record.get("history_key"):

                record["history_key"] = remote_record.get("history_key")

            record["sync_state"] = "synced"

            record["last_synced_at"] = remote_record.get("last_synced_at") or remote_record.get("updated_at") or datetime.now(timezone.utc).isoformat()

            if remote_record.get("updated_at"):

                record["updated_at"] = remote_record.get("updated_at")

            if remote_record.get("created_at"):

                record["created_at"] = remote_record.get("created_at")

            if remote_record.get("version") is not None:

                record["version"] = max(int(remote_record.get("version") or 1), 1)

            if "deleted_at" in remote_record:

                record["deleted_at"] = remote_record.get("deleted_at")

            changed = True

            break

        if changed:

            self._save_translation_records(records)



    def resolve_local_only_deleted_translation(self, record_id: int) -> None:

        if self.data_store is None:

            return

        records = [record for record in self._load_translation_records() if record.get("id") != record_id]

        self._save_translation_records(records)



    def get_remote_snapshot_translations(self) -> list[dict[str, Any]]:

        if self.data_store is None:

            return []

        data = self.data_store.load_json("remote_snapshot_translations", [])

        return data or []



    def set_remote_snapshot_translations(self, records: list[dict[str, Any]]) -> None:

        if self.data_store is None:

            return

        self.data_store.save_json("remote_snapshot_translations", records)



    def get_cloud_translations(self) -> list[dict[str, Any]]:

        return self.get_remote_snapshot_translations()



    def set_cloud_translations(self, records: list[dict[str, Any]]) -> None:

        self.set_remote_snapshot_translations(records)



    def find_remote_snapshot_translation_by_remote_id(self, remote_id: str) -> dict[str, Any] | None:

        remote_key = str(remote_id or "").strip()

        if not remote_key:

            return None

        for record in self.get_remote_snapshot_translations():

            if not isinstance(record, dict):

                continue

            candidate = str(record.get("remote_id") or record.get("id") or "").strip()

            if candidate == remote_key:

                return dict(record)

        return None



    def find_cloud_translation_by_remote_id(self, remote_id: str) -> dict[str, Any] | None:

        return self.find_remote_snapshot_translation_by_remote_id(remote_id)



    def overwrite_translation_with_remote(self, record_id: int, remote_record: dict[str, Any]) -> bool:

        records = self._load_translation_records()

        for index, record in enumerate(records):

            if record.get("id") != record_id:

                continue

            records[index] = self._build_local_translation_from_remote(

                remote_record,

                local_id=record_id,

                existing_record=record,

            )

            self._save_translation_records(records)

            return True

        return False



    def merge_remote_translations(self, remote_records: list[dict[str, Any]], prefer_remote: bool = False) -> dict[str, Any]:

        records = self.list_translation_records(include_deleted=True)

        remote_index = {}

        for record in records:

            remote_id = record.get("remote_id")

            history_key = record.get("history_key")

            if isinstance(remote_id, str) and remote_id:

                remote_index[remote_id] = record

            if isinstance(history_key, str) and history_key and history_key not in remote_index:

                remote_index[history_key] = record



        changed = False

        added = 0

        updated = 0

        skipped = 0

        conflicts: list[dict[str, Any]] = []

        next_id = self.data_store.next_id(records) if self.data_store is not None else 1



        for remote_record in remote_records or []:

            if not isinstance(remote_record, dict):

                continue

            remote_key = str(remote_record.get("remote_id") or remote_record.get("id") or remote_record.get("history_key") or "").strip()

            if not remote_key:

                continue

            existing_record = remote_index.get(remote_key)

            history_key = str(remote_record.get("history_key") or "").strip()

            if existing_record is None and history_key:

                existing_record = remote_index.get(history_key)



            if existing_record is None:

                local_record = self._build_local_translation_from_remote(remote_record, local_id=next_id)

                records.append(local_record)

                remote_index[remote_key] = local_record

                if local_record.get("history_key"):

                    remote_index[local_record.get("history_key")] = local_record

                next_id += 1

                added += 1

                changed = True

                continue



            if not self._is_remote_translation_different(existing_record, remote_record):

                if existing_record.get("sync_state") != "synced":

                    existing_record["sync_state"] = "synced"

                    existing_record["last_synced_at"] = remote_record.get("last_synced_at") or remote_record.get("updated_at")

                    updated += 1

                    changed = True

                continue



            if existing_record.get("sync_state") in {"local_only", "dirty"} and not prefer_remote:

                skipped += 1

                conflicts.append(

                    {

                        "local_id": existing_record.get("id"),

                        "remote_id": str(remote_record.get("remote_id") or remote_record.get("id") or remote_key),

                        "local_source_text": existing_record.get("source_text") or "",

                        "remote_source_text": remote_record.get("source_text") or "",

                        "local_translated_text": existing_record.get("translated_text") or "",

                        "remote_translated_text": remote_record.get("translated_text") or "",

                        "local_updated_at": existing_record.get("updated_at") or existing_record.get("created_at"),

                        "remote_updated_at": remote_record.get("updated_at") or remote_record.get("created_at"),

                    }

                )

                continue



            merged_record = self._build_local_translation_from_remote(

                remote_record,

                local_id=existing_record.get("id"),

                existing_record=existing_record,

            )

            existing_index = records.index(existing_record)

            records[existing_index] = merged_record

            remote_index[remote_key] = merged_record

            if merged_record.get("history_key"):

                remote_index[merged_record.get("history_key")] = merged_record

            updated += 1

            changed = True



        if changed:

            self._save_translation_records(records)



        return {

            "added": added,

            "updated": updated,

            "skipped": skipped,

            "conflicts": conflicts,

            "total_remote": len([item for item in (remote_records or []) if isinstance(item, dict)]),

        }



    def _build_local_translation_from_remote(self, remote_record: dict[str, Any], local_id: int, existing_record: dict[str, Any] | None = None) -> dict[str, Any]:

        now_iso = datetime.now(timezone.utc).isoformat()

        base = dict(existing_record or {})

        base.update(

            {

                "id": local_id,

                "remote_id": remote_record.get("remote_id") or remote_record.get("id") or base.get("remote_id"),

                "history_key": remote_record.get("history_key") or base.get("history_key"),

                "source_text": remote_record.get("source_text") or "",

                "translated_text": remote_record.get("translated_text") or "",

                "from_lang": remote_record.get("from_lang") or "auto",

                "to_lang": remote_record.get("to_lang") or "zh-CHS",

                "raw_result": remote_record.get("raw_result"),

                "created_at": remote_record.get("created_at") or base.get("created_at") or now_iso,

                "updated_at": remote_record.get("updated_at") or base.get("updated_at") or now_iso,

                "deleted_at": remote_record.get("deleted_at"),

                "version": max(int(remote_record.get("version") or base.get("version") or 1), 1),

                "device_id": remote_record.get("device_id") or base.get("device_id") or get_or_create_local_device_id(self.data_store),

                "sync_state": "synced",

                "last_synced_at": remote_record.get("last_synced_at") or remote_record.get("updated_at") or now_iso,

            }

        )

        ensure_translation_sync_metadata(base, self.data_store)

        base["sync_state"] = "synced"

        return base



    def _is_remote_translation_different(self, local_record: dict[str, Any], remote_record: dict[str, Any]) -> bool:

        comparable_fields = (

            "source_text",

            "translated_text",

            "from_lang",

            "to_lang",

            "raw_result",

            "deleted_at",

            "updated_at",

            "version",

        )

        for field_name in comparable_fields:

            if local_record.get(field_name) != remote_record.get(field_name):

                return True

        return False



    def get_translation_history(self) -> list[dict[str, Any]]:

        records = self.list_translation_records()

        normalized = []

        for record in records:

            item = dict(record)

            if "source_text" not in item and "text" in item:

                item["source_text"] = item.pop("text")

            if "translated_text" not in item and "result" in item:

                item["translated_text"] = item.pop("result")

            if "from_lang" not in item and "from" in item:

                item["from_lang"] = item.pop("from")

            if "to_lang" not in item and "to" in item:

                item["to_lang"] = item.pop("to")

            normalized.append(item)

        return normalized





