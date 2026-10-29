from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import uuid
from datetime import datetime
from pathlib import Path
import re
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from automation_hub.api.config import settings
from api import db
from automation_hub.notifications import NotificationMessage, NotificationService

DB_PATH = str(settings.DATABASE_PATH)


def fetch_rss_health() -> dict:
    """补充 RSS 健康信息：启用源数、最近抓取时间、最近抓取状态。

    用于当日 RSS 为空时，避免“0/0/0”但看不出原因。
    """
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            "SELECT COUNT(*) AS c FROM rss_sources WHERE is_enabled = 1"
        ).fetchone()
        enabled_sources = int(row["c"] if row else 0)

        latest_item = conn.execute("SELECT MAX(fetched_at) AS latest FROM rss_items").fetchone()
        latest_item_fetched_at = latest_item["latest"] if latest_item else None

        latest_fetch_row = conn.execute(
            """SELECT status, created_at, resource_id, meta_json
               FROM audit_events
               WHERE event_type = 'rss.fetch'
               ORDER BY created_at DESC
               LIMIT 1"""
        ).fetchone()
        latest_fetch = dict(latest_fetch_row) if latest_fetch_row else {}

        # 解析 meta_json 仅用于补充 error/inserted 等
        meta = {}
        try:
            if latest_fetch.get("meta_json"):
                meta = json.loads(latest_fetch.get("meta_json") or "{}")
        except Exception:
            meta = {}

        return {
            "enabled_sources": enabled_sources,
            "latest_item_fetched_at": latest_item_fetched_at,
            "latest_fetch": {
                "status": latest_fetch.get("status"),
                "created_at": latest_fetch.get("created_at"),
                "resource_id": latest_fetch.get("resource_id"),
                "meta": meta,
            },
        }
    except Exception:
        return {}
    finally:
        conn.close()


def _pct(num: float) -> str:
    return f"{num * 100:.1f}%"


def _safe_div(a: float, b: float) -> float:
    return (a / b) if b else 0.0


def fetch_daily_brief_stats(brief_date: str | None) -> tuple[str, dict] | None:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        if brief_date:
            row = conn.execute(
                "SELECT brief_date, stats_json, md_path FROM daily_briefs WHERE brief_date = ?",
                (brief_date,),
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT brief_date, stats_json, md_path FROM daily_briefs ORDER BY brief_date DESC LIMIT 1"
            ).fetchone()

        if not row:
            return None

        stats_json = row["stats_json"]
        try:
            stats = json.loads(stats_json or "{}")
        except Exception:
            stats = {}

        # 补充日报路径，便于在摘要中做“详见日报”指引
        try:
            stats["_daily_report"] = {"md_path": row["md_path"]}
        except Exception:
            pass

        return row["brief_date"], stats
    finally:
        conn.close()


def build_executive_summary(brief_date: str, stats: dict) -> tuple[str, str]:
    disk = stats.get("disk") or {}
    rss = stats.get("rss") or {}
    analysis = stats.get("analysis") or {}
    audit = stats.get("audit") or {}

    rss_total = int(rss.get("items_today") or 0)
    sources = rss.get("sources") or []
    sources_cnt = len(sources) if isinstance(sources, list) else 0

    rss_fetch = (audit.get("rss_fetch") or {}) if isinstance(audit, dict) else {}
    notif = (audit.get("notification") or {}) if isinstance(audit, dict) else {}

    risk = analysis.get("risk") or {}
    risk_flagged = int(risk.get("flagged_count") or 0)
    risk_items = risk.get("items") or []

    baseline_days = int(analysis.get("baseline_days") or 0)
    trending = analysis.get("trending_terms") or []
    top_terms = analysis.get("top_terms") or []
    top_domains = analysis.get("top_domains") or []
    summary = analysis.get("summary") or {}

    def _is_meaningful_term(term: str) -> bool:
        t = (term or "").strip()
        if not t:
            return False
        # 中文优先
        if any("\u4e00" <= ch <= "\u9fff" for ch in t):
            return True
        # 安全标识保留
        up = t.upper()
        if up.startswith("CVE-") or up.startswith("CNVD-") or up.startswith("CNNVD-") or up.startswith("CWE-"):
            return True
        return False

    def _filter_terms(items: list[dict], max_n: int = 6) -> list[dict]:
        out = []
        for x in items or []:
            term = x.get("term") or ""
            if _is_meaningful_term(term):
                out.append(x)
            if len(out) >= max_n:
                break
        return out

    rss_health = fetch_rss_health()

    report = stats.get("_daily_report") or {}
    report_md_path = report.get("md_path") if isinstance(report, dict) else None

    def _short_path(p: str | None) -> str | None:
        if not p:
            return None
        try:
            pp = Path(str(p)).resolve()
            try:
                rel = pp.relative_to(ROOT)
                return str(rel)
            except Exception:
                return pp.name
        except Exception:
            return str(p)

    def _domain(url: str | None) -> str | None:
        u = (url or "").strip()
        if not u:
            return None
        try:
            return (urlparse(u).netloc or "").lower() or None
        except Exception:
            return None

    def _truncate(s: str, max_len: int = 60) -> str:
        t = (s or "").strip()
        if len(t) <= max_len:
            return t
        return t[: max_len - 1] + "…"

    def _fetch_item_blob_by_link(link: str) -> str:
        u = (link or "").strip()
        if not u:
            return ""
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        try:
            row = conn.execute(
                """SELECT i.title, i.summary
                   FROM rss_items i
                   WHERE i.link = ?
                   ORDER BY i.fetched_at DESC
                   LIMIT 1""",
                (u,),
            ).fetchone()
            if not row:
                return ""
            return f"{(row['title'] or '').strip()}\n{(row['summary'] or '').strip()}"
        finally:
            conn.close()

    def _extract_region(text: str) -> str | None:
        t = (text or "").strip()
        if not t:
            return None
        if "全国" in t:
            return "全国"
        # 省级/直辖市/自治区（轻量）
        regions = [
            "北京",
            "上海",
            "天津",
            "重庆",
            "河北",
            "山西",
            "内蒙古",
            "辽宁",
            "吉林",
            "黑龙江",
            "江苏",
            "浙江",
            "安徽",
            "福建",
            "江西",
            "山东",
            "河南",
            "湖北",
            "湖南",
            "广东",
            "广西",
            "海南",
            "四川",
            "贵州",
            "云南",
            "西藏",
            "陕西",
            "甘肃",
            "青海",
            "宁夏",
            "新疆",
        ]
        for r in regions:
            if r in t:
                return r
        m = re.search(r"([\u4e00-\u9fff]{2,8})(省|市|自治区|州|盟|地区|县|区)", t)
        if m:
            return m.group(0)
        return None

    def _extract_target(text: str) -> str | None:
        t = (text or "").strip()
        if not t:
            return None
        targets = [
            "新就业群体",
            "灵活就业",
            "农民工",
            "高校毕业生",
            "毕业生",
            "参保人员",
            "缴费人员",
            "就业人员",
            "劳动者",
            "用人单位",
            "企业",
            "平台",
            "个体工商户",
            "失业人员",
            "困难人员",
        ]
        for x in targets:
            if x in t:
                return x
        return None

    def _extract_time_or_deadline(text: str) -> str | None:
        t = (text or "")
        if not t:
            return None
        # 优先：显式“截至/截止/申报至”
        m = re.search(r"(截至|截止|申报至|报名至)\s*(\d{4}[\-/年]\d{1,2}[\-/月]\d{1,2}日?)", t)
        if m:
            return f"{m.group(1)}{m.group(2)}"
        # 兜底：抽取首个日期
        m = re.search(r"(\d{4})[\-/年](\d{1,2})[\-/月](\d{1,2})日?", t)
        if m:
            y, mo, d = m.group(1), m.group(2).zfill(2), m.group(3).zfill(2)
            return f"{y}-{mo}-{d}"
        return None

    def _top_scored(items: list[dict], max_items: int = 3) -> list[dict]:
        scored = []
        for it in items or []:
            try:
                s = int(it.get("score") or 0)
            except Exception:
                s = 0
            scored.append((s, it))
        scored.sort(key=lambda x: (-x[0], (x[1].get("title") or "")))
        if any(s > 0 for s, _ in scored):
            return [it for s, it in scored if s > 0][:max_items]
        return []

    employment_items = summary.get("employment") or []
    policy_items = summary.get("policy") or []
    general_items = summary.get("general") or []
    industry_items = summary.get("industry") or []

    def _uniq_by_title_link(items: list[dict], max_items: int | None = None) -> list[dict]:
        out: list[dict] = []
        seen: set[tuple[str, str]] = set()
        for it in items or []:
            key = ((it.get("title") or "").strip(), (it.get("link") or "").strip())
            if key in seen:
                continue
            seen.add(key)
            out.append(it)
            if max_items is not None and len(out) >= max_items:
                break
        return out

    # 政策条目优先使用“policy_levels”（更偏政府/政策语境，噪声更低）
    policy_levels = analysis.get("policy_levels") or {}
    policy_candidates: list[dict] = []
    if isinstance(policy_levels, dict):
        for k in ["national", "ministry", "local", "association", "other"]:
            xs = policy_levels.get(k) or []
            if isinstance(xs, list):
                policy_candidates.extend(xs)
    policy_candidates = _uniq_by_title_link(policy_candidates) or (policy_items if isinstance(policy_items, list) else [])

    # 让“政策/民生要点”更贴近目标：就业/民生优先，弱化“调研/讲话/会见”等活动类时政
    # “民生优先→政策回退”：硬关键词更贴近你关注的社保/工资/灵活就业等
    livelihood_hard_kw = [
        "就业",
        "稳就业",
        "招聘",
        "岗位",
        "求职",
        "失业",
        "裁员",
        "劳动",
        "工资",
        "薪酬",
        "最低工资",
        "社保",
        "医保",
        "养老",
        "工伤",
        "生育",
        "失业保险",
        "公积金",
        "灵活就业",
        "新就业群体",
        "农民工",
    ]
    livelihood_soft_kw = [
        "高校毕业生",
        "毕业生",
        "技能",
        "培训",
        "见习",
        "实习",
        "补贴",
        "救助",
        "低保",
        "住房",
        "租房",
        "购房",
        "医疗",
        "托育",
        "教育",
        "高考",
    ]
    leadership_kw = [
        "调研",
        "强调",
        "会见",
        "指出",
        "主持",
        "讲话",
        "致辞",
        "出席",
        "座谈",
        "视察",
        "慰问",
        "考察",
    ]

    def _policy_livelihood_score(it: dict) -> int:
        title = (it.get("title") or "").strip()
        source = (it.get("source") or "").strip()
        blob = f"{title}\n{source}".lower()

        hard_hit = any(k.lower() in blob for k in livelihood_hard_kw)

        score = 0
        # 民生/就业词命中：硬关键词权重大，软关键词作为补充
        score += 4 * sum(1 for k in livelihood_hard_kw if k.lower() in blob)
        score += 1 * sum(1 for k in livelihood_soft_kw if k.lower() in blob)
        # 典型政策措辞：弱信号
        score += 1 * sum(
            1
            for k in ["通知", "公告", "通告", "意见", "办法", "方案", "印发", "若干措施", "举措", "条例", "规定"]
            if k in title
        )
        # 活动类时政：若没有“硬民生”信号，默认不进入政策/民生要点（仅在最后兜底才可能出现）
        if (not hard_hit) and any(k in title for k in leadership_kw):
            return -5
        return score

    if isinstance(policy_candidates, list) and policy_candidates:
        policy_candidates = sorted(
            policy_candidates,
            key=lambda it: (
                -_policy_livelihood_score(it),
                (it.get("title") or ""),
            ),
        )

    # C 风格：优先输出“就业相关度 Top3”（有分数就按分数；无分数则回退为列表顺序）
    focus_items = _top_scored(employment_items, 3)
    if len(focus_items) < 3:
        focus_items += _top_scored(policy_candidates, 3 - len(focus_items))
    if len(focus_items) < 3:
        focus_items += _top_scored(general_items, 3 - len(focus_items))
    if len(focus_items) < 3:
        focus_items += _top_scored(industry_items, 3 - len(focus_items))

    # 概览（突出民生/就业/政策）
    happened = []
    if focus_items:
        happened.append("- 就业重点：")
        for it in focus_items:
            title = it.get("title") or ""
            source = it.get("source") or ""
            link = it.get("link") or ""
            score = it.get("score")
            actions = it.get("actions") or []
            deadline = it.get("deadline")
            hint = []
            if score is not None:
                try:
                    hint.append(f"相关度{int(score)}")
                except Exception:
                    pass
            if actions:
                hint.append("动作:" + "/".join(actions[:2]))
            if deadline:
                hint.append("截止:" + str(deadline))
            extra = f"（{'；'.join(hint)}）" if hint else ""
            happened.append(f"  - {title}{extra}（{source}）")
            if link:
                happened.append(f"    - {link}")
    else:
        happened.append("- 就业重点：暂无明确命中")

    # C 风格补充：政策/民生要点（宁缺毋滥：仅展示命中民生硬/软关键词的政策条目）
    existing = {(it.get("title") or "", it.get("link") or "") for it in (focus_items or [])}

    def _has_hard_livelihood_hit(it: dict) -> bool:
        title = (it.get("title") or "").strip()
        source = (it.get("source") or "").strip()
        blob = f"{title}\n{source}".lower()
        return any(k.lower() in blob for k in livelihood_hard_kw)

    def _has_soft_livelihood_hit(it: dict) -> bool:
        title = (it.get("title") or "").strip()
        source = (it.get("source") or "").strip()
        blob = f"{title}\n{source}".lower()
        return any(k.lower() in blob for k in livelihood_soft_kw)

    policy_highlights: list[dict] = []
    for it in policy_candidates:
        if len(policy_highlights) >= 3:
            break
        key = (it.get("title") or "", it.get("link") or "")
        if key in existing:
            continue
        # 仅保留命中民生硬/软关键词的条目
        if not (_has_hard_livelihood_hit(it) or _has_soft_livelihood_hit(it)):
            continue
        # 过滤掉活动类/弱相关（保持“宁缺毋滥”）
        if _policy_livelihood_score(it) < 0:
            continue
        policy_highlights.append(it)
    has_livelihood_policy = bool(policy_highlights)
    if policy_highlights:
        happened.append("- 政策/民生要点：")
        for it in policy_highlights[:3]:
            title = it.get("title") or ""
            source = it.get("source") or ""
            link = it.get("link") or ""

            blob = _fetch_item_blob_by_link(link)
            target = _extract_target(blob) or "未识别到对象"
            region = _extract_region(blob) or "未识别到地区"
            timing = _extract_time_or_deadline(blob) or "未识别到时间"
            happened.append(f"  - {title}（对象:{target}；地区:{region}；时间:{timing}）（{source}）")
            if link:
                happened.append(f"    - {link}")
    else:
        happened.append("- 政策/民生要点：今日未发现明确民生政策条目")

    happened.append(f"- RSS 新增条目：{rss_total}（来源 {sources_cnt}）")
    if rss_total == 0 and sources_cnt == 0 and rss_health:
        enabled_sources = int(rss_health.get("enabled_sources") or 0)
        latest_item_fetched_at = rss_health.get("latest_item_fetched_at")
        latest_fetch = rss_health.get("latest_fetch") or {}
        latest_fetch_at = latest_fetch.get("created_at")
        latest_fetch_status = latest_fetch.get("status")
        latest_fetch_url = latest_fetch.get("resource_id")

        happened.append(f"- RSS 启用源：{enabled_sources}")
        if latest_item_fetched_at:
            happened.append(f"- 最近入库时间：{latest_item_fetched_at}")
        if latest_fetch_at or latest_fetch_status:
            extra = f"（{latest_fetch_status}）" if latest_fetch_status else ""
            happened.append(f"- 最近抓取审计：{latest_fetch_at or 'unknown'}{extra}")
        if latest_fetch_url:
            happened.append(f"  - 最近抓取源：{latest_fetch_url}")
    if rss_fetch:
        happened.append(
            "- 抓取情况："
            f"成功 {rss_fetch.get('succeeded', 0)} 次 / 失败 {rss_fetch.get('failed', 0)} 次；"
            f"去重率 {_pct(float(rss_fetch.get('duplicate_rate') or 0.0))}"
        )
        # 失败原因与样例（可操作）
        failed = int(rss_fetch.get("failed", 0) or 0)
        reason_counts = rss_fetch.get("failed_reason_counts") or {}
        examples = rss_fetch.get("failed_examples") or {}
        if failed and isinstance(reason_counts, dict):
            parts = []
            for k, v in sorted(reason_counts.items(), key=lambda x: (-int(x[1] or 0), str(x[0]))):
                try:
                    parts.append(f"{k}:{int(v)}")
                except Exception:
                    continue
            if parts:
                happened.append(f"- 抓取失败分类：{', '.join(parts[:6])}")
            if isinstance(examples, dict) and examples:
                sample_domains = []
                for k, urls in examples.items():
                    if not urls:
                        continue
                    dom = _domain(urls[0])
                    if dom:
                        sample_domains.append(f"{k}:{dom}")
                if sample_domains:
                    happened.append(f"- 失败源样例：{'; '.join(sample_domains[:4])}")
    if notif and isinstance(notif, dict):
        happened.append(f"- 通知状态：{notif.get('status', 'unknown')}")
    happened.append(f"- 风险信号：命中 {risk_flagged} 条")
    # 风险 Top3（降噪展示），其余引导到日报
    if risk_flagged and isinstance(risk_items, list) and risk_items:
        def _risk_score(it: dict) -> int:
            score = 0
            matched = it.get("matched") or []
            if isinstance(matched, list) and any("在野" in str(x) or "0day" in str(x).lower() for x in matched):
                score += 5
            cves = it.get("cves") or []
            cnvds = it.get("cnvds") or []
            cnnvds = it.get("cnnvds") or []
            cwes = it.get("cwes") or []
            if cves:
                score += 4
            if cnvds or cnnvds:
                score += 3
            if cwes:
                score += 1
            if isinstance(matched, list) and any("rce" in str(x).lower() for x in matched):
                score += 2
            return score

        ranked = sorted(risk_items, key=lambda x: (-_risk_score(x), (x.get("title") or "")))
        ranked = [it for it in ranked if _risk_score(it) > 0]
        top = ranked[:3]
        if top:
            happened.append("- 风险 Top：")
            for it in top:
                title = it.get("title") or ""
                cves = it.get("cves") or []
                cnvds = it.get("cnvds") or []
                cnnvds = it.get("cnnvds") or []
                cwes = it.get("cwes") or []
                ids = []
                if isinstance(cves, list):
                    ids += [x for x in cves[:2] if x]
                if isinstance(cnvds, list):
                    ids += [x for x in cnvds[:2] if x]
                if isinstance(cnnvds, list):
                    ids += [x for x in cnnvds[:2] if x]
                if isinstance(cwes, list):
                    ids += [x for x in cwes[:1] if x]
                # 组件/动作（轻量可执行）
                tl = title.lower()
                component = None
                for name in ["react", "nginx", "openssl", "kubernetes", "linux", "windows", "apache", "tomcat", "mysql", "postgres", "redis", "docker", "containerd"]:
                    if name in tl:
                        component = name.upper() if name.isupper() else name.capitalize()
                        break
                if not component:
                    component = "风险项"
                action = "核对版本→升级/打补丁" if ids else "人工复核→评估加固"
                id_txt = ",".join(ids[:2]) if ids else ""
                line = f"{component} {id_txt}：{action}" if id_txt else f"{component}：{action}"
                happened.append(f"  - {_truncate(line, 72)}")

        short_report = _short_path(report_md_path)
        if short_report:
            rest = max(0, risk_flagged - len(top))
            happened.append(f"- 其余风险 {rest} 条：详见日报 {short_report}")

    # 趋势/热点
    meaningful_trending = [x for x in trending if _is_meaningful_term(x.get("term") or "")]
    if meaningful_trending:
        sample = ", ".join([f"{x.get('term')}+{x.get('delta')}" for x in meaningful_trending[:6]])
        happened.append(
            f"- 趋势词（基线{baseline_days}日）：{sample}" if baseline_days else f"- 趋势词：{sample}"
        )
    else:
        filtered_top = _filter_terms(top_terms, 6)
        if filtered_top:
            sample = ", ".join([f"{x.get('term')}({x.get('count')})" for x in filtered_top])
            happened.append(f"- 热点词：{sample}")

    if top_domains:
        sample = ", ".join([f"{x.get('domain')}({x.get('count')})" for x in top_domains[:3]])
        happened.append(f"- 主要域名：{sample}")

    # 建议
    advice = []

    # 就业/政策可执行提示（优先）
    if focus_items:
        advice.append("- 就业/政策提示：建议打开原文，确认适用对象/地区/执行时间；如涉及平台用工/新就业群体，留意权益保障与合规调整")

    # 磁盘
    try:
        total = float(disk.get("total") or 0)
        free = float(disk.get("free") or 0)
        free_ratio = _safe_div(free, total)
        if total and free_ratio < 0.15:
            advice.append(f"- 磁盘空间偏紧（free {_pct(free_ratio)}）：建议清理 data/runs、data/logs、旧 reports，或扩容磁盘")
    except Exception:
        pass

    # RSS 抓取失败（可操作建议）
    failed_n = int(rss_fetch.get("failed") or 0)
    if failed_n > 0:
        reason_counts = rss_fetch.get("failed_reason_counts") or {}
        parts = []
        if isinstance(reason_counts, dict):
            for k, v in sorted(reason_counts.items(), key=lambda x: (-int(x[1] or 0), str(x[0]))):
                try:
                    parts.append(f"{k}:{int(v)}")
                except Exception:
                    continue
        suffix = f"（{', '.join(parts[:6])}）" if parts else ""
        advice.append(f"- 存在 RSS 抓取失败{suffix}：timeout→可适当提高超时/降低 limit；ssl→检查系统时间/证书/代理；http5xx→降频并稍后重试")

    # 当日 RSS 为空但又存在启用源：提示“今天没抓取/没入库”
    if rss_total == 0 and sources_cnt == 0:
        enabled_sources = int((rss_health or {}).get("enabled_sources") or 0)
        if enabled_sources > 0:
            advice.append("- 当日 RSS 为空：看起来今天尚未执行抓取/入库（或抓取全部失败）。建议先运行 RSS 抓取，再生成日报/摘要")

    # 去重率
    try:
        dup = float(rss_fetch.get("duplicate_rate") or 0.0)
        if rss_total >= 20 and dup > 0.9:
            advice.append("- 去重率偏高：建议增加更多 RSS 源，或提升抓取频率/limit，避免每天只有重复内容")
    except Exception:
        pass

    # 风险信号
    if risk_flagged > 0:
        top_cves = risk.get("top_cves") or []
        top_cnvd = risk.get("top_cnvd") or []
        top_cnnvd = risk.get("top_cnnvd") or []
        top_cwe = risk.get("top_cwe") or []
        ids = []
        if top_cves:
            ids.extend([x.get("cve") for x in top_cves[:3] if x.get("cve")])
        if top_cnvd:
            ids.extend([x.get("cnvd") for x in top_cnvd[:3] if x.get("cnvd")])
        if top_cnnvd:
            ids.extend([x.get("cnnvd") for x in top_cnnvd[:3] if x.get("cnnvd")])
        if top_cwe:
            ids.extend([x.get("cwe") for x in top_cwe[:3] if x.get("cwe")])
        ids = [x for x in ids if x]
        if ids:
            advice.append(f"- 安全风险命中：建议优先核对 {', '.join(ids[:6])} 是否影响你的资产，并排查是否有在野利用/POC")
        else:
            advice.append("- 安全风险命中：建议人工复核命中条目（漏洞/在野/POC/影响版本），并评估是否需要加固")

    # 通知
    if isinstance(notif, dict) and notif.get("status") == "failed":
        advice.append("- 通知发送失败：建议检查 SMTP 配置/授权码、网络、以及收件人地址")

    # 源结构提示：民生政策稀少时给出方向（不硬塞内容）
    if not has_livelihood_policy:
        advice.append("- 民生政策条目偏少：建议优先启用/加权 人社/医保/公积金/教育就业 等 RSS 源，否则摘要只能从少量条目中挑选")

    if not advice:
        advice.append("- 暂无明显异常：建议持续丰富 RSS 源，并观察 7 日趋势是否出现结构性变化")

    title = f"Automation Hub 总结与建议 {brief_date}"
    content_lines = []
    content_lines.append(f"# {title}")
    content_lines.append("")
    content_lines.append("## 发生了什么")
    content_lines.extend(happened)
    content_lines.append("")
    content_lines.append("## 建议")
    content_lines.extend(advice)

    return title, "\n".join(content_lines)


def log_audit_event(conn: sqlite3.Connection, brief_date: str, status: str, results: list[dict] | None) -> None:
    conn.execute(
        """INSERT INTO audit_events(
               id, actor_user_id, actor_device_id, event_type, resource_type,
               resource_id, action, status, message, meta_json, created_at
           ) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
        (
            str(uuid.uuid4()),
            None,
            None,
            "summary",
            "daily_brief",
            brief_date,
            "send",
            status,
            "executive summary sent",
            json.dumps({"results": results or []}, ensure_ascii=False),
            datetime.now().isoformat(),
        ),
    )


def main() -> None:
    p = argparse.ArgumentParser(description="Send an executive summary email based on daily_briefs.stats_json")
    p.add_argument("--date", default="", help="YYYY-MM-DD. Empty means latest")
    p.add_argument("--force", action="store_true", help="Send even if already sent today")
    args = p.parse_args()

    settings.ensure_dirs()
    db.init_db()

    target_date = args.date.strip() or None
    got = fetch_daily_brief_stats(target_date)
    if not got:
        raise SystemExit("No daily_briefs row found. Run scripts/daily_report.py first.")

    brief_date, stats = got

    if (not args.force) and target_date:
        # 如果明确指定 date，允许你自行控制；这里不做强幂等，靠 audit 做追溯
        pass

    title, content = build_executive_summary(brief_date, stats)

    msg = NotificationMessage(
        title=title,
        content=content,
        level="info",
        metadata={"brief_date": brief_date, "kind": "executive_summary"},
    )

    service = NotificationService()
    results = service.send(msg)
    ok = all(r.get("ok") for r in results)

    conn = sqlite3.connect(DB_PATH)
    with conn:
        log_audit_event(conn, brief_date, "succeeded" if ok else "failed", results)
    conn.close()

    print(f"send_summary_results: {results}")


if __name__ == "__main__":
    main()
