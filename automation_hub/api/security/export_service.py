# -*- coding: utf-8 -*-
"""Data export and delete service (Layer 3).

Provides endpoints for users to fully export or delete their data,
giving them complete control over their information.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any
from pathlib import Path

from automation_hub.api.config import settings
from automation_hub.api.db import get_db_connection
from automation_hub.api.audit.service import log_event
from automation_hub.api.security.data_classification import (
    classify_resource,
    DataLevel,
    check_resource_export,
)

logger = logging.getLogger(__name__)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _export_user_data(user_id: str) -> dict[str, Any]:
    """Export all user data as a structured dict.

    Returns data from all personal resource types owned by the user.
    """
    export: dict[str, Any] = {
        "exported_at": now_iso(),
        "user_id": user_id,
        "data": {},
    }

    with get_db_connection() as conn:
        # Personal notes
        try:
            rows = conn.execute(
                "SELECT * FROM personal_notes WHERE user_id=? ORDER BY updated_at DESC",
                (user_id,),
            ).fetchall()
            export["data"]["notes"] = [dict(r) for r in rows]
        except Exception as exc:
            logger.warning("Failed to export notes: %s", exc)
            export["data"]["notes"] = []

        # Personal tasks
        try:
            rows = conn.execute(
                "SELECT * FROM personal_tasks WHERE user_id=? ORDER BY updated_at DESC",
                (user_id,),
            ).fetchall()
            export["data"]["tasks"] = [dict(r) for r in rows]
        except Exception as exc:
            logger.warning("Failed to export tasks: %s", exc)
            export["data"]["tasks"] = []

        # Personal translations
        try:
            rows = conn.execute(
                "SELECT * FROM personal_translations WHERE user_id=? ORDER BY created_at DESC",
                (user_id,),
            ).fetchall()
            export["data"]["translations"] = [dict(r) for r in rows]
        except Exception as exc:
            logger.warning("Failed to export translations: %s", exc)
            export["data"]["translations"] = []

        # Personal vocabulary
        try:
            rows = conn.execute(
                "SELECT * FROM personal_words WHERE user_id=? ORDER BY created_at DESC",
                (user_id,),
            ).fetchall()
            export["data"]["vocabulary"] = [dict(r) for r in rows]
        except Exception as exc:
            logger.warning("Failed to export vocabulary: %s", exc)
            export["data"]["vocabulary"] = []

        # Personal news items
        try:
            rows = conn.execute(
                "SELECT * FROM personal_news WHERE user_id=? ORDER BY created_at DESC",
                (user_id,),
            ).fetchall()
            export["data"]["news_items"] = [dict(r) for r in rows]
        except Exception as exc:
            logger.warning("Failed to export news items: %s", exc)
            export["data"]["news_items"] = []

    return export


def export_user_data(user_id: str, format: str = "json") -> dict[str, Any]:
    """Export all user data with audit logging.

    Args:
        user_id: The user to export data for.
        format: Export format (only "json" supported currently).

    Returns:
        Dict with export_id, exported data, and metadata.
    """
    export_id = str(uuid.uuid4())
    data = _export_user_data(user_id)

    # Save export to disk for download
    export_dir = Path(settings.DATA_DIR) / "exports"
    export_dir.mkdir(parents=True, exist_ok=True)
    export_path = export_dir / f"{export_id}.json"
    export_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    resource_counts = {k: len(v) for k, v in data["data"].items()}

    log_event(
        event_type="data.exported",
        action="export",
        status="success",
        actor_user_id=user_id,
        resource_type="user_data",
        resource_id=export_id,
        message="user data exported",
        meta={"format": format, "resource_counts": resource_counts},
    )

    return {
        "export_id": export_id,
        "exported_at": data["exported_at"],
        "resource_counts": resource_counts,
        "download_path": f"/data/exports/{export_id}.json",
        "data": data,
    }


def delete_user_data(user_id: str, resource_types: list[str] | None = None) -> dict[str, Any]:
    """Delete user data for specified resource types (or all if None).

    Args:
        user_id: The user whose data to delete.
        resource_types: List of resource types to delete, or None for all.

    Returns:
        Dict with deletion summary.
    """
    deleted_counts: dict[str, int] = {}
    tables = {
        "notes": "personal_notes",
        "tasks": "personal_tasks",
        "translations": "personal_translations",
        "vocabulary": "personal_words",
        "news": "personal_news",
    }

    with get_db_connection() as conn:
        for resource_type, table in tables.items():
            if resource_types is not None and resource_type not in resource_types:
                continue
            try:
                cursor = conn.execute(f"DELETE FROM {table} WHERE user_id=?", (user_id,))
                deleted_counts[resource_type] = cursor.rowcount
            except Exception as exc:
                logger.warning("Failed to delete %s: %s", table, exc)
                deleted_counts[resource_type] = 0
        conn.commit()

    log_event(
        event_type="data.deleted",
        action="delete",
        status="success",
        actor_user_id=user_id,
        resource_type="user_data",
        message="user data deleted",
        meta={"resource_types": resource_types, "deleted_counts": deleted_counts},
    )

    return {
        "deleted": True,
        "resource_types": resource_types or list(tables.keys()),
        "deleted_counts": deleted_counts,
    }
