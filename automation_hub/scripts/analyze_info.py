from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import uuid
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from automation_hub.api.config import settings
from api import db
from automation_hub.utils.info_analysis import (
    AnalysisConfig,
    classify_items,
    classify_industry_track,
    classify_policy_level,
    compute_top_terms,
    detect_risk_signals,
    extract_domain,
    summarize_policy_agencies,
    trend_terms_baseline,
)


DB_PATH = str(settings.DATABASE_PATH)


def fetch_rss_texts_for_day(day: str) -> list[str]:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """SELECT ri.title, ri.summary
               FROM rss_items ri
               WHERE date(ri.fetched_at) = ?""",
            (day,),
        ).fetchall()
        texts: list[str] = []
        for r in rows:
            title = (r["title"] or "").strip()
            summary = (r["summary"] or "").strip()
            texts.append(f"{title}\n{summary}")
        return texts
    finally:
        conn.close()


def fetch_rss_items_for_day(day: str, limit: int = 500) -> list[dict]:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """SELECT ri.title, ri.link, ri.summary, ri.published_at, s.name as source_name
               FROM rss_items ri
               JOIN rss_sources s ON ri.source_id = s.id
               WHERE date(ri.fetched_at) = ?
               ORDER BY ri.fetched_at DESC
               LIMIT ?""",
            (day, limit),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def fetch_rss_texts_for_range(start_day: str, end_day: str) -> list[str]:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """SELECT ri.title, ri.summary
               FROM rss_items ri
               WHERE date(ri.fetched_at) >= ? AND date(ri.fetched_at) <= ?""",
            (start_day, end_day),
        ).fetchall()
        texts: list[str] = []
        for r in rows:
            title = (r["title"] or "").strip()
            summary = (r["summary"] or "").strip()
            texts.append(f"{title}\n{summary}")
        return texts
    finally:
        conn.close()


def log_audit_event(conn: sqlite3.Connection, meta: dict) -> None:
    conn.execute(
        """INSERT INTO audit_events(
               id, actor_user_id, actor_device_id, event_type, resource_type,
               resource_id, action, status, message, meta_json, created_at
           ) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
        (
            str(uuid.uuid4()),
            None,
            None,
            "info.analysis",
            "day",
            meta.get("date") or "",
            "analyze",
            "succeeded",
            "info analysis generated",
            json.dumps(meta, ensure_ascii=False),
            datetime.now().isoformat(),
        ),
    )


def main() -> None:
    p = argparse.ArgumentParser(description="Analyze RSS info and output JSON summary")
    p.add_argument("--date", default=datetime.now().strftime("%Y-%m-%d"), help="YYYY-MM-DD")
    p.add_argument("--prev-days", type=int, default=7, help="Baseline window (days)")
    p.add_argument("--limit", type=int, default=500, help="Max RSS items to include")
    p.add_argument("--out", default="", help="Output path (.json). If empty, print to stdout")
    args = p.parse_args()

    settings.ensure_dirs()
    db.init_db()

    today = args.date
    prev_day = (datetime.strptime(today, "%Y-%m-%d") - timedelta(days=1)).strftime("%Y-%m-%d")
    baseline_start = (datetime.strptime(today, "%Y-%m-%d") - timedelta(days=args.prev_days)).strftime("%Y-%m-%d")

    cfg = AnalysisConfig()
    today_texts = fetch_rss_texts_for_day(today)
    prev_texts = fetch_rss_texts_for_range(baseline_start, prev_day)
    items = fetch_rss_items_for_day(today, limit=args.limit)

    top_terms = compute_top_terms(today_texts, cfg)
    trending = trend_terms_baseline(today_texts, prev_texts, cfg)
    risk = detect_risk_signals(items, cfg)
    buckets = classify_items(items)
    policy_levels = classify_policy_level(buckets["policy"])
    industry_tracks = classify_industry_track(items)
    policy_agencies = summarize_policy_agencies(buckets["policy"], top_n=8)

    domain_counter = {}
    for it in items:
        d = extract_domain(it.get("link") or "")
        if not d:
            continue
        domain_counter[d] = domain_counter.get(d, 0) + 1
    top_domains = [
        {"domain": d, "count": c}
        for d, c in sorted(domain_counter.items(), key=lambda kv: (-kv[1], kv[0]))[:10]
    ]

    payload = {
        "date": today,
        "prev_day": prev_day,
        "baseline_days": args.prev_days,
        "items_count": len(items),
        "top_terms": top_terms,
        "trending_terms": trending,
        "risk": risk,
        "top_domains": top_domains,
        "policy_levels": {
            "national": [{"title": it.get("title"), "link": it.get("link"), "source": it.get("source_name")} for it in policy_levels["national"][:10]],
            "ministry": [{"title": it.get("title"), "link": it.get("link"), "source": it.get("source_name")} for it in policy_levels["ministry"][:10]],
            "local": [{"title": it.get("title"), "link": it.get("link"), "source": it.get("source_name")} for it in policy_levels["local"][:10]],
            "association": [{"title": it.get("title"), "link": it.get("link"), "source": it.get("source_name")} for it in policy_levels["association"][:10]],
            "other": [{"title": it.get("title"), "link": it.get("link"), "source": it.get("source_name")} for it in policy_levels["other"][:10]],
        },
        "policy_agencies": policy_agencies,
        "industry_tracks": {
            "cloud": [{"title": it.get("title"), "link": it.get("link"), "source": it.get("source_name")} for it in industry_tracks["cloud"][:10]],
            "ai": [{"title": it.get("title"), "link": it.get("link"), "source": it.get("source_name")} for it in industry_tracks["ai"][:10]],
            "security": [{"title": it.get("title"), "link": it.get("link"), "source": it.get("source_name")} for it in industry_tracks["security"][:10]],
            "energy": [{"title": it.get("title"), "link": it.get("link"), "source": it.get("source_name")} for it in industry_tracks["energy"][:10]],
            "manufacturing": [{"title": it.get("title"), "link": it.get("link"), "source": it.get("source_name")} for it in industry_tracks["manufacturing"][:10]],
            "finance": [{"title": it.get("title"), "link": it.get("link"), "source": it.get("source_name")} for it in industry_tracks["finance"][:10]],
            "telecom": [{"title": it.get("title"), "link": it.get("link"), "source": it.get("source_name")} for it in industry_tracks["telecom"][:10]],
            "healthcare": [{"title": it.get("title"), "link": it.get("link"), "source": it.get("source_name")} for it in industry_tracks["healthcare"][:10]],
            "education": [{"title": it.get("title"), "link": it.get("link"), "source": it.get("source_name")} for it in industry_tracks["education"][:10]],
            "gov": [{"title": it.get("title"), "link": it.get("link"), "source": it.get("source_name")} for it in industry_tracks["gov"][:10]],
            "other": [{"title": it.get("title"), "link": it.get("link"), "source": it.get("source_name")} for it in industry_tracks["other"][:10]],
        },
        "summary": {
            "policy": [{"title": it.get("title"), "link": it.get("link"), "source": it.get("source_name")} for it in buckets["policy"][:10]],
            "industry": [{"title": it.get("title"), "link": it.get("link"), "source": it.get("source_name")} for it in buckets["industry"][:10]],
            "risk": [{"title": it.get("title"), "link": it.get("link"), "source": it.get("source_name")} for it in buckets["risk"][:10]],
            "ops": [{"title": it.get("title"), "link": it.get("link"), "source": it.get("source_name")} for it in buckets["ops"][:10]],
            "general": [{"title": it.get("title"), "link": it.get("link"), "source": it.get("source_name")} for it in buckets["general"][:10]],
        },
    }

    conn = sqlite3.connect(DB_PATH)
    with conn:
        log_audit_event(conn, payload)
    conn.close()

    out = args.out.strip()
    if out:
        Path(out).write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    else:
        print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
