"""后台任务管理器。

提供：
- 后台任务跟踪
- 优雅关闭支持
- 失败任务日志记录
- 任务统计
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Callable, Coroutine

logger = logging.getLogger("siyi.core.task_manager")


class BackgroundTaskManager:
    """后台任务管理器。
    
    功能：
    - 跟踪所有后台任务
    - 支持优雅关闭（等待任务完成或超时后取消）
    - 记录失败任务
    - 提供任务统计
    """
    
    def __init__(self, shutdown_timeout: float = 5.0):
        self._tasks: set[asyncio.Task] = set()
        self._shutdown_timeout = shutdown_timeout
        self._is_shutting_down = False
        self._failed_count = 0
        self._completed_count = 0
    
    @property
    def active_count(self) -> int:
        """当前活跃任务数。"""
        return len(self._tasks)
    
    @property
    def stats(self) -> dict[str, int]:
        """任务统计。"""
        return {
            "active": self.active_count,
            "completed": self._completed_count,
            "failed": self._failed_count,
        }
    
    def fire_and_forget(
        self,
        coro: Coroutine[Any, Any, Any],
        name: str | None = None,
    ) -> asyncio.Task | None:
        """创建后台任务（不等待完成）。
        
        Args:
            coro: 要执行的协程
            name: 任务名称（用于日志）
            
        Returns:
            创建的 Task 对象，如果正在关闭则返回 None
        """
        if self._is_shutting_down:
            logger.warning("Cannot create task '%s': manager is shutting down", name)
            return None
        
        task = asyncio.create_task(coro, name=name or "unnamed_task")
        self._tasks.add(task)
        task.add_done_callback(self._on_task_done)
        return task
    
    def _on_task_done(self, task: asyncio.Task) -> None:
        """任务完成回调。"""
        self._tasks.discard(task)
        
        if task.cancelled():
            logger.debug("Task '%s' was cancelled", task.get_name())
            return
        
        exc = task.exception()
        if exc is not None:
            self._failed_count += 1
            logger.warning(
                "Background task '%s' failed: %s: %s",
                task.get_name(),
                type(exc).__name__,
                exc,
            )
        else:
            self._completed_count += 1
    
    async def shutdown(self, timeout: float | None = None) -> dict[str, int]:
        """优雅关闭所有后台任务。
        
        Args:
            timeout: 等待超时（秒），默认使用构造时的值
            
        Returns:
            关闭统计
        """
        if self._is_shutting_down:
            return self.stats
        
        self._is_shutting_down = True
        timeout = timeout or self._shutdown_timeout
        
        if not self._tasks:
            logger.info("No active tasks to shutdown")
            return self.stats
        
        logger.info(
            "Shutting down %d active tasks (timeout=%.1fs)",
            len(self._tasks),
            timeout,
        )
        
        # 等待所有任务完成
        done, pending = await asyncio.wait(
            self._tasks,
            timeout=timeout,
            return_when=asyncio.ALL_COMPLETED,
        )
        
        # 取消未完成的任务
        cancelled = 0
        for task in pending:
            task.cancel()
            cancelled += 1
        
        if cancelled:
            # 等待取消完成
            await asyncio.wait(pending, timeout=1.0)
            logger.info("Cancelled %d pending tasks", cancelled)
        
        logger.info(
            "Task manager shutdown complete: %d completed, %d failed",
            self._completed_count,
            self._failed_count,
        )
        
        return self.stats
    
    async def wait_all(self, timeout: float | None = None) -> bool:
        """等待所有任务完成。
        
        Args:
            timeout: 等待超时（秒）
            
        Returns:
            True 如果所有任务完成，False 如果超时
        """
        if not self._tasks:
            return True
        
        try:
            await asyncio.wait(self._tasks, timeout=timeout)
            return len(self._tasks) == 0
        except asyncio.TimeoutError:
            return False


# 全局任务管理器实例
_task_manager: BackgroundTaskManager | None = None


def get_task_manager() -> BackgroundTaskManager:
    """获取全局任务管理器实例。"""
    global _task_manager
    if _task_manager is None:
        _task_manager = BackgroundTaskManager()
    return _task_manager


async def shutdown_task_manager(timeout: float = 5.0) -> dict[str, int]:
    """关闭全局任务管理器。"""
    global _task_manager
    if _task_manager is not None:
        stats = await _task_manager.shutdown(timeout)
        _task_manager = None
        return stats
    return {"active": 0, "completed": 0, "failed": 0}