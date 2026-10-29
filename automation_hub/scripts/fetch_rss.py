"""模块说明：RSS 抓取脚本。"""

from __future__ import annotations

import argparse
import json
import hashlib
import sqlite3
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse, urlunparse

import feedparser
import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from automation_hub.api.config import settings
from api import db


def normalize_link(link: str) -> str:
    if not link:
        return ""
    try:
        parsed = urlparse(link.strip())
        return urlunparse(
            (parsed.scheme.lower(), parsed.netloc.lower(), parsed.path, "", "", "")
        )
    except Exception:
        return link.strip()


def canonical_id_for(title: str, link: str, published: str) -> str:
    base = normalize_link(link) or f"{title.strip()}|{published.strip()}"
    return hashlib.sha256(base.encode("utf-8")).hexdigest()


def parse_published(entry) -> str:
    try:
        if getattr(entry, "published_parsed", None):
            dt = datetime(*entry.published_parsed[:6], tzinfo=timezone.utc)
            return dt.isoformat()
    except Exception:
        pass
    return getattr(entry, "published", "") or ""


def log_audit_event(
    conn: sqlite3.Connection,
    event_type: str,
    resource_type: str,
    resource_id: str,
    action: str,
    status: str,
    message: str,
    meta: dict,
):
    conn.execute(
        """INSERT INTO audit_events(
               id, actor_user_id, actor_device_id, event_type, resource_type,
               resource_id, action, status, message, meta_json, created_at
           ) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
        (
            str(uuid.uuid4()),
            None,
            None,
            event_type,
            resource_type,
            resource_id,
            action,
            status,
            message,
            json.dumps(meta, ensure_ascii=False),
            datetime.now().isoformat(),
        ),
    )


def fetch_with_retries(url: str, timeout: int = 15, retries: int = 3) -> tuple[bytes, int, int]:
    """返回：(content, attempts, elapsed_ms)"""
    started = time.time()
    last_err: Exception | None = None
    for attempt in range(retries + 1):
        try:
            resp = requests.get(url, timeout=timeout, headers={"User-Agent": "automation-hub/0.1"})
            resp.raise_for_status()
            elapsed_ms = int((time.time() - started) * 1000)
            return resp.content, (attempt + 1), elapsed_ms
        except Exception as e:
            last_err = e
            if attempt >= retries:
                break
            # 指数退避：0.5s, 1s, 2s...
            time.sleep(0.5 * (2**attempt))
    assert last_err is not None
    raise last_err


def fetch_one(url: str, limit: int, out_dir: Path, db_path: str, quiet: bool = False) -> dict:
    settings.ensure_dirs()
    db.init_db()

    out_dir.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys = ON;")

    request_id = str(uuid.uuid4())
    source_id = None
    inserted = 0
    updated = 0
    attempts = 0
    elapsed_ms = 0

    try:
        try:
            content, attempts, elapsed_ms = fetch_with_retries(url, timeout=15, retries=3)
            feed = feedparser.parse(content)
        except Exception as e:
            with conn:
                log_audit_event(
                    conn,
                    event_type="rss.fetch",
                    resource_type="rss_source",
                    resource_id=url,
                    action="fetch",
                    status="failed",
                    message="rss fetch failed",
                    meta={
                        "url": url,
                        "error": str(e),
                        "request_id": request_id,
                    },
                )
            if not quiet:
                print(f"[rss] FAILED {url}: {e}")
            return {"ok": False, "url": url, "error": str(e)}

        ts = datetime.now().strftime("%Y%m%d-%H%M%S")
        json_path = out_dir / f"rss-{ts}.json"
        md_path = out_dir / f"rss-{ts}.md"

        now_iso = datetime.now(timezone.utc).isoformat()

        with conn:
            conn.execute(
                """INSERT INTO rss_sources(url, name, tags, is_enabled, created_at, updated_at)
                   VALUES(?,?,?,?,?,?)
                   ON CONFLICT(url) DO UPDATE SET
                     name=excluded.name,
                     updated_at=excluded.updated_at
                """,
                (
                    url,
                    getattr(feed.feed, "title", ""),
                    "",
                    1,
                    now_iso,
                    now_iso,
                ),
            )
            row = conn.execute("SELECT id FROM rss_sources WHERE url = ?", (url,)).fetchone()
            source_id = row[0] if row else None

        if source_id is None:
            raise RuntimeError("failed to resolve rss_sources.id")

        entries = feed.entries[:limit]
        payloads = []
        canonical_ids = []

        for e in entries:
            published = parse_published(e)
            title = getattr(e, "title", "")
            link = getattr(e, "link", "")
            canonical_id = canonical_id_for(title, link, published)
            entry_dict = {k: e.get(k) for k in e.keys()}
            raw_json = json.dumps(entry_dict, ensure_ascii=False, default=str)
            summary = getattr(e, "summary", "")[:300]

            canonical_ids.append(canonical_id)
            payloads.append(
                (
                    canonical_id,
                    source_id,
                    title,
                    link,
                    published,
                    summary,
                    raw_json,
                    now_iso,
                    now_iso,
                )
            )

        existing = set()
        if canonical_ids:
            placeholders = ",".join(["?"] * len(canonical_ids))
            rows = conn.execute(
                f"SELECT canonical_id FROM rss_items WHERE canonical_id IN ({placeholders})",
                canonical_ids,
            ).fetchall()
            existing = {r[0] for r in rows}

        inserted = len([cid for cid in canonical_ids if cid not in existing])
        updated = len(canonical_ids) - inserted

        with conn:
            conn.executemany(
                """INSERT INTO rss_items(
                       canonical_id, source_id, title, link, published_at,
                       summary, raw_json, fetched_at, created_at
                   ) VALUES(?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(canonical_id) DO UPDATE SET
                       title=excluded.title,
                       link=excluded.link,
                       published_at=excluded.published_at,
                       summary=excluded.summary,
                       raw_json=excluded.raw_json,
                       fetched_at=excluded.fetched_at
                """,
                payloads,
            )

        json_path.write_text(
            json.dumps(
                {
                    "feed_title": getattr(feed.feed, "title", ""),
                    "feed_link": getattr(feed.feed, "link", ""),
                    "url": url,
                    "items": [
                        {
                            "title": getattr(e, "title", ""),
                            "link": getattr(e, "link", ""),
                            "published": parse_published(e),
                            "summary": (getattr(e, "summary", "") or "")[:300],
                        }
                        for e in entries
                    ],
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        md_path.write_text(
            "\n".join(
                [
                    f"# RSS 抓取 {datetime.now().isoformat()}\n",
                    f"- URL: {url}",
                    f"- Title: {getattr(feed.feed, 'title', '')}\n",
                ]
                + [
                    f"- {getattr(e, 'title', '')}\n  - {getattr(e, 'link', '')}" for e in entries
                ]
            ),
            encoding="utf-8",
        )

        with conn:
            log_audit_event(
                conn,
                event_type="rss.fetch",
                resource_type="rss_source",
                resource_id=url,
                action="fetch",
                status="succeeded",
                message="rss fetch succeeded",
                meta={
                    "url": url,
                    "source_id": source_id,
                    "limit": limit,
                    "inserted": inserted,
                    "updated": updated,
                    "attempts": attempts,
                    "elapsed_ms": elapsed_ms,
                    "json_path": str(json_path),
                    "md_path": str(md_path),
                    "request_id": request_id,
                },
            )

        if not quiet:
            print(f"[rss] OK {url} inserted={inserted} updated={updated} attempts={attempts} {elapsed_ms}ms")
        return {
            "ok": True,
            "url": url,
            "source_id": source_id,
            "inserted": inserted,
            "updated": updated,
            "attempts": attempts,
            "elapsed_ms": elapsed_ms,
        }
    finally:
        conn.close()


def main():
    p = argparse.ArgumentParser(description="Fetch an RSS feed and save JSON + Markdown summary")
    p.add_argument("--url", default="https://www.people.com.cn/rss/politics.xml", help="RSS URL")
    p.add_argument("--all", action="store_true", help="Fetch all enabled rss_sources from DB")
    p.add_argument("--limit", type=int, default=10, help="Max items")
    p.add_argument("--out-dir", default=str(settings.DATA_DIR / "rss"), help="Output dir")
    p.add_argument("--db", default="", help="SQLite DB path (override)")
    p.add_argument("--quiet", action="store_true", help="Reduce console output")
    args = p.parse_args()

    # 注意：本项目的 db.init_db() 固定使用 settings.DATABASE_PATH。
    # 当前默认流程不依赖 --db 覆盖；如需覆盖 DB，请确保 schema 已在目标 DB 中初始化。
    db_path = args.db or str(settings.DATABASE_PATH)
    out_dir = Path(args.out_dir)

    # 确保默认 DB/schema 存在（便于读取 rss_sources）
    settings.ensure_dirs()
    db.init_db()

    if args.all:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute(
                "SELECT url FROM rss_sources WHERE is_enabled = 1 ORDER BY id"
            ).fetchall()
            urls = [r["url"] for r in rows if r and r["url"]]
        finally:
            conn.close()

        if not urls:
            raise SystemExit("No enabled rss_sources found. Run scripts/seed_rss_sources.py first.")

        ok = 0
        failed = 0
        inserted_sum = 0
        updated_sum = 0
        for u in urls:
            res = fetch_one(u, limit=args.limit, out_dir=out_dir, db_path=db_path, quiet=args.quiet)
            if res.get("ok"):
                ok += 1
                inserted_sum += int(res.get("inserted") or 0)
                updated_sum += int(res.get("updated") or 0)
            else:
                failed += 1

        if not args.quiet:
            print(
                f"[rss] ALL DONE sources={len(urls)} ok={ok} failed={failed} inserted={inserted_sum} updated={updated_sum} db={db_path}"
            )
        return

    fetch_one(args.url, limit=args.limit, out_dir=out_dir, db_path=db_path, quiet=args.quiet)


if __name__ == "__main__":
    main()