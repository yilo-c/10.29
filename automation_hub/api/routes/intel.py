# -*- coding: utf-8 -*-
"""Intel API router - RSS news aggregation, daily briefs, and delivery.

Provides endpoints for:
- RSS source management (CRUD)
- RSS items querying
- Daily brief generation and retrieval
- Intel result delivery for desktop/Android clients
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from automation_hub.api.auth.deps import require_scope, get_current_token
from automation_hub.api.audit.service import log_event
from automation_hub.api.db import get_db_connection
from automation_hub.api.config import settings
from automation_hub.api.intel.ingest import run_ingest
from automation_hub.api.intel.brief import generate_brief, get_intel_summary, get_latest_brief
from automation_hub.notifications import get_notification_service, NotificationMessage

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/intel", tags=["intel"])


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ==================== RSS Source Management ====================


class RssSourceCreate(BaseModel):
    url: str = Field(description="RSS feed URL", min_length=1, max_length=2048)
    name: str = Field(description="Display name", min_length=1, max_length=200)
    tags: list[str] = Field(default_factory=list, description="Tags for categorization")


class RssSourceUpdate(BaseModel):
    name: str | None = None
    tags: list[str] | None = None
    is_enabled: bool | None = None


class RssSourceResponse(BaseModel):
    id: int
    url: str
    name: str | None
    tags: list[str]
    is_enabled: bool
    created_at: str
    updated_at: str


@router.get("/rss/sources", response_model=list[RssSourceResponse])
def list_rss_sources(
    enabled_only: bool = Query(default=False, description="Only return enabled sources"),
    token=Depends(require_scope("news:read")),
):
    """List all RSS sources."""
    with get_db_connection() as conn:
        if enabled_only:
            rows = conn.execute(
                "SELECT id, url, name, tags, is_enabled, created_at, updated_at FROM rss_sources WHERE is_enabled=1 ORDER BY name ASC"
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT id, url, name, tags, is_enabled, created_at, updated_at FROM rss_sources ORDER BY name ASC"
            ).fetchall()
    result = []
    for r in rows:
        item = dict(r)
        try:
            item["tags"] = json.loads(item.get("tags") or "[]")
        except Exception:
            item["tags"] = []
        item["is_enabled"] = bool(item.get("is_enabled", 0))
        result.append(item)
    return result


@router.post("/rss/sources", status_code=201)
def create_rss_source(
    payload: RssSourceCreate,
    token=Depends(require_scope("news:write")),
):
    """Create a new RSS source."""
    now = now_iso()
    tags_json = json.dumps(payload.tags or [], ensure_ascii=False)

    with get_db_connection() as conn:
        # Check for duplicate URL
        existing = conn.execute("SELECT id FROM rss_sources WHERE url=?", (payload.url,)).fetchone()
        if existing:
            raise HTTPException(status_code=409, detail="RSS source with this URL already exists")

        conn.execute(
            "INSERT INTO rss_sources (url, name, tags, is_enabled, created_at, updated_at) VALUES (?, ?, ?, 1, ?, ?)",
            (payload.url, payload.name, tags_json, now, now),
        )
        conn.commit()
        source_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

    log_event(
        event_type="intel.rss_source_created",
        action="create",
        status="success",
        actor_user_id=token.get("user_id"),
        resource_type="rss_source",
        resource_id=str(source_id),
        message=f"RSS source created: {payload.name}",
        meta={"url": payload.url, "name": payload.name, "tags": payload.tags},
    )

    return {"id": source_id, "url": payload.url, "name": payload.name}


@router.put("/rss/sources/{source_id}")
def update_rss_source(
    source_id: int,
    payload: RssSourceUpdate,
    token=Depends(require_scope("news:write")),
):
    """Update an RSS source."""
    now = now_iso()
    updates: list[str] = []
    params: list[Any] = []

    if payload.name is not None:
        updates.append("name=?")
        params.append(payload.name)
    if payload.tags is not None:
        updates.append("tags=?")
        params.append(json.dumps(payload.tags, ensure_ascii=False))
    if payload.is_enabled is not None:
        updates.append("is_enabled=?")
        params.append(1 if payload.is_enabled else 0)

    if not updates:
        raise HTTPException(status_code=400, detail="no fields to update")

    updates.append("updated_at=?")
    params.append(now)
    params.append(source_id)

    with get_db_connection() as conn:
        cur = conn.execute(
            f"UPDATE rss_sources SET {', '.join(updates)} WHERE id=?",
            params,
        )
        conn.commit()
        if cur.rowcount == 0:
            raise HTTPException(status_code=404, detail="RSS source not found")

    return {"updated": True}


@router.delete("/rss/sources/{source_id}")
def delete_rss_source(
    source_id: int,
    token=Depends(require_scope("news:write")),
):
    """Delete an RSS source."""
    with get_db_connection() as conn:
        cur = conn.execute("DELETE FROM rss_sources WHERE id=?", (source_id,))
        conn.commit()
        if cur.rowcount == 0:
            raise HTTPException(status_code=404, detail="RSS source not found")

    return {"deleted": True}


# ==================== RSS Items ====================


@router.get("/rss/items")
def list_rss_items(
    date: str | None = Query(default=None, description="Filter by date (YYYY-MM-DD)"),
    source_id: int | None = Query(default=None, description="Filter by source ID"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    token=Depends(require_scope("news:read")),
):
    """List RSS items with optional date/source filters."""
    conditions: list[str] = []
    params: list[Any] = []

    if date:
        conditions.append("(date(r.published_at) = ? OR date(r.created_at) = ?)")
        params.extend([date, date])
    if source_id is not None:
        conditions.append("r.source_id = ?")
        params.append(source_id)

    where_clause = " AND ".join(conditions) if conditions else "1=1"

    with get_db_connection() as conn:
        count_row = conn.execute(
            f"SELECT COUNT(*) as cnt FROM rss_items r WHERE {where_clause}", params
        ).fetchone()

        rows = conn.execute(
            f"""SELECT r.id, r.title, r.link, r.published_at, r.summary, r.source_id,
                       r.fetched_at, s.name AS source_name
                FROM rss_items r
                LEFT JOIN rss_sources s ON s.id = r.source_id
                WHERE {where_clause}
                ORDER BY r.published_at DESC
                LIMIT ? OFFSET ?""",
            params + [limit, offset],
        ).fetchall()

    items = []
    for r in rows:
        item = dict(r)
        items.append(item)

    return {
        "items": items,
        "total": count_row["cnt"],
        "limit": limit,
        "offset": offset,
    }


# ==================== Daily Briefs ====================


@router.get("/briefs")
def list_briefs(
    limit: int = Query(default=10, ge=1, le=100),
    token=Depends(require_scope("news:read")),
):
    """List daily briefs."""
    with get_db_connection() as conn:
        rows = conn.execute(
            "SELECT id, brief_date, stats_json, md_path, created_at FROM daily_briefs ORDER BY brief_date DESC LIMIT ?",
            (limit,),
        ).fetchall()

    result = []
    for r in rows:
        item = dict(r)
        try:
            item["stats_json"] = json.loads(item["stats_json"])
        except Exception:
            pass
        result.append(item)
    return {"briefs": result, "count": len(result)}


@router.get("/briefs/{brief_date}")
def get_brief(
    brief_date: str,
    token=Depends(require_scope("news:read")),
):
    """Get a specific daily brief by date (YYYY-MM-DD)."""
    with get_db_connection() as conn:
        row = conn.execute(
            "SELECT id, brief_date, stats_json, md_path, source_counts_json, created_at FROM daily_briefs WHERE brief_date=?",
            (brief_date,),
        ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="brief not found")

    result = dict(row)
    for key in ("stats_json", "source_counts_json"):
        try:
            result[key] = json.loads(result[key])
        except Exception:
            pass
    return result


# ==================== Intel Actions ====================


class IngestResponse(BaseModel):
    sources_processed: int
    total_new: int
    results: list[dict]


@router.post("/ingest", response_model=IngestResponse)
def trigger_ingest(
    token=Depends(require_scope("news:write")),
):
    """Trigger RSS ingestion pipeline."""
    result = run_ingest()

    log_event(
        event_type="intel.ingest",
        action="ingest",
        status="success",
        actor_user_id=token.get("user_id"),
        resource_type="intel",
        message=f"Ingested {result['total_new']} new items from {result['sources_processed']} sources",
        meta={"sources_processed": result["sources_processed"], "total_new": result["total_new"]},
    )

    return IngestResponse(
        sources_processed=result["sources_processed"],
        total_new=result["total_new"],
        results=result["results"],
    )


class BriefGenerateResponse(BaseModel):
    brief_date: str
    total_items: int
    source_count: int
    source_distribution: dict[str, int]
    md_path: str


@router.post("/briefs/generate", response_model=BriefGenerateResponse)
def trigger_brief_generation(
    date: str | None = Query(default=None, description="Date (YYYY-MM-DD), defaults to today"),
    notify: bool = Query(default=False, description="Send notification after generation"),
    token=Depends(require_scope("news:write")),
):
    """Generate a daily brief."""
    result = generate_brief(date)

    log_event(
        event_type="intel.brief_generated",
        action="generate_brief",
        status="success",
        actor_user_id=token.get("user_id"),
        resource_type="intel",
        message=f"Brief generated for {result['brief_date']}",
        meta=result,
    )

    # Optionally send notification
    if notify:
        try:
            notification_service = get_notification_service()
            message = NotificationMessage(
                title=f"Intel Daily Brief — {result['brief_date']}",
                content=(
                    f"Total items: {result['total_items']}\n"
                    f"Sources: {result['source_count']}\n"
                    f"Report: {result['md_path']}"
                ),
                level="info",
                metadata={"brief_date": result["brief_date"], "total_items": result["total_items"]},
            )
            notification_service.send(message)
        except Exception as e:
            logger.warning("Failed to send brief notification: %s", e)

    return BriefGenerateResponse(**result)


# ==================== Intel Delivery ====================


@router.get("/delivery")
def get_intel_delivery(
    token=Depends(require_scope("news:read")),
):
    """Get latest intel results for push delivery to desktop/Android clients.

    Returns the latest brief summary and recent items for client-side display.
    """
    summary = get_intel_summary()

    if summary.get("status") == "no_data":
        return {"status": "no_data", "message": "No intel data available yet"}

    # Get recent items for delivery
    with get_db_connection() as conn:
        recent_items = conn.execute(
            """SELECT r.id, r.title, r.link, r.published_at, r.summary, r.source_id,
                      s.name AS source_name
               FROM rss_items r
               LEFT JOIN rss_sources s ON s.id = r.source_id
               ORDER BY r.published_at DESC
               LIMIT 20""",
        ).fetchall()

    items = []
    for r in recent_items:
        items.append(dict(r))

    return {
        "status": "available",
        "summary": summary,
        "items": items,
    }
