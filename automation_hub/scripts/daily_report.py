"""模块说明：每日报告生成脚本。"""

from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
import sys
import uuid
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from automation_hub.api.config import settings
from api import db
from automation_hub.notifications import NotificationMessage, NotificationService
from automation_hub.utils.info_analysis import (
    AnalysisConfig,
    classify_items,
    classify_industry_track,
    classify_policy_level,
    compute_top_terms,
    detect_risk_signals,
    extract_domain,
    score_employment_items,
    summarize_policy_agencies,
    trend_terms_baseline,
)

DB_PATH = str(settings.DATABASE_PATH)


def fetch_recent_runs(limit: int = 10):
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        cur = conn.execute(
            """SELECT tr.id, t.name as tool_name, tr.status, tr.created_at,
                      tr.started_at, tr.finished_at, tr.exit_code
               FROM tool_runs tr
               LEFT JOIN tools t ON tr.tool_id = t.id
               ORDER BY tr.created_at DESC LIMIT ?""",
            (limit,),
        )
        return [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()


def disk_usage(path: Path = settings.DATA_DIR):
    p = Path(path)
    total, used, free = shutil.disk_usage(str(p))
    return {"path": str(p), "total": total, "used": used, "free": free}


def fetch_rss_stats(brief_date: str, limit: int = 10):
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        source_rows = conn.execute(
            """SELECT s.name, s.url, COUNT(*) as cnt
               FROM rss_items ri
               JOIN rss_sources s ON ri.source_id = s.id
               WHERE date(ri.fetched_at, 'localtime') = ?
               GROUP BY s.id
               ORDER BY cnt DESC""",
            (brief_date,),
        ).fetchall()

        items = conn.execute(
            """SELECT ri.title, ri.link, ri.published_at, s.name as source_name
               FROM rss_items ri
               JOIN rss_sources s ON ri.source_id = s.id
               WHERE date(ri.fetched_at, 'localtime') = ?
               ORDER BY ri.fetched_at DESC
               LIMIT ?""",
            (brief_date, limit),
        ).fetchall()

        sources = [dict(r) for r in source_rows]
        latest = [dict(r) for r in items]
        total = sum(r["cnt"] for r in sources) if sources else 0
        return total, sources, latest
    finally:
        conn.close()


def fetch_rss_texts_for_day(day: str) -> list[str]:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """SELECT ri.title, ri.summary
               FROM rss_items ri
               WHERE date(ri.fetched_at, 'localtime') = ?""",
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


def fetch_rss_items_for_day(day: str, limit: int = 200) -> list[dict]:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """SELECT ri.title, ri.link, ri.summary, ri.published_at, s.name as source_name
               FROM rss_items ri
               JOIN rss_sources s ON ri.source_id = s.id
               WHERE date(ri.fetched_at, 'localtime') = ?
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
               WHERE date(ri.fetched_at, 'localtime') >= ? AND date(ri.fetched_at, 'localtime') <= ?""",
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


def already_notified_today(conn: sqlite3.Connection, today: str) -> bool:
    row = conn.execute(
        """SELECT 1 FROM audit_events
           WHERE event_type = 'notification'
             AND resource_type = 'daily_brief'
             AND resource_id = ?
             AND action = 'send'
             AND status = 'succeeded'
           LIMIT 1""",
        (today,),
    ).fetchone()
    return bool(row)


def fetch_audit_rollups(conn: sqlite3.Connection, today: str) -> dict:
        """聚合当天审计指标（用于 stats_json）"""
        rollup: dict = {"rss_fetch": {}, "notification": {}}

        def _classify_rss_error(err: str) -> str:
            e = (err or "").lower()
            if not e:
                return "unknown"
            if "ssl" in e or "certificate" in e or "cert" in e:
                return "ssl"
            if "timed out" in e or "timeout" in e:
                return "timeout"
            if "name or service not known" in e or "nodename" in e or "getaddrinfo" in e or "dns" in e:
                return "dns"
            if "connection refused" in e or "max retries exceeded" in e or "connection reset" in e:
                return "conn"
            if "503" in e or "502" in e or "504" in e or "500" in e:
                return "http5xx"
            if "404" in e:
                return "http404"
            return "other"

        rss_rows = conn.execute(
            """SELECT status, meta_json, created_at
               FROM audit_events
               WHERE event_type = 'rss.fetch'
                 AND date(created_at) = ?""",
            (today,),
        ).fetchall()

        inserted = 0
        updated = 0
        attempts_total = 0
        elapsed_ms_total = 0
        succeeded = 0
        failed = 0
        sources = set()
        failed_reason_counts: dict[str, int] = {}
        failed_examples: dict[str, list[str]] = {}

        for status, meta_json, _created_at in rss_rows:
            try:
                meta = json.loads(meta_json or "{}")
            except Exception:
                meta = {}

            url = meta.get("url")
            if url:
                sources.add(url)

            if status == "succeeded":
                succeeded += 1
                inserted += int(meta.get("inserted") or 0)
                updated += int(meta.get("updated") or 0)
                # attempts 缺失时按 1 次尝试计，避免平均值异常偏低
                attempts_total += int(meta.get("attempts") or 1)
                elapsed_ms_total += int(meta.get("elapsed_ms") or 0)
            else:
                failed += 1
                reason = _classify_rss_error(str(meta.get("error") or ""))
                failed_reason_counts[reason] = failed_reason_counts.get(reason, 0) + 1
                if url:
                    failed_examples.setdefault(reason, [])
                    if len(failed_examples[reason]) < 3:
                        failed_examples[reason].append(url)

        total_items = inserted + updated
        duplicate_rate = (updated / total_items) if total_items else 0.0
        avg_elapsed_ms = (elapsed_ms_total / succeeded) if succeeded else 0.0
        avg_attempts = (attempts_total / succeeded) if succeeded else 0.0

        rollup["rss_fetch"] = {
            "succeeded": succeeded,
            "failed": failed,
            "sources": len(sources),
            "inserted": inserted,
            "updated": updated,
            "total_items": total_items,
            "duplicate_rate": duplicate_rate,
            "avg_elapsed_ms": avg_elapsed_ms,
            "avg_attempts": avg_attempts,
            "failed_reason_counts": failed_reason_counts,
            "failed_examples": failed_examples,
        }

        notif_rows = conn.execute(
            """SELECT status, meta_json
               FROM audit_events
               WHERE event_type = 'notification'
                 AND resource_type = 'daily_brief'
                 AND resource_id = ?
                 AND action = 'send'
               ORDER BY created_at DESC
               LIMIT 1""",
            (today,),
        ).fetchone()

        if notif_rows:
            n_status, n_meta_json = notif_rows
            try:
                n_meta = json.loads(n_meta_json or "{}")
            except Exception:
                n_meta = {}
            payload = {"status": n_status, **(n_meta if isinstance(n_meta, dict) else {})}
            results = None
            if isinstance(n_meta, dict):
                r = n_meta.get("results")
                if isinstance(r, list):
                    results = r
            payload["summary"] = summarize_notification_results(results)
            rollup["notification"] = payload
        else:
            rollup["notification"] = {"status": "unknown"}

        return rollup


def fmt_bytes(n: int) -> str:
    units = ["B", "KB", "MB", "GB", "TB"]
    x = float(n)
    for u in units:
        if x < 1024 or u == units[-1]:
            return f"{x:.2f}{u}"
        x /= 1024
    return f"{x:.2f}B"


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


def update_daily_brief_audit_notification(
    conn: sqlite3.Connection,
    today: str,
    notification_payload: dict,
) -> None:
    row = conn.execute(
        "SELECT stats_json FROM daily_briefs WHERE brief_date = ?",
        (today,),
    ).fetchone()
    if not row:
        return

    try:
        stats = json.loads(row[0] or "{}")
    except Exception:
        stats = {}

    audit = stats.get("audit")
    if not isinstance(audit, dict):
        audit = {}

    audit["notification"] = notification_payload
    stats["audit"] = audit

    conn.execute(
        "UPDATE daily_briefs SET stats_json = ? WHERE brief_date = ?",
        (json.dumps(stats, ensure_ascii=False), today),
    )


def summarize_notification_results(results: list[dict] | None) -> dict:
    if not results:
        return {
            "channels_total": 0,
            "channels_ok": 0,
            "channels_failed": 0,
            "errors_top": [],
        }

    channels_total = len(results)
    channels_ok = 0
    channels_failed = 0
    errors: dict[str, int] = {}

    for r in results:
        if bool(r.get("ok")):
            channels_ok += 1
        else:
            channels_failed += 1

        err = (r.get("error") or "").strip()
        if err:
            errors[err] = errors.get(err, 0) + 1

    errors_top = [
        {"error": e, "count": c}
        for e, c in sorted(errors.items(), key=lambda kv: (-kv[1], kv[0]))
    ][:5]

    return {
        "channels_total": channels_total,
        "channels_ok": channels_ok,
        "channels_failed": channels_failed,
        "errors_top": errors_top,
    }


def main():
    p = argparse.ArgumentParser(description="Generate a daily Markdown report under data/reports")
    p.add_argument("--out-dir", default=str(settings.DATA_DIR / "reports"), help="Output directory")
    p.add_argument("--limit", type=int, default=10, help="Recent runs to include")
    p.add_argument("--notify", dest="notify", action="store_true", default=True, help="Send notification")
    p.add_argument("--no-notify", dest="notify", action="store_false", help="Disable notification")
    p.add_argument("--quiet", action="store_true", help="Reduce console output")
    p.add_argument("--force-notify", action="store_true", help="Send notification even if already sent today")
    args = p.parse_args()

    settings.ensure_dirs()
    db.init_db()

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    today = datetime.now().strftime("%Y-%m-%d")
    prev_day = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
    baseline_days = 7
    baseline_start = (datetime.now() - timedelta(days=baseline_days)).strftime("%Y-%m-%d")
    report_path = out_dir / f"{today}.md"

    runs = fetch_recent_runs(args.limit)
    du = disk_usage(settings.DATA_DIR)
    rss_total, rss_sources, rss_latest = fetch_rss_stats(today, limit=10)

    cfg = AnalysisConfig()
    today_texts = fetch_rss_texts_for_day(today)
    prev_texts = fetch_rss_texts_for_range(baseline_start, prev_day)
    # 今日总量可能超过 200；为保证“就业/政策”少量条目不被截断，拉满到 500（本项目日常量级可承受）
    today_items_for_risk = fetch_rss_items_for_day(today, limit=500)

    top_terms = compute_top_terms(today_texts, cfg)
    trending = trend_terms_baseline(today_texts, prev_texts, cfg)
    risk = detect_risk_signals(today_items_for_risk, cfg)

    buckets = classify_items(today_items_for_risk)
    employment_scored = score_employment_items(today_items_for_risk, top_n=10, min_score=4)
    policy_levels = classify_policy_level(buckets["policy"])
    industry_tracks = classify_industry_track(today_items_for_risk)
    policy_agencies = summarize_policy_agencies(buckets["policy"], top_n=8)

    domain_counter = {}
    for it in today_items_for_risk:
        d = extract_domain(it.get("link") or "")
        if not d:
            continue
        domain_counter[d] = domain_counter.get(d, 0) + 1
    top_domains = [
        {"domain": d, "count": c}
        for d, c in sorted(domain_counter.items(), key=lambda kv: (-kv[1], kv[0]))[:10]
    ]

    lines = []
    lines.append(f"# Automation Hub 日报 - {today}")
    lines.append("")
    lines.append("## 磁盘使用（/data）")
    lines.append(f"- total: {fmt_bytes(du['total'])}")
    lines.append(f"- used:  {fmt_bytes(du['used'])}")
    lines.append(f"- free:  {fmt_bytes(du['free'])}")
    lines.append("")
    lines.append("## 最近执行记录")
    if not runs:
        lines.append("- 暂无执行记录")
    else:
        lines.append("| time | tool | status | exit |")
        lines.append("|---|---|---|---|")
        for r in runs:
            t = (r.get("created_at") or "")[:19]
            lines.append(f"| {t} | {r.get('tool_name') or r.get('tool_id') or ''} | {r['status']} | {r.get('exit_code','')} |")

    lines.append("")
    lines.append("## 资讯日报")
    lines.append(f"- 今日条目: {rss_total}")

    if rss_sources:
        lines.append("")
        lines.append("### 来源分布")
        lines.append("| source | count | url |")
        lines.append("|---|---|---|")
        for s in rss_sources:
            lines.append(f"| {s.get('name','')} | {s.get('cnt',0)} | {s.get('url','')} |")

    if rss_latest:
        lines.append("")
        lines.append("### 最新条目")
        for i, it in enumerate(rss_latest, 1):
            title = it.get("title", "")
            link = it.get("link", "")
            source = it.get("source_name", "")
            published = (it.get("published_at") or "")[:19]
            lines.append(f"{i}. {title} ({source})")
            if published:
                lines.append(f"   - published: {published}")
            if link:
                lines.append(f"   - link: {link}")

    lines.append("")
    lines.append("## 信息分析")
    if top_terms:
        lines.append(
            f"- 热点词(Top{cfg.top_terms}): "
            + ", ".join([f"{x['term']}({x['count']})" for x in top_terms[:10]])
        )
    else:
        lines.append(f"- 热点词(Top{cfg.top_terms}): (暂无)")

    if trending:
        lines.append(
            f"- 趋势词(Top{cfg.trend_terms}): "
            + ", ".join([f"{x['term']}(+{x['delta']})" for x in trending])
        )
    else:
        lines.append(f"- 趋势词(Top{cfg.trend_terms}): (暂无)")

    lines.append(f"- 风险信号: flagged={risk.get('flagged_count', 0)}")
    if risk.get("top_cves"):
        lines.append("- Top CVE: " + ", ".join([f"{x['cve']}({x['count']})" for x in risk['top_cves'][:5]]))

    if top_domains:
        lines.append("- 域名分布(Top10): " + ", ".join([f"{x['domain']}({x['count']})" for x in top_domains]))

    lines.append("")
    lines.append("## 政策/行业信息归纳")
    lines.append(f"- 就业类: {len(buckets['employment'])} 条")
    if employment_scored:
        lines.append("- 就业重点(Top5, 相关度打分):")
        for it in employment_scored[:5]:
            score = int(it.get("score") or 0)
            hits = it.get("hits") or []
            actions = it.get("actions") or []
            deadline = it.get("deadline")
            hint = []
            if hits:
                hint.append("命中:" + "/".join(hits[:4]))
            if actions:
                hint.append("动作:" + "/".join(actions[:2]))
            if deadline:
                hint.append("截止:" + str(deadline))
            extra = f"（{'; '.join(hint)}）" if hint else ""
            lines.append(f"  - [{score}] {it.get('title','')}{extra}")
    else:
        for it in buckets["employment"][:5]:
            lines.append(f"  - {it.get('title','')}")
    lines.append(f"- 政策类: {len(buckets['policy'])} 条")
    for it in buckets["policy"][:5]:
        lines.append(f"  - {it.get('title','')}")
    lines.append(
        "- 政策级别归一: "
        + ", ".join(
            [
                f"国家({len(policy_levels['national'])})",
                f"部委({len(policy_levels['ministry'])})",
                f"地方({len(policy_levels['local'])})",
                f"行业协会({len(policy_levels['association'])})",
                f"其他({len(policy_levels['other'])})",
            ]
        )
    )
    if policy_agencies:
        lines.append(
            "- 政策机构Top: "
            + ", ".join([f"{x['agency']}({x['count']})" for x in policy_agencies])
        )
    lines.append(f"- 行业类: {len(buckets['industry'])} 条")
    for it in buckets["industry"][:5]:
        lines.append(f"  - {it.get('title','')}")
    lines.append(
        "- 行业赛道归类: "
        + ", ".join(
            [
                f"云({len(industry_tracks['cloud'])})",
                f"AI({len(industry_tracks['ai'])})",
                f"安全({len(industry_tracks['security'])})",
                f"能源({len(industry_tracks['energy'])})",
                f"制造({len(industry_tracks['manufacturing'])})",
                f"金融({len(industry_tracks['finance'])})",
                f"电信({len(industry_tracks['telecom'])})",
                f"医疗({len(industry_tracks['healthcare'])})",
                f"教育({len(industry_tracks['education'])})",
                f"政务({len(industry_tracks['gov'])})",
                f"其他({len(industry_tracks['other'])})",
            ]
        )
    )
    lines.append(f"- 风险类: {len(buckets['risk'])} 条")
    for it in buckets["risk"][:5]:
        lines.append(f"  - {it.get('title','')}")
    lines.append(f"- 运维类: {len(buckets['ops'])} 条")
    for it in buckets["ops"][:5]:
        lines.append(f"  - {it.get('title','')}")

    report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    if not args.quiet:
        print(f"OK: wrote {report_path}")

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        audit_rollups = fetch_audit_rollups(conn, today)
    finally:
        conn.close()

    if not args.notify:
        audit_rollups["notification"] = {
            "status": "disabled",
            "summary": summarize_notification_results([]),
        }

    stats = {
        "date": today,
        "disk": du,
        "tool_runs": {"recent": len(runs)},
        "rss": {
            "items_today": rss_total,
            "sources": rss_sources,
        },
        "analysis": {
            "top_terms": top_terms,
            "trending_terms": trending,
            "risk": risk,
            "top_domains": top_domains,
            "prev_day": prev_day,
            "baseline_days": baseline_days,
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
                "employment": employment_scored or [{"title": it.get("title"), "link": it.get("link"), "source": it.get("source_name")} for it in buckets["employment"][:10]],
                "policy": [{"title": it.get("title"), "link": it.get("link"), "source": it.get("source_name")} for it in buckets["policy"][:10]],
                "industry": [{"title": it.get("title"), "link": it.get("link"), "source": it.get("source_name")} for it in buckets["industry"][:10]],
                "risk": [{"title": it.get("title"), "link": it.get("link"), "source": it.get("source_name")} for it in buckets["risk"][:10]],
                "ops": [{"title": it.get("title"), "link": it.get("link"), "source": it.get("source_name")} for it in buckets["ops"][:10]],
                "general": [{"title": it.get("title"), "link": it.get("link"), "source": it.get("source_name")} for it in buckets["general"][:10]],
            },
        },
        "audit": audit_rollups,
    }
    source_counts_json = json.dumps(rss_sources, ensure_ascii=False)

    conn = sqlite3.connect(DB_PATH)
    with conn:
        conn.execute(
            """INSERT INTO daily_briefs(brief_date, stats_json, md_path, created_at, source_counts_json)
               VALUES(?,?,?,?,?)
               ON CONFLICT(brief_date) DO UPDATE SET
                   stats_json=excluded.stats_json,
                   md_path=excluded.md_path,
                   created_at=excluded.created_at,
                   source_counts_json=excluded.source_counts_json
            """,
            (
                today,
                json.dumps(stats, ensure_ascii=False),
                str(report_path),
                datetime.now().isoformat(),
                source_counts_json,
            ),
        )
        log_audit_event(
            conn,
            event_type="daily_brief",
            resource_type="daily_brief",
            resource_id=today,
            action="generate",
            status="succeeded",
            message=f"daily brief generated: {report_path.name}",
            meta={
                "report_path": str(report_path),
                "rss_total": rss_total,
                "sources": rss_sources,
            },
        )
        if not args.notify:
            log_audit_event(
                conn,
                event_type="notification",
                resource_type="daily_brief",
                resource_id=today,
                action="send",
                status="disabled",
                message="notification disabled (--no-notify)",
                meta={"reason": "--no-notify"},
            )
            update_daily_brief_audit_notification(
                conn,
                today,
                {
                    "status": "disabled",
                    "summary": summarize_notification_results([]),
                },
            )
    conn.close()

    if args.notify:
        conn = sqlite3.connect(DB_PATH)
        try:
            if (not args.force_notify) and already_notified_today(conn, today):
                with conn:
                    log_audit_event(
                        conn,
                        event_type="notification",
                        resource_type="daily_brief",
                        resource_id=today,
                        action="send",
                        status="skipped",
                        message="skip notification: already sent today",
                        meta={},
                    )
                    update_daily_brief_audit_notification(
                        conn,
                        today,
                        {
                            "status": "skipped",
                            "summary": summarize_notification_results([]),
                        },
                    )
                if not args.quiet:
                    print("notify_skipped: already sent today (use --force-notify to override)")
                return
        finally:
            conn.close()

        service = NotificationService()

        try:
            rel_report_path = report_path.relative_to(settings.DATA_DIR)
            report_hint = f"data/{rel_report_path.as_posix()}"
        except Exception:
            report_hint = str(report_path)

        summary_lines = [
            f"日报已生成: {report_hint}",
            f"今日条目: {rss_total}",
        ]
        for s in rss_sources[:5]:
            summary_lines.append(f"- {s.get('name','')}: {s.get('cnt',0)}")

        if rss_latest:
            summary_lines.append("")
            summary_lines.append("最新条目:")
            for it in rss_latest[:5]:
                title = (it.get("title") or "").strip()
                link = (it.get("link") or "").strip()
                source = (it.get("source_name") or "").strip()
                if link:
                    summary_lines.append(f"- {title} ({source})\n  {link}")
                else:
                    summary_lines.append(f"- {title} ({source})")

        message = NotificationMessage(
            title=f"Automation Hub 日报 {today}",
            content="\n".join(summary_lines),
            level="info",
            metadata={"report_path": report_hint},
        )
        results = service.send(message)
        if not args.quiet:
            print(f"notify_results: {results}")

        conn = sqlite3.connect(DB_PATH)
        with conn:
            status = "succeeded" if all(r.get("ok") for r in results) else "failed"
            summary = summarize_notification_results(results)
            log_audit_event(
                conn,
                event_type="notification",
                resource_type="daily_brief",
                resource_id=today,
                action="send",
                status=status,
                message="daily brief notification",
                meta={"results": results},
            )
            update_daily_brief_audit_notification(
                conn,
                today,
                {
                    "status": status,
                    "summary": summary,
                    "results": results,
                },
            )
        conn.close()


if __name__ == "__main__":
    main()
