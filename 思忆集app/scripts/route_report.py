# -*- coding: utf-8 -*-
"""route_report.py — chat 路由质量统一读数口（M4.0 基线采点 + D20 观察窗）。

净新建（C-38 观察窗前置 · 任务二）。**只读**访问，不写任何库、不改动运行态。

读三类数据源：
1. ``ai_usage.db`` / ``usage_records``：内核经网关执行用量（source='kernel:*'）
   ——kernel_fail_ratio、真实/估算样本占比（estimated）、成本 unknown 计数。
2. ``trace.db`` / ``task_runs``：内核执行 run（source='kernel:*'）
   ——TTFB 的**替代源**（usage 无时延字段时，用 finished_at-started_at 的端到端
   run 时延近似，登记为 proxy，非真实首字时延）。
3. chat 路由埋点（若存在）：任一库中的 ``route_decisions`` 表，或 usage 里
   ``source IN ('route:fast','route:deep','route:shell')`` ——算 fast_ratio。
   **当前无此埋点 → fast_ratio 记 None（缺字段），绝不拿 shell 直调冒充路由占比。**

观察窗判据（--window，D20 原文口径）：**14 天窗口内 kernel 调用（source=kernel:*）≥20 次
且 kernel 成功率 ≥90%**。三态输出：
  - PASS   ：窗口内 kernel 样本 ≥20 且 成功率 ≥90%
  - FAIL   ：窗口内 kernel 样本 ≥20 且 成功率 <90%
  - 样本不足：kernel 调用 <20 次 / 有效跨度数据不足（不可据此放行翻转）
fast_ratio（路由占比）为**观测项非放行判据**（2026-09-12 用户裁决降级）：
  埋点缺失时记 None 并进告警，**绝不拿 shell 直调冒充路由占比，也不因其缺失卡死观察窗**。

用法::

    python scripts/route_report.py                      # 近 7 天，md
    python scripts/route_report.py --days 30 --format json
    python scripts/route_report.py --window             # D20 观察窗三态
    python scripts/route_report.py --usage-db <path> --trace-db <path>
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from typing import Any

# 判据常量（D20 观察窗·原文口径）
WINDOW_DAYS = 14
WINDOW_MIN_SAMPLES = 20
WINDOW_SUCCESS_RATE_THRESHOLD = 0.90


# ── 只读连接与查询 ──────────────────────────────────────────────
def connect_ro(path: str) -> sqlite3.Connection | None:
    """以只读方式打开 SQLite；文件不存在返回 None。

    先试 mode=ro（尊重 -wal，能读到最新已提交数据）；若因 -shm/权限受限失败，
    退化为 immutable=1（假设无并发写，纯静态读）。两种方式都不写库。
    """
    if not path or not os.path.isfile(path):
        return None
    uri_ro = f"file:{path}?mode=ro"
    try:
        conn = sqlite3.connect(uri_ro, uri=True, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("SELECT 1 FROM sqlite_master LIMIT 1").fetchone()
        return conn
    except sqlite3.Error:
        pass
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro&immutable=1", uri=True, timeout=10)
        conn.row_factory = sqlite3.Row
        return conn
    except sqlite3.Error:
        return None


def table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    try:
        return {r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    except sqlite3.Error:
        return set()


def has_table(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type IN ('table','view') AND name=? LIMIT 1",
        (table,),
    ).fetchone()
    return row is not None


def _percentile(sorted_vals: list[float], pct: float) -> float | None:
    """线性插值分位数；空集返回 None。sorted_vals 须已升序。"""
    if not sorted_vals:
        return None
    if len(sorted_vals) == 1:
        return float(sorted_vals[0])
    k = (len(sorted_vals) - 1) * (pct / 100.0)
    lo = int(k)
    hi = min(lo + 1, len(sorted_vals) - 1)
    frac = k - lo
    return float(sorted_vals[lo] * (1 - frac) + sorted_vals[hi] * frac)


# ── 指标计算 ────────────────────────────────────────────────────
def compute_usage_metrics(usage_db: str, days: int) -> dict[str, Any]:
    """从 ai_usage.db 计算 kernel_fail_ratio / estimated 占比 / unknown 成本 / fast_ratio 埋点。"""
    out: dict[str, Any] = {
        "usage_db": usage_db,
        "db_found": False,
        "window_start": None,
        "total_calls": 0,
        "kernel_calls": 0,
        "kernel_fail": 0,
        "kernel_fail_ratio": None,
        "estimated_calls": 0,
        "precise_calls": 0,
        "unknown_cost_calls": 0,
        "known_cost": 0.0,
        "fast_ratio": None,
        "fast_ratio_source": None,
        "route_telemetry_present": False,
        "kernel_by_model": [],
    }
    conn = connect_ro(usage_db)
    if conn is None:
        return out
    out["db_found"] = True
    cols = table_columns(conn, "usage_records")
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    out["window_start"] = cutoff

    # 路由埋点（fast_ratio）：优先专用表 route_decisions，其次 usage.source 里的 route:* 标记
    if has_table(conn, "route_decisions"):
        rd_cols = table_columns(conn, "route_decisions")
        col = "route" if "route" in rd_cols else ("decision" if "decision" in rd_cols else None)
        if col:
            fast = conn.execute(
                f"SELECT COUNT(*) FROM route_decisions WHERE lower({col})='fast'"
            ).fetchone()[0]
            allr = conn.execute(
                f"SELECT COUNT(*) FROM route_decisions WHERE lower({col}) IN ('fast','deep','kernel','shell')"
            ).fetchone()[0]
            if allr:
                out["route_telemetry_present"] = True
                out["fast_ratio"] = round(fast / allr, 4)
                out["fast_ratio_source"] = "route_decisions"

    # usage 侧 route 埋点（若来源里出现 route: 前缀）
    rt = conn.execute(
        "SELECT source, COUNT(*) c FROM usage_records WHERE timestamp>=? AND source LIKE 'route:%' GROUP BY source",
        (cutoff,),
    ).fetchall()
    if rt:
        counts = {r["source"]: r["c"] for r in rt}
        fast_n = counts.get("route:fast", 0)
        total_n = sum(counts.values())
        if total_n:
            out["route_telemetry_present"] = True
            out["fast_ratio"] = round(fast_n / total_n, 4)
            out["fast_ratio_source"] = "usage.source=route:*"

    out["total_calls"] = conn.execute(
        "SELECT COUNT(*) FROM usage_records WHERE timestamp>=?", (cutoff,)
    ).fetchone()[0]

    k_total = conn.execute(
        "SELECT COUNT(*) FROM usage_records WHERE timestamp>=? AND source LIKE 'kernel:%'",
        (cutoff,),
    ).fetchone()[0]
    k_fail = conn.execute(
        "SELECT COUNT(*) FROM usage_records WHERE timestamp>=? AND source LIKE 'kernel:%' AND success=0",
        (cutoff,),
    ).fetchone()[0]
    out["kernel_calls"] = k_total
    out["kernel_fail"] = k_fail
    out["kernel_fail_ratio"] = round(k_fail / k_total, 4) if k_total else None

    if "estimated" in cols:
        out["estimated_calls"] = conn.execute(
            "SELECT COUNT(*) FROM usage_records WHERE timestamp>=? AND estimated=1", (cutoff,)
        ).fetchone()[0]
    out["precise_calls"] = out["total_calls"] - out["estimated_calls"]

    if "pricing" in cols:
        out["unknown_cost_calls"] = conn.execute(
            "SELECT COUNT(*) FROM usage_records WHERE timestamp>=? AND pricing='unknown'", (cutoff,)
        ).fetchone()[0]
    out["known_cost"] = round(
        conn.execute(
            "SELECT COALESCE(SUM(cost_estimate),0.0) FROM usage_records WHERE timestamp>=?"
            + (" AND pricing='known'" if "pricing" in cols else ""),
            (cutoff,),
        ).fetchone()[0],
        6,
    )

    for r in conn.execute(
        "SELECT provider, model, source, COUNT(*) c, SUM(total_tokens) tok "
        "FROM usage_records WHERE timestamp>=? AND source LIKE 'kernel:%' "
        "GROUP BY provider, model, source ORDER BY c DESC LIMIT 20",
        (cutoff,),
    ).fetchall():
        out["kernel_by_model"].append({
            "provider": r["provider"], "model": r["model"], "source": r["source"],
            "calls": r["c"], "tokens": int(r["tok"] or 0),
        })
    conn.close()
    return out


def compute_ttfb_proxy(trace_db: str, days: int) -> dict[str, Any]:
    """usage 无时延字段时，用 trace.db task_runs 的内核 run 端到端时延做 TTFB 替代源。"""
    out: dict[str, Any] = {
        "trace_db": trace_db,
        "db_found": False,
        "kernel_runs": 0,
        "kernel_run_failed": 0,
        "ttfb_field_in_usage": False,   # usage_records 无时延列（已核实）
        "latency_source": "trace.db:task_runs(finished_at-started_at)",
        "is_proxy": True,               # 端到端 run 时延 ≠ 真实首字时延
        "p50_ms": None,
        "p95_ms": None,
        "n_samples": 0,
        "min_span_days": None,
        "window_fail_ratio": None,
    }
    conn = connect_ro(trace_db)
    if conn is None:
        return out
    out["db_found"] = True
    cols = table_columns(conn, "task_runs")
    if "started_at" not in cols or "source" not in cols:
        conn.close()
        return out
    since = datetime.now(timezone.utc).timestamp() - days * 86400
    dur_sql = (
        "SELECT (finished_at - started_at) AS dur FROM task_runs "
        "WHERE source LIKE 'kernel:%' AND finished_at IS NOT NULL AND started_at >= ?"
    )
    durs = [row[0] for row in conn.execute(dur_sql, (since,)).fetchall() if row[0] is not None and row[0] > 0]
    durs.sort()
    out["n_samples"] = len(durs)
    out["p50_ms"] = round(_percentile(durs, 50) * 1000, 1) if durs else None
    out["p95_ms"] = round(_percentile(durs, 95) * 1000, 1) if durs else None

    out["kernel_runs"] = conn.execute(
        "SELECT COUNT(*) FROM task_runs WHERE source LIKE 'kernel:%' AND started_at >= ?", (since,)
    ).fetchone()[0]
    fin_sql = (
        "SELECT status, COUNT(*) c FROM task_runs WHERE source LIKE 'kernel:%' "
        "AND status IN ('completed','failed','interrupted') AND started_at >= ? GROUP BY status"
    )
    st = {r["status"]: r["c"] for r in conn.execute(fin_sql, (since,)).fetchall()}
    fin = st.get("completed", 0) + st.get("failed", 0) + st.get("interrupted", 0)
    out["kernel_run_failed"] = st.get("failed", 0) + st.get("interrupted", 0)
    out["window_fail_ratio"] = round(out["kernel_run_failed"] / fin, 4) if fin else None

    span = conn.execute(
        "SELECT MIN(started_at), MAX(started_at) FROM task_runs WHERE source LIKE 'kernel:%'"
    ).fetchone()
    if span and span[0] and span[1]:
        out["min_span_days"] = round((span[1] - span[0]) / 86400.0, 2)
    conn.close()
    return out


# ── D20 观察窗三态 ──────────────────────────────────────────────
def evaluate_window(usage: dict[str, Any], ttfb: dict[str, Any]) -> dict[str, Any]:
    """判据（D20 原文）：14 天窗口内 kernel 调用 ≥20 次 且 kernel 成功率 ≥90%。

    三态 PASS / FAIL / 样本不足。fast_ratio 为观测项（2026-09-12 用户裁决降级）：
    路由埋点缺失只进告警不阻断判定；成功率取 usage 的 1-kernel_fail_ratio
    （source=kernel:* 口径，C-38 修复后可信）。有效跨度以 trace.db 内核 run 度量。
    """
    fast_ratio = usage.get("fast_ratio")
    telemetry = usage.get("route_telemetry_present")
    kernel_samples = int(usage.get("kernel_calls") or 0)
    kfail = usage.get("kernel_fail_ratio")
    kernel_success_rate = (1 - kfail) if kfail is not None else None
    span_days = ttfb.get("min_span_days")
    effective_span = WINDOW_DAYS  # 判据固定 14 天窗口
    reasons: list[str] = []
    hard: list[str] = []
    if kernel_samples < WINDOW_MIN_SAMPLES:
        hard.append(f"样本不足：窗口内 kernel 调用 {kernel_samples} 次 < {WINDOW_MIN_SAMPLES}")
    if kernel_success_rate is None:
        hard.append("样本不足：窗口内 kernel 记录缺 success 字段")
    if span_days is not None and span_days < WINDOW_DAYS:
        hard.append(f"有效跨度不足：内核 run 数据仅覆盖 {span_days} 天 < {WINDOW_DAYS}")
    if not telemetry or fast_ratio is None:
        reasons.append("告警（非阻断）：fast_ratio 缺路由埋点，已降级为观测项（埋点归 M1 尾补建）")
    if hard:
        verdict = "样本不足"
    elif kernel_success_rate >= WINDOW_SUCCESS_RATE_THRESHOLD:
        verdict = "PASS"
    else:
        verdict = "FAIL"
    return {
        "mode": "window",
        "criteria": {
            "days": WINDOW_DAYS,
            "min_samples": WINDOW_MIN_SAMPLES,
            "kernel_success_rate_threshold": WINDOW_SUCCESS_RATE_THRESHOLD,
            "fast_ratio_role": "observation_only",
        },
        "kernel_calls": kernel_samples,
        "kernel_success_rate": None if kernel_success_rate is None else round(kernel_success_rate, 4),
        "fast_ratio": fast_ratio,
        "route_telemetry_present": bool(telemetry),
        "kernel_run_span_days": span_days,
        "effective_days_window": effective_span,
        "verdict": verdict,
        "reasons": hard + reasons,
    }


# ── 渲染 ────────────────────────────────────────────────────────
def _fmt_ratio(v: float | None) -> str:
    return "N/A（缺字段）" if v is None else f"{v * 100:.1f}%"


def render_md(usage: dict[str, Any], ttfb: dict[str, Any], window: dict[str, Any] | None, days: int) -> str:
    L: list[str] = []
    L.append("# 思忆集 chat 路由质量读数")
    L.append("")
    L.append(f"- 生成时间(UTC)：{datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}")
    L.append(f"- 读数窗口：近 {days} 天")
    L.append(f"- ai_usage.db：`{usage['usage_db']}`（{'命中' if usage['db_found'] else '未找到'}）")
    L.append(f"- trace.db：`{ttfb['trace_db']}`（{'命中' if ttfb['db_found'] else '未找到'}）")
    L.append("")
    L.append("## 核心指标")
    L.append("")
    L.append("| 指标 | 值 | 来源/口径 |")
    L.append("| --- | --- | --- |")
    L.append(f"| fast_ratio（快档占比） | {_fmt_ratio(usage['fast_ratio'])} | "
             f"{'真实埋点:' + str(usage['fast_ratio_source']) if usage['route_telemetry_present'] else '缺路由埋点 — 不冒充'} |")
    if ttfb["p50_ms"] is None:
        L.append("| TTFB p50 | N/A | usage 无时延字段；trace 无内核 run 样本 |")
        L.append("| TTFB p95 | N/A | 同上 |")
    else:
        L.append(f"| TTFB p50（proxy） | {ttfb['p50_ms']} ms | trace 内核 run 端到端时延（**非真实首字**） |")
        L.append(f"| TTFB p95（proxy） | {ttfb['p95_ms']} ms | 样本 {ttfb['n_samples']} 个内核 run |")
    L.append(f"| kernel_fail_ratio | {_fmt_ratio(usage['kernel_fail_ratio'])} | "
             f"usage source=kernel:* success=0（{usage['kernel_fail']}/{usage['kernel_calls']}） |")
    L.append(f"| trace 内核 run 失败率 | {_fmt_ratio(ttfb['window_fail_ratio'])} | "
             f"task_runs status=failed/interrupted（交叉校验） |")
    L.append("")
    L.append("## 精度与成本（C-38 修复后可读）")
    L.append("")
    L.append(f"- 窗口内总调用：{usage['total_calls']}（estimated 估算样本：{usage['estimated_calls']}，"
             f"精确样本：{usage['precise_calls']}）")
    L.append(f"- 成本 unknown 调用数：{usage['unknown_cost_calls']}（模型无登记单价，cost 不冒充真实花费）")
    L.append(f"- 已知定价合计成本(USD)：{usage['known_cost']}")
    if usage["kernel_by_model"]:
        L.append("- 内核执行按 provider/model：")
        for row in usage["kernel_by_model"]:
            L.append(f"    - {row['provider']} / {row['model']} （{row['source']}）：{row['calls']} 次 / {row['tokens']} tok")
    else:
        L.append("- 内核执行：窗口内无 kernel:* 记录")
    L.append("")
    if window is not None:
        L.append("## D20 观察窗三态（--window）")
        L.append("")
        c = window["criteria"]
        L.append(
            f"- 判据（D20 原文）：{c['days']} 天窗口 kernel 调用 ≥{c['min_samples']} 次"
            f" 且 kernel 成功率 ≥{c['kernel_success_rate_threshold'] * 100:.0f}%"
            f"（fast_ratio={c['fast_ratio_role']}，观测项）"
        )
        L.append(
            f"- 窗口内 kernel 调用：{window['kernel_calls']} 次，"
            f"kernel 成功率：{_fmt_ratio(window['kernel_success_rate'])}"
        )
        L.append(
            f"- fast_ratio（观测，非放行判据）：{_fmt_ratio(window['fast_ratio'])}"
            f"（埋点 {'有' if window['route_telemetry_present'] else '缺失'}）"
        )
        L.append(f"- 内核 run 数据跨度：{window['kernel_run_span_days']} 天")
        L.append(f"- **判定：{window['verdict']}**")
        if window["reasons"]:
            L.append("- 原因：")
            for r in window["reasons"]:
                L.append(f"    - {r}")
        L.append("")
    return "\n".join(L)


def build_report(usage_db: str, trace_db: str, days: int, window_mode: bool) -> dict[str, Any]:
    usage = compute_usage_metrics(usage_db, days)
    ttfb = compute_ttfb_proxy(trace_db, days)
    window = evaluate_window(usage, ttfb) if window_mode else None
    return {"days": days, "usage": usage, "ttfb": ttfb, "window": window}


def default_db_paths() -> tuple[str, str]:
    app_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # 思忆集app/
    data_dir = os.path.join(app_root, "data", "data")
    return os.path.join(data_dir, "ai_usage.db"), os.path.join(data_dir, "trace.db")


def main(argv: list[str] | None = None) -> int:
    ud, td = default_db_paths()
    ap = argparse.ArgumentParser(description="思忆集 chat 路由质量读数（M4.0 基线 / D20 观察窗）")
    ap.add_argument("--days", type=int, default=7, help="读数窗口天数（默认 7）")
    ap.add_argument("--format", choices=["md", "json"], default="md", help="输出格式")
    ap.add_argument("--window", action="store_true", help="启用 D20 观察窗三态判定（14天≥20次≥90%）")
    ap.add_argument("--usage-db", default=ud, help="ai_usage.db 路径")
    ap.add_argument("--trace-db", default=td, help="trace.db 路径")
    args = ap.parse_args(argv)

    report = build_report(args.usage_db, args.trace_db, args.days, args.window)
    if args.format == "json":
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(render_md(report["usage"], report["ttfb"], report["window"], args.days))
    if args.window and report["window"]:
        # 三态退出码：PASS=0 / FAIL=1 / 样本不足=2（便于 CI/脚本区分）
        return {"PASS": 0, "FAIL": 1}.get(report["window"]["verdict"], 2)
    return 0


if __name__ == "__main__":
    sys.exit(main())
