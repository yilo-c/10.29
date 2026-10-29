"""共享后台任务管理器。

原 _inbox_data 里的模块级 ``_pending_bg_tasks`` 被 inbox.* 与 info.* 跨域共享，
为避免各子模块各自维护状态，抽到此处集中管理。
"""

from __future__ import annotations

import asyncio
import logging

logger = logging.getLogger("siyi.backend.handlers.bg_tasks")

# 后台任务强引用集合（防止 asyncio.create_task 被 GC 回收）
_pending_bg_tasks: set[asyncio.Task] = set()


def spawn_bg_task(coro: asyncio.coroutines.Coroutine) -> asyncio.Task:
    """启动一个 fire-and-forget 后台任务，并保留强引用防止被 GC 回收。

    Returns:
        创建的 asyncio.Task 实例。
    """
    task = asyncio.create_task(coro)
    _pending_bg_tasks.add(task)
    task.add_done_callback(_pending_bg_tasks.discard)
    return task


def pending_bg_tasks() -> set[asyncio.Task]:
    """返回当前在跑的后台任务集合（只读视图）。"""
    return _pending_bg_tasks
