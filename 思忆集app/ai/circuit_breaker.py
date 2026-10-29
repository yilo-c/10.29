"""AI 服务熔断器 — 防止级联故障。

当某个 AI 提供方连续失败达到阈值时，自动熔断（跳过该提供方），
经过冷却期后半开探测，成功则恢复。

状态机：CLOSED → OPEN → HALF_OPEN → CLOSED
"""
from __future__ import annotations

import asyncio
import logging
import threading
import time
from dataclasses import dataclass
from enum import Enum
from typing import Any

logger = logging.getLogger("siyi.ai.circuit_breaker")


class CircuitState(Enum):
    """熔断器状态。"""
    CLOSED = "closed"        # 正常 — 允许请求通过
    OPEN = "open"            # 熔断 — 跳过该提供方
    HALF_OPEN = "half_open"  # 半开 — 允许单次探测请求


@dataclass
class CircuitBreakerStats:
    """熔断器统计信息。"""
    total_calls: int = 0
    total_successes: int = 0
    total_failures: int = 0
    consecutive_failures: int = 0
    last_failure_time: float = 0.0
    last_success_time: float = 0.0
    state_transitions: int = 0


@dataclass
class CircuitBreakerConfig:
    """熔断器配置。"""
    failure_threshold: int = 5          # 连续失败次数触发熔断
    success_threshold: int = 2          # 半开状态连续成功次数恢复
    timeout_seconds: float = 60.0       # 熔断冷却期（秒）
    half_open_max_calls: int = 1        # 半开状态最大并发探测数


class CircuitBreaker:
    """单个提供方的熔断器。"""

    def __init__(self, name: str, config: CircuitBreakerConfig | None = None) -> None:
        self.name = name
        self.config = config or CircuitBreakerConfig()
        self._state = CircuitState.CLOSED
        self._stats = CircuitBreakerStats()
        self._half_open_calls = 0
        self._half_open_successes = 0
        self._lock = asyncio.Lock()
        self._sync_lock = threading.Lock()

    @property
    def state(self) -> CircuitState:
        return self._state

    @property
    def stats(self) -> CircuitBreakerStats:
        return self._stats

    def is_call_permitted(self) -> bool:
        """检查是否允许发起调用。"""
        with self._sync_lock:
            if self._state == CircuitState.CLOSED:
                return True
            elif self._state == CircuitState.OPEN:
                # 检查冷却期是否已过
                elapsed = time.monotonic() - self._stats.last_failure_time
                if elapsed >= self.config.timeout_seconds:
                    # 转为半开状态
                    self._transition_to(CircuitState.HALF_OPEN)
                    self._half_open_calls = 1
                    return True
                return False
            elif self._state == CircuitState.HALF_OPEN:
                # 半开状态：限制并发探测数
                if self._half_open_calls < self.config.half_open_max_calls:
                    self._half_open_calls += 1
                    return True
                return False
            return False

    def record_success_sync(self) -> None:
        """记录成功调用（同步版本）。"""
        with self._sync_lock:
            self._stats.total_calls += 1
            self._stats.total_successes += 1
            self._stats.consecutive_failures = 0
            self._stats.last_success_time = time.monotonic()

            if self._state == CircuitState.HALF_OPEN:
                self._half_open_calls = max(0, self._half_open_calls - 1)
                self._half_open_successes += 1
                # 半开连续成功达到阈值 → 恢复
                if self._half_open_successes >= self.config.success_threshold:
                    self._transition_to(CircuitState.CLOSED)
                    self._half_open_successes = 0

    def record_failure_sync(self, error: Exception | None = None) -> None:
        """记录失败调用（同步版本）。"""
        with self._sync_lock:
            self._stats.total_calls += 1
            self._stats.total_failures += 1
            self._stats.consecutive_failures += 1
            self._stats.last_failure_time = time.monotonic()

            if self._state == CircuitState.HALF_OPEN:
                # 半开探测失败 → 重新熔断
                self._half_open_calls = max(0, self._half_open_calls - 1)
                self._transition_to(CircuitState.OPEN)
            elif self._state == CircuitState.CLOSED:
                # 连续失败达到阈值 → 熔断
                if self._stats.consecutive_failures >= self.config.failure_threshold:
                    self._transition_to(CircuitState.OPEN)
                    logger.warning(
                        "Circuit breaker OPEN for %s after %d consecutive failures",
                        self.name, self._stats.consecutive_failures,
                    )

    async def record_success(self) -> None:
        """记录成功调用。"""
        async with self._lock:
            self.record_success_sync()

    async def record_failure(self, error: Exception | None = None) -> None:
        """记录失败调用。"""
        async with self._lock:
            self.record_failure_sync(error)

    def _transition_to(self, new_state: CircuitState) -> None:
        """状态转换。"""
        if self._state != new_state:
            old_state = self._state
            self._state = new_state
            self._stats.state_transitions += 1
            if new_state == CircuitState.HALF_OPEN:
                self._half_open_calls = 0
                self._half_open_successes = 0
            logger.info(
                "Circuit breaker %s: %s → %s",
                self.name, old_state.value, new_state.value,
            )

    def get_status(self) -> dict[str, Any]:
        """获取熔断器状态快照。"""
        return {
            "name": self.name,
            "state": self._state.value,
            "stats": {
                "total_calls": self._stats.total_calls,
                "total_successes": self._stats.total_successes,
                "total_failures": self._stats.total_failures,
                "consecutive_failures": self._stats.consecutive_failures,
                "last_failure_time": self._stats.last_failure_time,
                "last_success_time": self._stats.last_success_time,
                "state_transitions": self._stats.state_transitions,
            },
            "config": {
                "failure_threshold": self.config.failure_threshold,
                "success_threshold": self.config.success_threshold,
                "timeout_seconds": self.config.timeout_seconds,
            },
        }

    def reset(self) -> None:
        """手动重置熔断器。"""
        with self._sync_lock:
            self._state = CircuitState.CLOSED
            self._stats.consecutive_failures = 0
            self._half_open_calls = 0
            self._half_open_successes = 0
        logger.info("Circuit breaker %s manually reset", self.name)


class CircuitBreakerRegistry:
    """熔断器注册中心 — 管理多个提供方的熔断器。"""

    def __init__(self, default_config: CircuitBreakerConfig | None = None) -> None:
        self._breakers: dict[str, CircuitBreaker] = {}
        self._default_config = default_config or CircuitBreakerConfig()
        self._lock = asyncio.Lock()
        # 同步方法（get_or_create / get / record_*_sync）的并发保护
        self._sync_lock = threading.Lock()

    def get_or_create(self, name: str, config: CircuitBreakerConfig | None = None) -> CircuitBreaker:
        """获取或创建熔断器（线程安全）。"""
        with self._sync_lock:
            if name not in self._breakers:
                self._breakers[name] = CircuitBreaker(name, config or self._default_config)
            return self._breakers[name]

    def get(self, name: str) -> CircuitBreaker | None:
        """获取熔断器（不自动创建）。"""
        return self._breakers.get(name)

    def record_success_sync(self, name: str) -> None:
        """记录成功（同步版本）。"""
        breaker = self.get(name)
        if breaker:
            breaker.record_success_sync()

    def record_failure_sync(self, name: str, error: Exception | None = None) -> None:
        """记录失败（同步版本）。"""
        breaker = self.get(name)
        if breaker:
            breaker.record_failure_sync(error)

    async def record_success(self, name: str) -> None:
        """记录成功。"""
        breaker = self.get(name)
        if breaker:
            await breaker.record_success()

    async def record_failure(self, name: str, error: Exception | None = None) -> None:
        """记录失败。"""
        breaker = self.get(name)
        if breaker:
            await breaker.record_failure(error)

    def is_call_permitted(self, name: str) -> bool:
        """检查调用是否被允许。"""
        breaker = self.get(name)
        if breaker is None:
            return True  # 没有熔断器 = 允许
        return breaker.is_call_permitted()

    def get_all_status(self) -> dict[str, Any]:
        """获取所有熔断器状态。"""
        return {
            name: breaker.get_status()
            for name, breaker in self._breakers.items()
        }

    def reset_all(self) -> None:
        """重置所有熔断器。"""
        for breaker in self._breakers.values():
            breaker.reset()


# ── 全局注册中心 ──

_global_registry: CircuitBreakerRegistry | None = None


def get_circuit_breaker_registry() -> CircuitBreakerRegistry:
    """获取全局熔断器注册中心。"""
    global _global_registry
    if _global_registry is None:
        _global_registry = CircuitBreakerRegistry()
    return _global_registry


def reset_circuit_breaker_registry() -> None:
    """重置全局注册中心（用于测试）。"""
    global _global_registry
    _global_registry = None