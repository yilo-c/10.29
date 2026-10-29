"""读写锁 — 读操作并发，写操作独占。

用于优化高并发场景下的数据存储访问：
- 读操作可以并发执行
- 写操作独占执行
- 支持锁超时和重试
"""
from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import AsyncIterator

logger = logging.getLogger("siyi.core.rwlock")


@dataclass
class RWLockStats:
    """读写锁统计信息"""
    active_readers: int = 0
    waiting_readers: int = 0
    waiting_writers: int = 0
    is_write_locked: bool = False
    total_read_acquires: int = 0
    total_write_acquires: int = 0
    total_read_wait_time: float = 0.0
    total_write_wait_time: float = 0.0


class ReadWriteLock:
    """异步读写锁。
    
    使用示例：
        lock = ReadWriteLock()
        
        # 读操作（可并发）
        async with lock.read_lock():
            data = await read_data()
        
        # 写操作（独占）
        async with lock.write_lock():
            await write_data(data)
    """
    
    def __init__(self, timeout: float = 30.0):
        """初始化读写锁。

        Args:
            timeout: 获取锁的超时时间（秒）
        """
        self._timeout = timeout
        self._readers = 0
        self._writer = False
        self._waiting_writers = 0
        # 单一 Condition 保护所有状态，避免两个 Condition 之间竞态
        self._condition = asyncio.Condition(asyncio.Lock())

        # 统计信息
        self._stats = RWLockStats()

    @asynccontextmanager
    async def read_lock(self) -> AsyncIterator[None]:
        """获取读锁（可并发）。

        Raises:
            TimeoutError: 获取锁超时
        """
        import time
        start_time = time.monotonic()

        async with self._condition:
            self._stats.waiting_readers += 1
            try:
                # writer-preference：有等待的写者时，新读者排队
                while self._writer or self._waiting_writers > 0:
                    remaining = self._timeout - (time.monotonic() - start_time)
                    if remaining <= 0:
                        raise TimeoutError("读锁获取超时")
                    try:
                        await asyncio.wait_for(
                            self._condition.wait(),
                            timeout=remaining,
                        )
                    except asyncio.TimeoutError:
                        raise TimeoutError("读锁获取超时")

                self._readers += 1
                self._stats.active_readers += 1
                self._stats.total_read_acquires += 1
                self._stats.total_read_wait_time += time.monotonic() - start_time
            finally:
                self._stats.waiting_readers -= 1

        try:
            yield
        finally:
            async with self._condition:
                self._readers -= 1
                self._stats.active_readers -= 1
                if self._readers == 0:
                    self._condition.notify_all()

    @asynccontextmanager
    async def write_lock(self) -> AsyncIterator[None]:
        """获取写锁（独占）。

        Raises:
            TimeoutError: 获取锁超时
        """
        import time
        start_time = time.monotonic()

        async with self._condition:
            self._stats.waiting_writers += 1
            try:
                while self._writer or self._readers > 0:
                    remaining = self._timeout - (time.monotonic() - start_time)
                    if remaining <= 0:
                        raise TimeoutError("写锁获取超时")
                    try:
                        await asyncio.wait_for(
                            self._condition.wait(),
                            timeout=remaining,
                        )
                    except asyncio.TimeoutError:
                        raise TimeoutError("写锁获取超时")

                self._writer = True
                self._stats.is_write_locked = True
                self._stats.total_write_acquires += 1
                self._stats.total_write_wait_time += time.monotonic() - start_time
            finally:
                self._stats.waiting_writers -= 1

        try:
            yield
        finally:
            async with self._condition:
                self._writer = False
                self._stats.is_write_locked = False
                self._condition.notify_all()
    
    @property
    def stats(self) -> RWLockStats:
        """获取锁统计信息"""
        return RWLockStats(
            active_readers=self._stats.active_readers,
            waiting_readers=self._stats.waiting_readers,
            waiting_writers=self._stats.waiting_writers,
            is_write_locked=self._stats.is_write_locked,
            total_read_acquires=self._stats.total_read_acquires,
            total_write_acquires=self._stats.total_write_acquires,
            total_read_wait_time=self._stats.total_read_wait_time,
            total_write_wait_time=self._stats.total_write_wait_time,
        )
    
    def reset_stats(self) -> None:
        """重置统计信息"""
        self._stats = RWLockStats()


class NamedLockRegistry:
    """命名锁注册表 — 管理多个命名锁。
    
    使用示例：
        registry = NamedLockRegistry()
        
        # 获取特定资源的锁
        async with registry.get_lock("note_123").write_lock():
            await update_note("note_123", data)
    """
    
    def __init__(self, default_timeout: float = 30.0):
        self._locks: dict[str, ReadWriteLock] = {}
        self._default_timeout = default_timeout
        self._global_lock = asyncio.Lock()
    
    async def get_lock(self, name: str, timeout: float | None = None) -> ReadWriteLock:
        """获取或创建命名锁。
        
        Args:
            name: 锁名称
            timeout: 超时时间（秒），None 使用默认值
        
        Returns:
            ReadWriteLock: 读写锁实例
        """
        if name in self._locks:
            return self._locks[name]
        
        async with self._global_lock:
            if name not in self._locks:
                self._locks[name] = ReadWriteLock(timeout or self._default_timeout)
            return self._locks[name]
    
    def remove_lock(self, name: str) -> None:
        """移除命名锁"""
        self._locks.pop(name, None)
    
    def clear(self) -> None:
        """清空所有锁"""
        self._locks.clear()
    
    @property
    def lock_count(self) -> int:
        """获取锁数量"""
        return len(self._locks)
    
    def get_all_stats(self) -> dict[str, RWLockStats]:
        """获取所有锁的统计信息"""
        return {name: lock.stats for name, lock in self._locks.items()}


# 便捷函数
@asynccontextmanager
async def read_lock(lock: ReadWriteLock) -> AsyncIterator[None]:
    """读锁上下文管理器"""
    async with lock.read_lock():
        yield


@asynccontextmanager
async def write_lock(lock: ReadWriteLock) -> AsyncIterator[None]:
    """写锁上下文管理器"""
    async with lock.write_lock():
        yield