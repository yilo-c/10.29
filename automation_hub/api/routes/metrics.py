"""模块说明：Prometheus 指标输出。

说明：
- 指标以 tool_runs（新执行入口）为主，runs（legacy）为辅。
- 采用“拉取时刷新”的方式，避免在 worker / 多进程场景下引入额外状态同步复杂度。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Response
from prometheus_client import CONTENT_TYPE_LATEST, CollectorRegistry, Gauge, Histogram, generate_latest

from automation_hub.api.db import get_db_connection
from automation_hub.api._worker_bridge import arq_queue_length

router = APIRouter(tags=["metrics"])

registry = CollectorRegistry()

# ── HTTP request duration histogram ──────────────────────────────────────
# This Histogram accumulates observations from the logging middleware.
# It is NOT cleared by _refresh_metrics(); Prometheus handles the cumulative
# values natively via generate_latest().
request_duration = Histogram(
    "automation_hub_http_request_duration_seconds",
    "HTTP request duration in seconds (histogram)",
    labelnames=["method", "path", "status_code"],
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0),
    registry=registry,
)


def observe_request_duration(method: str, path: str, status_code: int, duration_seconds: float) -> None:
    """Record an HTTP request duration observation.

    Called by the request logging middleware after each request completes.
    """
    request_duration.labels(method=method, path=path, status_code=str(status_code)).observe(duration_seconds)


run_status_total = Gauge(
    "automation_hub_run_status_total",
    "Number of runs by status",
    ["status"],
    registry=registry,
)
run_total = Gauge(
    "automation_hub_runs_total",
    "Total number of runs",
    registry=registry,
)
run_duration_avg = Gauge(
    "automation_hub_run_duration_seconds_avg",
    "Average run duration in seconds",
    registry=registry,
)
queue_length = Gauge(
    "automation_hub_queue_length",
    "RQ queue length (per primary queue)",
    registry=registry,
)

tool_run_total = Gauge(
    "automation_hub_tool_runs_total",
    "Total number of tool runs",
    registry=registry,
)
tool_run_status_total = Gauge(
    "automation_hub_tool_run_status_total",
    "Number of tool runs by status",
    ["status"],
    registry=registry,
)
tool_run_duration_avg = Gauge(
    "automation_hub_tool_run_duration_seconds_avg",
    "Average tool run duration in seconds",
    registry=registry,
)

approval_pending_total = Gauge(
    "automation_hub_approval_pending_total",
    "Number of approval requests pending decision",
    registry=registry,
)

audit_events_24h_total = Gauge(
    "automation_hub_audit_events_24h_total",
    "Number of audit events in the last 24 hours",
    registry=registry,
)


def _refresh_metrics() -> None:
    run_status_total.clear()
    tool_run_status_total.clear()

    with get_db_connection() as conn:
        # tool_runs（主）
        total_tool_runs = conn.execute("SELECT COUNT(*) FROM tool_runs").fetchone()[0] or 0
        tool_run_total.set(total_tool_runs)

        rows = conn.execute("SELECT status, COUNT(*) AS c FROM tool_runs GROUP BY status").fetchall()
        for row in rows:
            tool_run_status_total.labels(status=row["status"]).set(row["c"])

        avg = conn.execute(
            """
            SELECT AVG((julianday(finished_at) - julianday(started_at)) * 86400.0)
            FROM tool_runs
            WHERE finished_at IS NOT NULL AND started_at IS NOT NULL
            """
        ).fetchone()[0]
        tool_run_duration_avg.set(float(avg) if avg is not None else 0.0)

        # runs（legacy）
        total_runs = conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0] or 0
        run_total.set(total_runs)

        rows = conn.execute("SELECT status, COUNT(*) AS c FROM runs GROUP BY status").fetchall()
        for row in rows:
            run_status_total.labels(status=row["status"]).set(row["c"])

        avg = conn.execute(
            """
            SELECT AVG((julianday(completed_at) - julianday(started_at)) * 86400.0)
            FROM runs
            WHERE completed_at IS NOT NULL AND started_at IS NOT NULL
            """
        ).fetchone()[0]
        run_duration_avg.set(float(avg) if avg is not None else 0.0)

        # approvals
        pending = conn.execute(
            "SELECT COUNT(*) FROM approval_requests WHERE status='pending'"
        ).fetchone()[0] or 0
        approval_pending_total.set(pending)

        # audit events（最近 24h）
        since = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
        ev = conn.execute(
            "SELECT COUNT(*) FROM audit_events WHERE created_at >= ?",
            (since,),
        ).fetchone()[0] or 0
        audit_events_24h_total.set(ev)

    try:
        queue_length.set(arq_queue_length())
    except Exception:
        queue_length.set(-1)


@router.get("/metrics")
def metrics() -> Response:
    _refresh_metrics()
    payload = generate_latest(registry)
    return Response(payload, media_type=CONTENT_TYPE_LATEST)
