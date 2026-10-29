"""Assistant tag service — manages supertag structured data binding for memory entries.

Provides:
- Structured data storage/retrieval per memory entry
- Field validation against supertag schemas
- Schema migration for existing structured data
- Integration with existing flat tag system
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any

from memory.entry_schema import (
    SupertagSchemaRegistry,
    validate_supertag_data,
)

logger = logging.getLogger("siyi.assistant.tag_service")

STRUCTURED_DATA_KEY = "supertag_structured_data"


class AssistantTagService:
    """Service for managing supertag structured data on memory entries.

    Structured data is stored separately from tags themselves, keyed by
    entry_id. Each entry can have multiple supertags with structured data:
        {entry_id: {supertag_type: {field_name: value, ...}, ...}}

    The existing tag system (flat string tags) remains untouched for
    backward compatibility.
    """

    def __init__(self, data_store):
        self._data_store = data_store
        self._schema_registry = SupertagSchemaRegistry(data_store=data_store)

    # ── Properties ──────────────────────────────────────────────────────

    @property
    def schema_registry(self) -> SupertagSchemaRegistry:
        return self._schema_registry

    # ── Structured data CRUD ────────────────────────────────────────────

    def get_structured_data(self, entry_id: str) -> dict[str, Any]:
        """Get all supertag structured data for a memory entry.

        Returns:
            {supertag_type: {field_name: value, ...}, ...}
        """
        store = self._load_store()
        return dict(store.get(entry_id) or {})

    def set_structured_data(
        self,
        entry_id: str,
        supertag_type: str,
        field_data: dict[str, Any],
    ) -> dict[str, Any]:
        """Set structured field data for a supertag on a memory entry.

        Validates field values against the supertag schema before saving.

        Args:
            entry_id: Memory entry ID.
            supertag_type: Supertag type (e.g. '@任务').
            field_data: {field_name: value, ...}

        Returns:
            {"success": bool, "errors": list, "entry_id": str}
        """
        # Validate schema exists
        schema = self._schema_registry.get_schema(supertag_type)
        if schema is None:
            return {
                "success": False,
                "errors": [{"field": "", "message": f"未知超级标签类型: {supertag_type}"}],
                "entry_id": entry_id,
            }

        # Validate field values
        errors = validate_supertag_data(schema, field_data)
        if errors:
            return {"success": False, "errors": errors, "entry_id": entry_id}

        # Clean: only keep known fields
        known_names = {f["name"] for f in schema.get("fields", [])}
        clean_data = {k: v for k, v in field_data.items() if k in known_names}

        # Save
        store = self._load_store()
        if entry_id not in store:
            store[entry_id] = {}
        store[entry_id][supertag_type] = clean_data
        self._save_store(store)

        logger.debug(
            "Structured data set for entry %s supertag %s: %s",
            entry_id, supertag_type, clean_data,
        )
        return {"success": True, "errors": [], "entry_id": entry_id}

    def update_structured_data(
        self,
        entry_id: str,
        supertag_type: str,
        field_data: dict[str, Any],
    ) -> dict[str, Any]:
        """Merge field updates into existing structured data.

        Only overwrites the fields provided; leaves other fields intact.
        Validates new values before merging.

        Args:
            entry_id: Memory entry ID.
            supertag_type: Supertag type (e.g. '@任务').
            field_data: {field_name: value, ...} partial update.

        Returns:
            {"success": bool, "errors": list}
        """
        schema = self._schema_registry.get_schema(supertag_type)
        if schema is None:
            return {
                "success": False,
                "errors": [{"field": "", "message": f"未知超级标签类型: {supertag_type}"}],
            }

        errors = validate_supertag_data(schema, field_data)
        if errors:
            return {"success": False, "errors": errors}

        store = self._load_store()
        if entry_id not in store:
            store[entry_id] = {}
        if supertag_type not in store[entry_id]:
            store[entry_id][supertag_type] = {}

        known_names = {f["name"] for f in schema.get("fields", [])}
        clean_data = {k: v for k, v in field_data.items() if k in known_names}
        store[entry_id][supertag_type].update(clean_data)
        self._save_store(store)

        return {"success": True, "errors": []}

    def remove_structured_data(
        self,
        entry_id: str,
        supertag_type: str | None = None,
    ) -> bool:
        """Remove structured data for an entry.

        Args:
            entry_id: Memory entry ID.
            supertag_type: If provided, only remove data for this type.
                          If None, remove all structured data for entry.

        Returns:
            True if anything was removed.
        """
        store = self._load_store()
        if entry_id not in store:
            return False

        if supertag_type is not None:
            removed = store[entry_id].pop(supertag_type, None)
            if not store[entry_id]:
                del store[entry_id]
        else:
            removed = store.pop(entry_id, None)

        if removed is not None:
            self._save_store(store)
            return True
        return False

    # ── Tag integration ─────────────────────────────────────────────────

    def enrich_entry_with_supertag_data(
        self,
        entry: dict[str, Any],
    ) -> dict[str, Any]:
        """Add structured data to a memory entry dict if it has supertags.

        Args:
            entry: Memory entry dict (with 'entry_id' and/or 'tags').

        Returns:
            Entry dict with extra 'supertag_data' field if applicable.
        """
        entry_id = entry.get("entry_id") or entry.get("id")
        if not entry_id:
            return entry

        structured = self.get_structured_data(entry_id)
        if structured:
            entry = dict(entry)
            entry["supertag_data"] = structured

        # Also mark which tags are supertags
        tags = entry.get("tags") or entry.get("tags_text") or ""
        if isinstance(tags, str):
            tag_list = [t.strip() for t in tags.split() if t.strip()]
        elif isinstance(tags, list):
            tag_list = [str(t).strip() for t in tags if str(t).strip()]
        else:
            tag_list = []

        supertag_flags = {}
        for tag in tag_list:
            st_type = self._schema_registry.get_supertag_type_from_tag(tag)
            if st_type:
                supertag_flags[tag] = st_type

        if supertag_flags:
            entry = dict(entry) if entry is not entry else entry
            entry["supertag_flags"] = supertag_flags

        return entry

    # ── Schema migration ────────────────────────────────────────────────

    def migrate_structured_data(
        self,
        entry_id: str,
        supertag_type: str,
        old_schema_version: str | None = None,
    ) -> dict[str, Any]:
        """Validate and fix structured data against current schema.

        Args:
            entry_id: Memory entry ID.
            supertag_type: Supertag type.
            old_schema_version: Not used currently, for future versioning.

        Returns:
            {"success": bool, "migrated": bool, "errors": list}
        """
        schema = self._schema_registry.get_schema(supertag_type)
        if schema is None:
            return {"success": False, "migrated": False,
                    "errors": [f"Unknown supertag: {supertag_type}"]}

        store = self._load_store()
        entry_data = store.get(entry_id, {})
        current = entry_data.get(supertag_type, {})

        if not current:
            return {"success": True, "migrated": False, "errors": []}

        # Validate
        errors = validate_supertag_data(schema, current)
        if not errors:
            return {"success": True, "migrated": False, "errors": []}

        # Auto-fix: remove invalid fields, keep valid ones
        known_names = {f["name"] for f in schema.get("fields", [])}
        fixed = {k: v for k, v in current.items()
                 if k in known_names and not any(
                     e.get("field") == k for e in errors
                     if "必填" not in e.get("message", "")
                 )}

        store[entry_id][supertag_type] = fixed
        self._save_store(store)
        logger.info("Migrated structured data for %s / %s", entry_id, supertag_type)
        return {"success": True, "migrated": True, "errors": errors}

    # ── Internal persistence ────────────────────────────────────────────

    def _load_store(self) -> dict[str, Any]:
        """Load the structured data store from disk."""
        try:
            return dict(self._data_store.load_json(STRUCTURED_DATA_KEY, {}))
        except Exception as exc:
            logger.warning("Failed to load supertag structured data: %s", exc, exc_info=True)
            return {}

    def _save_store(self, store: dict[str, Any]) -> None:
        """Save the structured data store to disk."""
        try:
            self._data_store.save_json(STRUCTURED_DATA_KEY, store)
        except Exception as exc:
            logger.error("Failed to save supertag structured data: %s", exc)
