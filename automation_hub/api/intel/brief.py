# -*- coding: utf-8 -*-
"""Daily brief generation service.

Aggregates RSS items by date, generates source distribution stats,
and creates daily_briefs records for push delivery to clients.
"""

from __future__ import annotations

import json
import logging
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from automation_hub.api.db import get_db_connection
from automation_hub.api.config import settings

logger = logging.getLogger(__name__)

INTEL_WORKSPACE = settings.WORKSPACE_DIR / "intel"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def today_str() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def items_for_date(date_str: str | None = None) -> list[dict]:
    """Get RSS items grouped by date (default: today UTC)."""
    query_date = date_str or today_str()

    with get_db_connection() as conn:
        rows = conn.execute(
            """SELECT r.id, r.title, r.link, r.published_at, r.summary, r.source_id, r.created_at,
                      s.name AS source_name, s.url AS source_url
               FROM rss_items r
               LEFT JOIN rss_sources s ON s.id = r.source_id
               WHERE date(r.published_at) = ? OR date(r.created_at) = ?
               ORDER BY r.published_at DESC""",
            (query_date, query_date),
        ).fetchall()
    return [dict(r) for r in rows]


def source_distribution(items: list[dict]) -> dict[str, int]:
    """Compute source distribution from items list."""
    dist: dict[str, int] = {}
    for item in items:
        src = item.get("source_name") or f"source-{item.get('source_id')}"
        dist[src] = dist.get(src, 0) + 1
    return dist


DATE_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def generate_brief(date_str: str | None = None) -> dict:
    """Generate a daily brief for the given date (default: today).

    The brief is stored in the daily_briefs table and written to
    a markdown file in the intel workspace.
    """
    brief_date = date_str or today_str()
    if date_str and not DATE_PATTERN.match(brief_date):
        raise ValueError(f"Invalid date format: {date_str}. Expected YYYY-MM-DD")
    items = items_for_date(brief_date)

    dist = source_distribution(items)
    total_items = len(items)

    # Build stats
    stats = {
        "total_items": total_items,
        "source_distribution": dist,
        "source_count": len(dist),
        "date": brief_date,
    }

    # Generate markdown content
    md_lines = [
        f"# Intel Daily Brief — {brief_date}",
        "",
        f"**Total items:** {total_items}",
        f"**Sources:** {len(dist)}",
        "",
        "## Source Distribution",
        "",
    ]
    for src, count in sorted(dist.items(), key=lambda x: -x[1]):
        md_lines.append(f"- **{src}**: {count} items")
    md_lines += ["", "## Top Items", ""]

    for i, item in enumerate(items[:20], 1):
        title = item.get("title") or "(no title)"
        source = item.get("source_name") or "unknown"
        link = item.get("link") or ""
        summary = (item.get("summary") or "")[:200]
        md_lines.append(f"### {i}. {title}")
        md_lines.append(f"- **Source:** {source}")
        if link:
            md_lines.append(f"- **Link:** {link}")
        if summary:
            md_lines.append(f"- {summary}")
        md_lines.append("")

    md_content = "\n".join(md_lines)

    # Write markdown to workspace
    intel_dir = Path(INTEL_WORKSPACE) / "reports"
    intel_dir.mkdir(parents=True, exist_ok=True)
    md_path = str(intel_dir / f"brief-{brief_date}.md")
    try:
        Path(md_path).write_text(md_content, encoding="utf-8")
        logger.info("Brief written to %s", md_path)
    except OSError as e:
        logger.error("Failed to write brief to %s: %s", md_path, e)
        md_path = ""

    # Upsert into daily_briefs
    brief_id = str(uuid.uuid4())
    now = now_iso()
    with get_db_connection() as conn:
        existing = conn.execute(
            "SELECT id FROM daily_briefs WHERE brief_date=?", (brief_date,)
        ).fetchone()
        if existing:
            conn.execute(
                """UPDATE daily_briefs
                   SET stats_json=?, md_path=?, source_counts_json=?, created_at=?
                   WHERE brief_date=?""",
                (json.dumps(stats, ensure_ascii=False), md_path,
                 json.dumps(dist, ensure_ascii=False), now, brief_date),
            )
        else:
            conn.execute(
                """INSERT INTO daily_briefs
                   (id, brief_date, stats_json, md_path, source_counts_json, created_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (brief_id, brief_date, json.dumps(stats, ensure_ascii=False),
                 md_path, json.dumps(dist, ensure_ascii=False), now),
            )
        conn.commit()

    return {
        "brief_date": brief_date,
        "total_items": total_items,
        "source_count": len(dist),
        "source_distribution": dist,
        "md_path": md_path,
    }


def get_latest_brief() -> dict | None:
    """Get the most recent daily brief."""
    with get_db_connection() as conn:
        row = conn.execute(
            "SELECT id, brief_date, stats_json, md_path, source_counts_json, created_at "
            "FROM daily_briefs ORDER BY brief_date DESC LIMIT 1"
        ).fetchone()
    if not row:
        return None
    result = dict(row)
    for key in ("stats_json", "source_counts_json"):
        try:
            result[key] = json.loads(result[key])
        except (json.JSONDecodeError, TypeError):
            pass
    return result


def get_intel_summary() -> dict[str, Any]:
    """Get a summary of latest intel data for push delivery to clients."""
    latest_brief = get_latest_brief()
    if not latest_brief:
        return {"status": "no_data"}

    with get_db_connection() as conn:
        recent_count = conn.execute(
            "SELECT COUNT(*) as cnt FROM rss_items WHERE created_at > datetime('now', '-1 day')"
        ).fetchone()["cnt"]

        source_count = conn.execute(
            "SELECT COUNT(*) as cnt FROM rss_sources WHERE is_enabled=1"
        ).fetchone()["cnt"]

    stats = latest_brief.get("stats_json", {})
    if isinstance(stats, str):
        try:
            stats = json.loads(stats)
        except Exception:
            stats = {}

    return {
        "status": "available",
        "latest_brief_date": latest_brief.get("brief_date"),
        "latest_brief_path": latest_brief.get("md_path"),
        "total_items_today": stats.get("total_items", 0) if isinstance(stats, dict) else 0,
        "active_sources": source_count,
        "items_last_24h": recent_count,
    }
