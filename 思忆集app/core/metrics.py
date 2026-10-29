# -*- coding: utf-8 -*-
"""Prometheus 兼容的 metrics 采集端点。

提供：
- 请求计数器（按方法、状态码）
- 请求延迟直方图（P50/P95/P99）
- 活跃连接数
- 内存使用
- AI 调用统计

用法：
    # 在 ws_server.py 中注册路由
    app.router.add_get('/metrics', metrics_handler)
"""

from __future__ import annotations

import asyncio  # noqa: F401  (保留与原版一致的导入面)
import gc
import logging
import sys  # noqa: F401  (保留与原版一致的导入面)
import threading
import time
from collections import defaultdict, deque
from typing import Any

logger = logging.getLogger("siyi.metrics")


class MetricsCollector:
    """线程安全的 metrics 采集器。"""

    def __init__(self, histogram_buckets: list[float] | None = None) -> None:
        self._lock = threading.Lock()
        # method -> status -> count
        self._request_counts: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
        # method -> deque[(timestamp, latency_ms)]（上限 10000 条/方法）
        self._latencies: dict[str, deque] = defaultdict(lambda: deque(maxlen=10000))
        # 活跃 WebSocket 连接数
        self._active_connections = 0
        # AI 调用统计（按 provider）
        self._ai_calls: dict[str, int] = defaultdict(int)
        self._ai_errors: dict[str, int] = defaultdict(int)
        # 缓存命中统计
        self._cache_hits = 0
        self._cache_misses = 0
        # 启动时间（用于 uptime）
        self._start_time = time.time()
        # 直方图分桶（秒）
        self._buckets = histogram_buckets or [
            0.005,
            0.01,
            0.025,
            0.05,
            0.1,
            0.25,
            0.5,
            1.0,
            2.5,
            5.0,
            10.0,
        ]

    def record_request(self, method: str, status: str, latency_ms: float) -> None:
        """记录一次 RPC 请求。"""
        with self._lock:
            self._request_counts[method][status] += 1
            self._latencies[method].append((time.time(), latency_ms))

    def inc_connections(self) -> None:
        with self._lock:
            self._active_connections += 1

    def dec_connections(self) -> None:
        with self._lock:
            self._active_connections = max(0, self._active_connections - 1)

    def record_ai_call(self, provider: str, success: bool) -> None:
        """记录 AI 调用。"""
        with self._lock:
            self._ai_calls[provider] += 1
            if not success:
                self._ai_errors[provider] += 1

    def record_cache_hit(self) -> None:
        with self._lock:
            self._cache_hits += 1

    def record_cache_miss(self) -> None:
        with self._lock:
            self._cache_misses += 1

    def get_metrics_text(self) -> str:
        """生成 Prometheus 文本格式 metrics。"""
        with self._lock:
            lines: list[str] = []
            uptime = time.time() - self._start_time

            # ── uptime ──
            lines.append("# HELP siyi_uptime_seconds Server uptime in seconds")
            lines.append("# TYPE siyi_uptime_seconds counter")
            lines.append(f"siyi_uptime_seconds {uptime:.1f}")

            # ── 请求计数 ──
            lines.append("# HELP siyi_rpc_requests_total Total RPC requests by method and status")
            lines.append("# TYPE siyi_rpc_requests_total counter")
            for method, statuses in sorted(self._request_counts.items()):
                for status, count in sorted(statuses.items()):
                    lines.append(f'siyi_rpc_requests_total{{method="{method}",status="{status}"}} {count}')

            # ── 活跃连接 ──
            lines.append("# HELP siyi_active_connections Current active WebSocket connections")
            lines.append("# TYPE siyi_active_connections gauge")
            lines.append(f"siyi_active_connections {self._active_connections}")

            # ── 延迟直方图（仅统计最近 300s）──
            lines.append("# HELP siyi_rpc_latency_seconds RPC latency distribution")
            lines.append("# TYPE siyi_rpc_latency_seconds histogram")
            now = time.time()
            for method, latencies in sorted(self._latencies.items()):
                if not latencies:
                    continue
                recent = [(ts, lat) for ts, lat in latencies if now - ts <= 300]
                if not recent:
                    continue
                latency_values = [lat / 1000.0 for _, lat in recent]
                latency_values.sort()
                for bucket in self._buckets:
                    count = sum(1 for v in latency_values if v <= bucket)
                    lines.append(f'siyi_rpc_latency_seconds_bucket{{method="{method}",le="{bucket}"}} {count}')
                lines.append(f'siyi_rpc_latency_seconds_bucket{{method="{method}",le="+Inf"}} {len(latency_values)}')
                total = sum(latency_values)
                lines.append(f'siyi_rpc_latency_seconds_sum{{method="{method}"}} {total:.6f}')
                lines.append(f'siyi_rpc_latency_seconds_count{{method="{method}"}} {len(latency_values)}')

            # ── AI 调用 ──
            lines.append("# HELP siyi_ai_calls_total Total AI API calls by provider")
            lines.append("# TYPE siyi_ai_calls_total counter")
            for provider, count in sorted(self._ai_calls.items()):
                lines.append(f'siyi_ai_calls_total{{provider="{provider}"}} {count}')

            lines.append("# HELP siyi_ai_errors_total Total AI API errors by provider")
            lines.append("# TYPE siyi_ai_errors_total counter")
            for provider, count in sorted(self._ai_errors.items()):
                lines.append(f'siyi_ai_errors_total{{provider="{provider}"}} {count}')

            # ── 缓存 ──
            lines.append("# HELP siyi_cache_hits_total Cache hit count")
            lines.append("# TYPE siyi_cache_hits_total counter")
            lines.append(f"siyi_cache_hits_total {self._cache_hits}")

            lines.append("# HELP siyi_cache_misses_total Cache miss count")
            lines.append("# TYPE siyi_cache_misses_total counter")
            lines.append(f"siyi_cache_misses_total {self._cache_misses}")

            # ── GC 统计 ──
            gc_counts = gc.get_count()
            lines.append("# HELP siyi_gc_gen0_count Garbage collector generation 0 count")
            lines.append("# TYPE siyi_gc_gen0_count gauge")
            lines.append(f"siyi_gc_gen0_count {gc_counts[0]}")
            lines.append("# HELP siyi_gc_gen1_count Garbage collector generation 1 count")
            lines.append("# TYPE siyi_gc_gen1_count gauge")
            lines.append(f"siyi_gc_gen1_count {gc_counts[1]}")
            lines.append("# HELP siyi_gc_gen2_count Garbage collector generation 2 count")
            lines.append("# TYPE siyi_gc_gen2_count gauge")
            lines.append(f"siyi_gc_gen2_count {gc_counts[2]}")

            # ── 清理 300s 内无任何样本的方法，防止 _latencies 无界增长 ──
            stale_methods = [m for m, lats in self._latencies.items() if lats and all(now - ts > 300 for ts, _ in lats)]
            for m in stale_methods:
                del self._latencies[m]

            return "\n".join(lines) + "\n"


_collector: MetricsCollector | None = None


def get_metrics_collector() -> MetricsCollector:
    global _collector
    if _collector is None:
        _collector = MetricsCollector()
    return _collector


async def metrics_handler(request: Any) -> Any:
    """aiohttp handler for /metrics endpoint."""
    from aiohttp import web

    collector = get_metrics_collector()
    text = collector.get_metrics_text()
    # aiohttp 3.13 起 content_type 参数不允许内嵌 charset；Prometheus 需要
    # `text/plain; version=0.0.4; charset=utf-8` 完整 Content-Type，改走 headers。
    return web.Response(text=text, headers={"Content-Type": "text/plain; version=0.0.4; charset=utf-8"})


__all__ = [
    "MetricsCollector",
    "get_metrics_collector",
    "metrics_handler",
]
