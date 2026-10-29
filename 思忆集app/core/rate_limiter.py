"""请求限流器。

提供：
- RateLimiter: 基于滑动窗口的限流器
- TokenBucketLimiter: 令牌桶限流器（支持突发）
- 装饰器：用于限制方法调用频率
"""

from __future__ import annotations

import asyncio
import functools
import logging
import threading
import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Callable, TypeVar

logger = logging.getLogger("siyi.core.rate_limiter")

F = TypeVar("F", bound=Callable[..., Any])


@dataclass
class RateLimitStats:
    """限流统计信息。"""
    total_requests: int = 0
    allowed_requests: int = 0
    rejected_requests: int = 0
    last_rejected_at: float = 0.0


class RateLimiter:
    """基于滑动窗口的限流器。
    
    在指定时间窗口内限制每个客户端的请求次数。
    
    用法：
        limiter = RateLimiter(max_requests=100, window=60.0)
        
        if limiter.allow("client_1"):
            # 处理请求
            pass
        else:
            # 返回 429
            pass
    """
    
    def __init__(
        self,
        max_requests: int = 100,
        window: float = 60.0,
        cleanup_interval: float = 300.0,
    ):
        """初始化限流器。
        
        Args:
            max_requests: 时间窗口内最大请求数
            window: 时间窗口（秒）
            cleanup_interval: 清理过期记录的间隔（秒）
        """
        self.max_requests = max_requests
        self.window = window
        self.cleanup_interval = cleanup_interval
        
        self._requests: dict[str, list[float]] = defaultdict(list)
        self._stats: dict[str, RateLimitStats] = defaultdict(RateLimitStats)
        self._last_cleanup = time.monotonic()
        # 使用 threading.Lock 保护共享数据，确保 async allow() 和 sync allow_sync() 线程安全。
        # 临界区内无 await，阻塞时间极短（微秒级），不会阻塞事件循环。
        self._lock = threading.Lock()

    async def allow(self, client_id: str) -> bool:
        """检查是否允许请求。

        Args:
            client_id: 客户端标识符

        Returns:
            True 表示允许，False 表示被限流
        """
        with self._lock:
            now = time.monotonic()
            stats = self._stats[client_id]
            stats.total_requests += 1
            
            # 定期清理过期记录
            if now - self._last_cleanup > self.cleanup_interval:
                self._cleanup(now)
                self._last_cleanup = now
            
            # 清理该客户端的过期记录
            window_start = now - self.window
            self._requests[client_id] = [
                t for t in self._requests[client_id]
                if t > window_start
            ]
            
            # 检查是否超限
            if len(self._requests[client_id]) >= self.max_requests:
                stats.rejected_requests += 1
                stats.last_rejected_at = now
                logger.warning(
                    "Rate limit exceeded for client %s: %d/%d in %.0fs",
                    client_id,
                    len(self._requests[client_id]),
                    self.max_requests,
                    self.window,
                )
                return False
            
            # 记录请求
            self._requests[client_id].append(now)
            stats.allowed_requests += 1
            return True
    
    def allow_sync(self, client_id: str) -> bool:
        """同步版本的 allow()，用于非异步上下文。"""
        with self._lock:
            now = time.monotonic()
            stats = self._stats[client_id]
            stats.total_requests += 1

            # 清理该客户端的过期记录
            window_start = now - self.window
            self._requests[client_id] = [
                t for t in self._requests[client_id]
                if t > window_start
            ]

            # 检查是否超限
            if len(self._requests[client_id]) >= self.max_requests:
                stats.rejected_requests += 1
                stats.last_rejected_at = now
                return False

            # 记录请求
            self._requests[client_id].append(now)
            stats.allowed_requests += 1
            return True
    
    def get_stats(self, client_id: str | None = None) -> dict[str, RateLimitStats] | RateLimitStats:
        """获取限流统计信息。"""
        if client_id:
            return self._stats.get(client_id, RateLimitStats())
        return dict(self._stats)
    
    def get_remaining(self, client_id: str) -> int:
        """获取客户端剩余可用请求数。"""
        now = time.monotonic()
        window_start = now - self.window
        current = len([
            t for t in self._requests.get(client_id, [])
            if t > window_start
        ])
        return max(0, self.max_requests - current)
    
    def reset(self, client_id: str | None = None) -> None:
        """重置限流记录。"""
        if client_id:
            self._requests.pop(client_id, None)
            self._stats.pop(client_id, None)
        else:
            self._requests.clear()
            self._stats.clear()
    
    def _cleanup(self, now: float) -> None:
        """清理所有客户端的过期记录。"""
        window_start = now - self.window
        empty_clients = []
        
        for client_id, timestamps in self._requests.items():
            self._requests[client_id] = [
                t for t in timestamps if t > window_start
            ]
            if not self._requests[client_id]:
                empty_clients.append(client_id)
        
        for client_id in empty_clients:
            del self._requests[client_id]
            # P1 修复：同步清理 _stats 中对应客户端的条目，防止 _stats 无限增长
            self._stats.pop(client_id, None)


class TokenBucketLimiter:
    """令牌桶限流器。
    
    支持突发流量：桶满时可以一次性处理多个请求，
    然后按固定速率补充令牌。
    
    用法：
        limiter = TokenBucketLimiter(rate=10, capacity=20)
        
        if limiter.allow("client_1"):
            # 处理请求
            pass
    """
    
    def __init__(
        self,
        rate: float = 10.0,
        capacity: int = 20,
    ):
        """初始化令牌桶限流器。
        
        Args:
            rate: 令牌补充速率（每秒）
            capacity: 桶容量（最大突发数）
        """
        self.rate = rate
        self.capacity = capacity
        
        self._buckets: dict[str, dict[str, float]] = {}
        self._stats: dict[str, RateLimitStats] = defaultdict(RateLimitStats)
        self._lock = threading.Lock()

    async def allow(self, client_id: str, tokens: int = 1) -> bool:
        """检查是否允许请求。

        Args:
            client_id: 客户端标识符
            tokens: 本次请求消耗的令牌数

        Returns:
            True 表示允许，False 表示被限流
        """
        with self._lock:
            now = time.monotonic()
            stats = self._stats[client_id]
            stats.total_requests += 1
            
            # 获取或创建桶
            if client_id not in self._buckets:
                self._buckets[client_id] = {
                    "tokens": float(self.capacity),
                    "last_refill": now,
                }
            
            bucket = self._buckets[client_id]
            
            # 补充令牌
            elapsed = now - bucket["last_refill"]
            bucket["tokens"] = min(
                self.capacity,
                bucket["tokens"] + elapsed * self.rate,
            )
            bucket["last_refill"] = now
            
            # 检查是否有足够令牌
            if bucket["tokens"] >= tokens:
                bucket["tokens"] -= tokens
                stats.allowed_requests += 1
                return True
            
            # 令牌不足
            stats.rejected_requests += 1
            stats.last_rejected_at = now
            logger.warning(
                "Token bucket empty for client %s: need %d, have %.1f",
                client_id,
                tokens,
                bucket["tokens"],
            )
            return False
    
    def get_tokens(self, client_id: str) -> float:
        """获取客户端当前令牌数。"""
        if client_id not in self._buckets:
            return float(self.capacity)
        
        bucket = self._buckets[client_id]
        now = time.monotonic()
        elapsed = now - bucket["last_refill"]
        return min(
            self.capacity,
            bucket["tokens"] + elapsed * self.rate,
        )
    
    def get_stats(self, client_id: str | None = None) -> dict[str, RateLimitStats] | RateLimitStats:
        """获取限流统计信息。"""
        if client_id:
            return self._stats.get(client_id, RateLimitStats())
        return dict(self._stats)
    
    def reset(self, client_id: str | None = None) -> None:
        """重置限流记录。"""
        if client_id:
            self._buckets.pop(client_id, None)
            self._stats.pop(client_id, None)
        else:
            self._buckets.clear()
            self._stats.clear()


def rate_limit(
    limiter: RateLimiter | TokenBucketLimiter,
    client_id_getter: Callable[..., str] | None = None,
    on_rejected: Callable[..., Any] | None = None,
) -> Callable[[F], F]:
    """限流装饰器。
    
    用法：
        limiter = RateLimiter(max_requests=10, window=60.0)
        
        @rate_limit(limiter, client_id_getter=lambda ctx: ctx["client_id"])
        async def handle_request(ctx):
            ...
    """
    def decorator(func: F) -> F:
        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            # 获取客户端 ID
            if client_id_getter:
                client_id = client_id_getter(*args, **kwargs)
            else:
                client_id = "default"
            
            # 检查限流
            if not await limiter.allow(client_id):
                if on_rejected:
                    return await on_rejected(*args, **kwargs)
                from core.errors import RateLimitedError
                raise RateLimitedError(
                    details={
                        "retry_after": getattr(limiter, "window", 60.0),
                        "client_id": client_id,
                    }
                )
            
            return await func(*args, **kwargs)
        
        return wrapper  # type: ignore[return-value]
    
    return decorator


# 全局限流器实例（可在应用中共享）
_global_limiters: dict[str, RateLimiter | TokenBucketLimiter] = {}


def get_limiter(
    name: str,
    limiter_type: str = "sliding_window",
    **kwargs,
) -> RateLimiter | TokenBucketLimiter:
    """获取或创建全局限流器。
    
    Args:
        name: 限流器名称
        limiter_type: "sliding_window" 或 "token_bucket"
        **kwargs: 传递给限流器构造函数的参数
        
    Returns:
        限流器实例
    """
    if name not in _global_limiters:
        if limiter_type == "token_bucket":
            _global_limiters[name] = TokenBucketLimiter(**kwargs)
        else:
            _global_limiters[name] = RateLimiter(**kwargs)
    return _global_limiters[name]


def reset_all_limiters() -> None:
    """重置所有限流器。"""
    for limiter in _global_limiters.values():
        limiter.reset()
    _global_limiters.clear()