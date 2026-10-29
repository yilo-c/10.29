# -*- coding: utf-8 -*-
"""API 聚合层可观测性：持久化聚合调用日志与统计。"""
from __future__ import annotations

import json
import logging
import sqlite3
import threading
import time
from dataclasses import asdict  # noqa: F401  (保留与原版一致的导入面)
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover
    pass

logger = logging.getLogger("siyi.ai.aggregation_telemetry")


class AggregationTelemetry:
    """聚合调用日志。

    记录每次聚合调用的策略、provider、延迟、成本、成功/失败状态，
    用于后续 dashboard 分析和配额告警。
    """

    def __init__(self, db_path: str | Path | None = None) -> None:
        if db_path is None:
            db_path = Path.home() / ".siyi" / "data" / "aggregation_telemetry.db"
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._init_db()

    def _connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._lock:
            with self._connection() as conn:
                conn.executescript(
                    """
                CREATE TABLE IF NOT EXISTS aggregation_calls (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp REAL NOT NULL,
                    strategy TEXT NOT NULL,
                    winner TEXT,
                    latency_ms INTEGER,
                    cost_estimate REAL,
                    attempted TEXT,
                    failed TEXT,
                    all_results TEXT,
                    content_length INTEGER
                );
                CREATE INDEX IF NOT EXISTS idx_calls_timestamp
                    ON aggregation_calls(timestamp);
                CREATE INDEX IF NOT EXISTS idx_calls_strategy
                    ON aggregation_calls(strategy);
                CREATE INDEX IF NOT EXISTS idx_calls_winner
                    ON aggregation_calls(winner);

                -- S1+S2: AI 调用全链路追踪。
                -- 记录 FallbackOpenAIClient 的每次回退（primary→fallback→local→cache），
                -- 同一次 chat.send 的多次回退通过 correlation_id 关联。
                CREATE TABLE IF NOT EXISTS call_traces (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp REAL NOT NULL,
                    correlation_id TEXT NOT NULL,
                    provider_name TEXT NOT NULL,
                    stage TEXT NOT NULL,
                    success INTEGER NOT NULL DEFAULT 0,
                    latency_ms INTEGER,
                    error TEXT,
                    token_count INTEGER
                );
                CREATE INDEX IF NOT EXISTS idx_traces_timestamp
                    ON call_traces(timestamp);
                CREATE INDEX IF NOT EXISTS idx_traces_correlation
                    ON call_traces(correlation_id);
                CREATE INDEX IF NOT EXISTS idx_traces_provider
                    ON call_traces(provider_name);
                -- P3.4 查询优化：get_trace() 按 correlation_id 过滤 + timestamp ASC 排序
                -- 复合索引避免排序开销和全表扫描，大表下必要
                CREATE INDEX IF NOT EXISTS idx_traces_corr_ts
                    ON call_traces(correlation_id, timestamp ASC);
                -- P3.4：error_rate() 按 WHERE timestamp >= ? AND success = 0 ORDER BY timestamp DESC
                -- success=0 的行占比极小（通常 <5%），部分索引几乎零成本，避免全表扫描开销
                CREATE INDEX IF NOT EXISTS idx_traces_error_ts
                    ON call_traces(timestamp DESC) WHERE success = 0;
                """
                )

    def log(self, result: Any, content: str = "") -> None:
        """记录一次聚合调用结果。"""
        with self._lock:
            try:
                with self._connection() as conn:
                    conn.execute(
                        """
                    INSERT INTO aggregation_calls
                        (timestamp, strategy, winner, latency_ms, cost_estimate,
                         attempted, failed, all_results, content_length)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                        (
                            time.time(),
                            result.strategy,
                            result.winner,
                            result.latency_ms,
                            result.cost_estimate,
                            json.dumps(result.attempted, ensure_ascii=False),
                            json.dumps(result.failed, ensure_ascii=False),
                            json.dumps(
                                [
                                    {
                                        "provider_name": r.provider_name,
                                        "model": r.model,
                                        "success": r.success,
                                        "latency_ms": r.latency_ms,
                                        "error": r.error,
                                        "tokens_input": r.tokens_input,
                                        "tokens_output": r.tokens_output,
                                    }
                                    for r in result.all_results
                                ],
                                ensure_ascii=False,
                            ),
                            len(content),
                        ),
                    )
            except Exception as exc:
                logger.debug("failed to log aggregation telemetry: %s", exc)

    def query(
        self,
        *,
        since: float | None = None,
        until: float | None = None,
        strategy: str | None = None,
        winner: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """查询聚合调用日志。"""
        conditions: list[str] = []
        params: list[Any] = []
        if since is not None:
            conditions.append("timestamp >= ?")
            params.append(since)
        if until is not None:
            conditions.append("timestamp <= ?")
            params.append(until)
        if strategy is not None:
            conditions.append("strategy = ?")
            params.append(strategy)
        if winner is not None:
            conditions.append("winner = ?")
            params.append(winner)
        where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
        sql = f"""
            SELECT * FROM aggregation_calls
            {where}
            ORDER BY timestamp DESC
            LIMIT ? OFFSET ?
        """
        params.extend([limit, offset])
        with self._lock:
            with self._connection() as conn:
                rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]

    def stats(self, since: float | None = None) -> dict[str, Any]:
        """返回聚合调用统计。"""
        if since is None:
            since = time.time() - 86400
        with self._lock:
            with self._connection() as conn:
                row = conn.execute(
                    """
                SELECT
                    COUNT(*) as total_calls,
                    AVG(latency_ms) as avg_latency_ms,
                    SUM(cost_estimate) as total_cost,
                    AVG(cost_estimate) as avg_cost,
                    COUNT(DISTINCT winner) as unique_winners
                FROM aggregation_calls
                WHERE timestamp >= ?
                """,
                    (since,),
                ).fetchone()
                winners = [
                    {"winner": w["winner"], "count": w["cnt"]}
                    for w in conn.execute(
                        """
                SELECT winner, COUNT(*) as cnt
                FROM aggregation_calls
                WHERE timestamp >= ?
                GROUP BY winner
                ORDER BY cnt DESC
                LIMIT 10
                """,
                        (since,),
                    ).fetchall()
                ]
                strategies = [
                    {
                        "strategy": s["strategy"],
                        "count": s["cnt"],
                        "avg_latency_ms": round(s["avg_latency_ms"] or 0, 2),
                    }
                    for s in conn.execute(
                        """
                SELECT strategy, COUNT(*) as cnt, AVG(latency_ms) as avg_latency_ms
                FROM aggregation_calls
                WHERE timestamp >= ?
                GROUP BY strategy
                ORDER BY cnt DESC
                """,
                        (since,),
                    ).fetchall()
                ]
        return {
            "total_calls": row["total_calls"],
            "avg_latency_ms": round(row["avg_latency_ms"] or 0, 2),
            "total_cost": round(row["total_cost"] or 0, 6),
            "avg_cost": round(row["avg_cost"] or 0, 6),
            "unique_winners": row["unique_winners"],
            "winners": winners,
            "strategies": strategies,
        }

    def reset(self) -> None:
        """清空日志。"""
        with self._lock:
            with self._connection() as conn:
                conn.execute("DELETE FROM aggregation_calls")
                conn.execute("DELETE FROM call_traces")

    def record_trace(
        self,
        *,
        correlation_id: str,
        provider_name: str,
        stage: str,
        success: bool,
        latency_ms: int | None = None,
        error: str | None = None,
        token_count: int | None = None,
    ) -> None:
        """记录单次 AI 调用的链路追踪。

        Args:
            correlation_id: 同一次 chat.send 的唯一标识，用于关联多次回退。
            provider_name: provider 名称（如 "deepseek" / "moonshot" / "ollama"）。
            stage: 调用阶段，取值: 'primary' | 'fallback' | 'local' | 'cache' | 'aggregate'。
            success: 是否成功。
            latency_ms: 调用耗时（毫秒）。
            error: 失败时的错误信息。
            token_count: 本次调用的 token 数。
        """
        with self._lock:
            try:
                with self._connection() as conn:
                    conn.execute(
                        """
                    INSERT INTO call_traces
                        (timestamp, correlation_id, provider_name, stage,
                         success, latency_ms, error, token_count)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                        (
                            time.time(),
                            correlation_id,
                            provider_name,
                            stage,
                            1 if success else 0,
                            latency_ms,
                            error,
                            token_count,
                        ),
                    )
            except Exception as exc:
                logger.debug("failed to record call trace: %s", exc)

    def get_trace(self, correlation_id: str) -> list[dict[str, Any]]:
        """查询某次调用的完整链路（按时间正序）。"""
        with self._lock:
            with self._connection() as conn:
                rows = conn.execute(
                    """
                SELECT * FROM call_traces
                WHERE correlation_id = ?
                ORDER BY timestamp ASC
                """,
                    (correlation_id,),
                ).fetchall()
        return [dict(r) for r in rows]

    def error_rate(self, since_seconds: float = 300.0) -> dict[str, Any]:
        """返回最近一段时间的错误率统计。

        Args:
            since_seconds: 统计窗口（秒），默认 300（5 分钟）。

        Returns:
            包含 total/successful/failed/error_rate/recent_errors 字段的字典。
        """
        cutoff = time.time() - since_seconds
        with self._lock:
            with self._connection() as conn:
                row = conn.execute(
                    """
                SELECT
                    COUNT(*) as total,
                    SUM(success) as successful,
                    SUM(CASE WHEN success = 0 THEN 1 ELSE 0 END) as failed
                FROM call_traces
                WHERE timestamp >= ?
                """,
                    (cutoff,),
                ).fetchone()
                recent_errors = [
                    dict(e)
                    for e in conn.execute(
                        """
                SELECT provider_name, stage, error, timestamp
                FROM call_traces
                WHERE timestamp >= ? AND success = 0
                ORDER BY timestamp DESC
                LIMIT 20
                """,
                        (cutoff,),
                    ).fetchall()
                ]
        total = row["total"] or 0
        failed = row["failed"] or 0
        successful = row["successful"] or 0
        return {
            "window_seconds": since_seconds,
            "total_calls": total,
            "successful_calls": successful,
            "failed_calls": failed,
            "error_rate": round(failed / total, 4) if total else 0.0,
            "recent_errors": recent_errors,
        }


__all__ = ["AggregationTelemetry"]
