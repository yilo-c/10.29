# -*- coding: utf-8 -*-
"""RSS ingestion pipeline.

Fetches RSS feeds from configured sources, normalizes, deduplicates,
and stores items in the rss_items table.
"""

from __future__ import annotations

import hashlib
import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse, urlencode, parse_qs

from automation_hub.api.db import get_db_connection

logger = logging.getLogger(__name__)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normalize_link(link: str) -> str:
    """Normalize a URL by removing tracking parameters."""
    parsed = urlparse(link)
    # Strip common tracking params
    track_params = {"utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content", "ref", "source"}
    query = parse_qs(parsed.query, keep_blank_values=True)
    cleaned = {k: v for k, v in query.items() if k not in track_params}
    if cleaned:
        new_query = urlencode(cleaned, doseq=True)
        return parsed._replace(query=new_query).geturl()
    return parsed._replace(query="").geturl()


def _make_canonical_id(item: dict) -> str:
    """Generate a canonical ID for deduplication."""
    link = _normalize_link(item.get("link", ""))
    title = (item.get("title") or "").strip()
    published = (item.get("published_at") or "").strip()
    raw = link or f"{title}|{published}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def list_enabled_sources() -> list[dict]:
    """List all enabled RSS sources."""
    with get_db_connection() as conn:
        rows = conn.execute(
            "SELECT id, url, name, tags FROM rss_sources WHERE is_enabled=1"
        ).fetchall()
    return [dict(r) for r in rows]


def fetch_feed(url: str, timeout: int = 15) -> list[dict]:
    """Fetch a single RSS/Atom feed and return items.

    Uses feedparser if available, otherwise falls back to a simple
    XML extraction.
    """
    try:
        import feedparser
        import socket
        old_timeout = socket.getdefaulttimeout()
        socket.setdefaulttimeout(timeout)
        try:
            feed = feedparser.parse(url)
        finally:
            socket.setdefaulttimeout(old_timeout)
        items = []
        for entry in feed.entries:
            published = None
            if hasattr(entry, "published_parsed") and entry.published_parsed:
                try:
                    published = datetime(*entry.published_parsed[:6], tzinfo=timezone.utc).isoformat()
                except Exception:
                    published = None
            elif hasattr(entry, "published"):
                published = entry.published

            items.append({
                "title": entry.get("title", ""),
                "link": entry.get("link", ""),
                "published_at": published or now_iso(),
                "summary": entry.get("summary", ""),
                "raw_json": json.dumps(
                    {k: str(v) for k, v in entry.items() if k in ("title", "link", "summary", "published", "author", "tags")},
                    ensure_ascii=False,
                ),
            })
        return items
    except ImportError:
        logger.warning("feedparser not installed, using fallback XML parser")
        return _fetch_feed_fallback(url, timeout)


def _fetch_feed_fallback(url: str, timeout: int = 15) -> list[dict]:
    """Fallback feed fetch using basic XML parsing."""
    import xml.etree.ElementTree as ET
    import urllib.request

    try:
        req = urllib.request.Request(url, headers={"User-Agent": "AutomationHub/0.1"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = resp.read()
    except Exception as e:
        logger.error("Failed to fetch feed %s: %s", url, e)
        return []

    items = []
    try:
        root = ET.fromstring(data)
        # RSS 2.0
        for entry in root.iter("item"):
            ns = {"dc": "http://purl.org/dc/elements/1.1/"}
            title_el = entry.find("title")
            link_el = entry.find("link")
            desc_el = entry.find("description")
            pub_el = entry.find("pubDate") or entry.find("dc:date", ns)

            title = title_el.text if title_el is not None and title_el.text else ""
            link = link_el.text if link_el is not None and link_el.text else ""
            items.append({
                "title": title,
                "link": link,
                "published_at": pub_el.text if pub_el is not None and pub_el.text else now_iso(),
                "summary": desc_el.text if desc_el is not None and desc_el.text else "",
                "raw_json": json.dumps({"title": title, "link": link} if title or link else {}),
            })
        # Atom
        if not items:
            for entry in root.iter("{http://www.w3.org/2005/Atom}entry"):
                title_el = entry.find("{http://www.w3.org/2005/Atom}title")
                link_el = entry.find("{http://www.w3.org/2005/Atom}link")
                pub_el = entry.find("{http://www.w3.org/2005/Atom}published")
                summary_el = entry.find("{http://www.w3.org/2005/Atom}summary")
                href = link_el.get("href") if link_el is not None else ""

            atitle = title_el.text if title_el is not None and title_el.text else ""
            items.append({
                "title": atitle,
                "link": href,
                "published_at": pub_el.text if pub_el is not None and pub_el.text else now_iso(),
                "summary": summary_el.text if summary_el is not None and summary_el.text else "",
                "raw_json": json.dumps({"title": atitle, "link": href} if atitle or href else {}),
            })
    except ET.ParseError as e:
        logger.error("XML parse error for %s: %s", url, e)
        return []

    return items


def ingest_source(source: dict) -> dict:
    """Fetch a single RSS source, deduplicate, and store items.

    Returns summary stats for this source.
    """
    url = source.get("url", "")
    source_id = source.get("id")
    source_name = source.get("name", url)

    raw_items = fetch_feed(url)
    logger.info("Fetched %d items from %s", len(raw_items), source_name)

    new_count = 0
    error_count = 0
    now = now_iso()

    with get_db_connection() as conn:
        for item in raw_items:
            canonical_id = _make_canonical_id(item)

            # Check if already exists (proper dedup)
            existing = conn.execute(
                "SELECT id FROM rss_items WHERE canonical_id=?", (canonical_id,)
            ).fetchone()
            if existing:
                continue

            new_count += 1
            try:
                conn.execute(
                    """INSERT INTO rss_items
                       (id, canonical_id, source_id, title, link, published_at, summary, raw_json, fetched_at, created_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        str(uuid.uuid4()),
                        canonical_id,
                        source_id,
                        (item.get("title") or "")[:500],
                        (item.get("link") or "")[:2048],
                        item.get("published_at"),
                        (item.get("summary") or "")[:2000],
                        item.get("raw_json", "{}"),
                        now,
                        now,
                    ),
                )
            except Exception as e:
                logger.error("Failed to insert item from %s: %s", source_name, e)
                error_count += 1
        conn.commit()

    return {
        "source_id": source_id,
        "source_name": source_name,
        "fetched": len(raw_items),
        "new": new_count,
        "errors": error_count,
    }


def run_ingest(source_ids: list[int] | None = None) -> dict:
    """Run the full RSS ingestion pipeline for all or specified sources.

    Args:
        source_ids: Optional list of source IDs to ingest. None = all enabled sources.

    Returns:
        Summary dict with per-source results.
    """
    if source_ids:
        with get_db_connection() as conn:
            placeholders = ",".join("?" * len(source_ids))
            rows = conn.execute(
                f"SELECT id, url, name, tags FROM rss_sources WHERE is_enabled=1 AND id IN ({placeholders})",
                source_ids,
            ).fetchall()
        sources = [dict(r) for r in rows]
    else:
        sources = list_enabled_sources()

    if not sources:
        return {"sources_processed": 0, "results": [], "total_new": 0}

    results = []
    total_new = 0
    for source in sources:
        try:
            result = ingest_source(source)
            results.append(result)
            total_new += result["new"]
        except Exception as e:
            logger.exception("Ingest failed for source %s: %s", source.get("name"), e)
            results.append({
                "source_id": source.get("id"),
                "source_name": source.get("name", ""),
                "error": str(e),
            })

    return {
        "sources_processed": len(sources),
        "results": results,
        "total_new": total_new,
    }
