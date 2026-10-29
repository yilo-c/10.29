"""GatewayHealthMonitor — 网关健康监控（契约 §5.3）。

TTL 缓存：``check()`` 在 ``ttl_s`` 内返回上一次 ``gateway.health()`` 结果，
避免高频探测。连续超时标记：``record_outcome`` 累计 ``error_kind == "timeout"``，
达到 ``degraded_after`` 次后置 ``degraded``，供调用方降级到备用内核。
"""
from __future__ import annotations

import time
from typing import Any, Optional


class GatewayHealthMonitor:
    def __init__(self, gateway: Any, ttl_s: float = 60.0, degraded_after: int = 3):
        self.gateway = gateway
        self.ttl_s = ttl_s
        self.degraded_after = degraded_after
        self._cache: Optional[dict] = None
        self._cache_at: float = 0.0
        self._consecutive_timeouts: int = 0
        self._degraded: bool = False

    def check(self) -> dict:
        now = time.monotonic()
        if self._cache is not None and (now - self._cache_at) < self.ttl_s:
            return self._cache
        health = self.gateway.health()
        self._cache = health
        self._cache_at = now
        return health

    def record_outcome(self, run: Any) -> None:
        kind = (getattr(run, "raw", None) or {}).get("error_kind")
        if kind == "timeout":
            self._consecutive_timeouts += 1
            if self._consecutive_timeouts >= self.degraded_after:
                self._degraded = True
        else:
            self._consecutive_timeouts = 0
            self._degraded = False

    @property
    def degraded(self) -> bool:
        return self._degraded

    def reset(self) -> None:
        self._consecutive_timeouts = 0
        self._degraded = False
        self._cache = None
        self._cache_at = 0.0
