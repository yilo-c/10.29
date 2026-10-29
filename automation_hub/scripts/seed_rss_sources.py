"""批量注册 RSS 来源（不抓取内容）。"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from automation_hub.api.config import settings
from api import db


def log_audit_event(conn: sqlite3.Connection, status: str, message: str, meta: dict) -> None:
    conn.execute(
        """INSERT INTO audit_events(
               id, actor_user_id, actor_device_id, event_type, resource_type,
               resource_id, action, status, message, meta_json, created_at
           ) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
        (
            str(uuid.uuid4()),
            None,
            None,
            "rss.seed",
            "rss_sources",
            "rss_seed_list",
            "upsert",
            status,
            message,
            json.dumps(meta, ensure_ascii=False),
            datetime.now().isoformat(),
        ),
    )


def seed_sources(include_tier2: bool, force_enable: bool) -> list[dict]:
    tier1 = [
        # 中文
        {
            "url": "https://36kr.com/feed",
            "name": "36氪",
            "tags": "lang=zh,topic=industry,topic=employment,topic=policy",
            "is_enabled": 1,
        },
        {
            "url": "https://www.infoq.cn/feed",
            "name": "InfoQ 中文",
            "tags": "lang=zh,topic=tech,topic=devops,topic=ai",
            "is_enabled": 1,
        },
        {
            "url": "https://www.oschina.net/news/rss",
            "name": "开源中国 News",
            "tags": "lang=zh,topic=oss,topic=cloud,topic=devops",
            "is_enabled": 1,
        },
        {
            "url": "https://www.cnblogs.com/rss",
            "name": "博客园",
            "tags": "lang=zh,topic=tech,topic=devops",
            "is_enabled": 1,
        },
        {
            "url": "http://www.ruanyifeng.com/blog/atom.xml",
            "name": "阮一峰的网络日志",
            "tags": "lang=zh,topic=tech,topic=trend",
            "is_enabled": 1,
        },
        {
            "url": "https://www.people.com.cn/rss/politics.xml",
            "name": "人民网-时政",
            "tags": "lang=zh,topic=policy,topic=livelihood,weight=low",
            "is_enabled": 1,
        },
        # 英文
        {
            "url": "https://blog.cloudflare.com/rss/",
            "name": "Cloudflare Blog",
            "tags": "lang=en,topic=security,topic=network,topic=cloud",
            "is_enabled": 1,
        },
        {
            "url": "https://kubernetes.io/feed.xml",
            "name": "Kubernetes Blog",
            "tags": "lang=en,topic=cloud,topic=devops,topic=cncf",
            "is_enabled": 1,
        },
        {
            "url": "https://www.cncf.io/feed/",
            "name": "CNCF",
            "tags": "lang=en,topic=cloud,topic=ecosystem,topic=cncf",
            "is_enabled": 1,
        },
        {
            "url": "https://www.ietf.org/blog/feed/",
            "name": "IETF Blog",
            "tags": "lang=en,topic=standard,topic=protocol",
            "is_enabled": 1,
        },
        {
            "url": "https://export.arxiv.org/rss/cs.CR",
            "name": "arXiv cs.CR",
            "tags": "lang=en,topic=security,topic=research,topic=report",
            "is_enabled": 1,
        },
        {
            "url": "https://export.arxiv.org/rss/cs.AI",
            "name": "arXiv cs.AI",
            "tags": "lang=en,topic=ai,topic=research,topic=report",
            "is_enabled": 1,
        },
    ]

    tier2 = [
        {
            "url": "https://www.cisa.gov/uscert/ncas/alerts.xml",
            "name": "CISA Alerts",
            "tags": "lang=en,topic=security,topic=alert,tier=2",
            "is_enabled": 0,
        },
        {
            "url": "https://www.cisa.gov/uscert/ncas/bulletins.xml",
            "name": "CISA Bulletins",
            "tags": "lang=en,topic=security,topic=bulletin,tier=2",
            "is_enabled": 0,
        },
        {
            "url": "https://msrc.microsoft.com/blog/feed",
            "name": "MSRC Blog",
            "tags": "lang=en,topic=security,topic=vendor,tier=2",
            "is_enabled": 0,
        },
        {
            "url": "https://security.googleblog.com/feeds/posts/default?alt=rss",
            "name": "Google Security Blog",
            "tags": "lang=en,topic=security,topic=vendor,tier=2",
            "is_enabled": 0,
        },
        {
            "url": "https://aws.amazon.com/security/security-bulletins/rss/",
            "name": "AWS Security Bulletins",
            "tags": "lang=en,topic=security,topic=cloud,tier=2",
            "is_enabled": 0,
        },
    ]

    items = list(tier1)
    if include_tier2:
        items.extend(tier2)

    if force_enable:
        for item in items:
            item["is_enabled"] = 1

    return items


def main() -> None:
    p = argparse.ArgumentParser(description="Seed RSS sources into database")
    p.add_argument("--db", default="", help="SQLite DB path (override)")
    p.add_argument("--include-tier2", action="store_true", help="Include tier2 sources (default disabled)")
    p.add_argument("--enable-all", action="store_true", help="Force enable all seeded sources")
    args = p.parse_args()

    settings.ensure_dirs()
    db.init_db()

    db_path = args.db or str(settings.DATABASE_PATH)
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys = ON;")

    items = seed_sources(args.include_tier2, args.enable_all)
    now_iso = datetime.now(timezone.utc).isoformat()

    try:
        with conn:
            for item in items:
                conn.execute(
                    """INSERT INTO rss_sources(url, name, tags, is_enabled, created_at, updated_at)
                       VALUES(?,?,?,?,?,?)
                       ON CONFLICT(url) DO UPDATE SET
                         name=excluded.name,
                         tags=excluded.tags,
                         is_enabled=excluded.is_enabled,
                         updated_at=excluded.updated_at
                    """,
                    (
                        item["url"],
                        item["name"],
                        item.get("tags", ""),
                        int(item.get("is_enabled", 1)),
                        now_iso,
                        now_iso,
                    ),
                )

        log_audit_event(
            conn,
            status="success",
            message="rss seed list upserted",
            meta={
                "count": len(items),
                "include_tier2": bool(args.include_tier2),
                "enable_all": bool(args.enable_all),
            },
        )
    finally:
        conn.close()


if __name__ == "__main__":
    main()