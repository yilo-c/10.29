# -*- coding: utf-8 -*-
"""Task Queue JSON-RPC handlers — P1.2 持久任务队列前端入口。

暴露 PersistentTaskQueue 的管理能力到前端 / 手机端：
    task_queue.enqueue          — 入队一条任务
    task_queue.list             — 列出任务（可按 queue_name / status 过滤）
    task_queue.get              — 获取单条任务详情
    task_queue.cancel           — 取消 pending/running 任务
    task_queue.stats            — 各状态计数
    task_queue.purge_completed  — 清理已完成的旧任务
    task_queue.dead_letter.list    — 列出死信任务
    task_queue.dead_letter.requeue — 把死信任务重新入队

任务执行由 TaskQueueWorker 在后端启动时自动拉起（见 backend.headless_main）。
前端只负责"投递"与"观察"，不直接执行任务。
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from backend.handlers._list_guard import clamp_limit

logger = logging.getLogger("siyi.backend.handlers.task_queue")


def _get_queue(context):
    """从 context 拿 PersistentTaskQueue 实例（由 headless_main 注入）。"""
    return getattr(context, "task_queue", None)


def register_task_queue(registry, context) -> None:
    """Register task_queue.* RPC methods."""

    async def task_queue_enqueue(
        capability_id: str = "",
        payload: "dict[str, Any] | None" = None,
        queue_name: str = "default",
        priority: int = 0,
        max_retries: int = 3,
        backoff_base: float = 1.0,
        backoff_cap: float = 300.0,
        **kwargs: Any,
    ) -> "dict[str, Any]":
        """入队一条持久任务。

        Args:
            capability_id: 要调用的 action_id（如 "info.brief" / "url_learning.learn"）
            payload: 调用参数 dict
            queue_name: 队列名（不同队列互不干扰，可并行消费）
            priority: 0=normal, 1=high（高优先级先被取出）
            max_retries: 失败后最大重试次数（不含首次）。0 表示不重试。
            backoff_base: 退避基数（秒）。第 N 次失败后等待 min(base*2^(N-1), cap) 秒
            backoff_cap: 退避上限（秒）
        """
        capability_id = str(capability_id or "").strip()
        if not capability_id:
            return {"success": False, "error": "capability_id required"}
        q = _get_queue(context)
        if q is None:
            return {"success": False, "error": "task_queue service unavailable"}
        try:
            tid = await asyncio.to_thread(
                q.enqueue,
                capability_id=capability_id,
                payload=dict(payload or {}),
                queue_name=queue_name or "default",
                priority=int(priority),
                max_retries=int(max_retries),
                backoff_base=float(backoff_base),
                backoff_cap=float(backoff_cap),
            )
            return {"success": True, "task_id": tid}
        except Exception as exc:
            logger.warning("task_queue.enqueue failed: %s", exc)
            return {"success": False, "error": str(exc)}

    async def task_queue_list(
        queue_name: "str | None" = None,
        status: "str | None" = None,
        limit: int = 100,
        **kwargs: Any,
    ) -> "dict[str, Any]":
        """列出任务，按 created_at 倒序。"""
        q = _get_queue(context)
        if q is None:
            return {"success": False, "error": "task_queue service unavailable", "tasks": []}
        try:
            tasks = await asyncio.to_thread(
                q.list,
                queue_name=queue_name,
                status=status,
                limit=clamp_limit(limit, default=100),
            )
            return {
                "success": True,
                "tasks": [t.to_dict() for t in tasks],
                "total": len(tasks),
            }
        except Exception as exc:
            logger.warning("task_queue.list failed: %s", exc)
            return {"success": False, "error": str(exc), "tasks": []}

    async def task_queue_get(task_id: str = "", **kwargs: Any) -> "dict[str, Any]":
        """获取单条任务详情。"""
        if not task_id:
            return {"success": False, "error": "task_id required"}
        q = _get_queue(context)
        if q is None:
            return {"success": False, "error": "task_queue service unavailable"}
        try:
            rec = await asyncio.to_thread(q.get, task_id)
            if rec is None:
                return {"success": False, "error": "task not found"}
            return {"success": True, "task": rec.to_dict()}
        except Exception as exc:
            logger.warning("task_queue.get failed: %s", exc)
            return {"success": False, "error": str(exc)}

    async def task_queue_cancel(task_id: str = "", **kwargs: Any) -> "dict[str, Any]":
        """取消任务（仅 pending/running 可取消）。"""
        if not task_id:
            return {"success": False, "error": "task_id required"}
        q = _get_queue(context)
        if q is None:
            return {"success": False, "error": "task_queue service unavailable"}
        try:
            ok = await asyncio.to_thread(q.cancel, task_id)
            if not ok:
                return {
                    "success": False,
                    "error": "task not cancellable (status not in pending/running)",
                }
            return {"success": True}
        except Exception as exc:
            logger.warning("task_queue.cancel failed: %s", exc)
            return {"success": False, "error": str(exc)}

    async def task_queue_stats(
        queue_name: "str | None" = None,
        **kwargs: Any,
    ) -> "dict[str, Any]":
        """返回各状态计数。"""
        q = _get_queue(context)
        if q is None:
            return {"success": False, "error": "task_queue service unavailable"}
        try:
            stats = await asyncio.to_thread(q.stats, queue_name)
            return {"success": True, "stats": stats}
        except Exception as exc:
            logger.warning("task_queue.stats failed: %s", exc)
            return {"success": False, "error": str(exc)}

    async def task_queue_purge_completed(
        older_than_seconds: float = 86400.0,
        **kwargs: Any,
    ) -> "dict[str, Any]":
        """清理完成时间超过 older_than_seconds 的 completed 任务。"""
        q = _get_queue(context)
        if q is None:
            return {"success": False, "error": "task_queue service unavailable"}
        try:
            count = await asyncio.to_thread(q.purge_completed, older_than_seconds=float(older_than_seconds))
            return {"success": True, "purged": count}
        except Exception as exc:
            logger.warning("task_queue.purge_completed failed: %s", exc)
            return {"success": False, "error": str(exc)}

    async def task_queue_dead_letter_list(
        queue_name: "str | None" = None,
        limit: int = 50,
        **kwargs: Any,
    ) -> "dict[str, Any]":
        """列出死信任务。"""
        from services.task_queue import TaskStatus

        q = _get_queue(context)
        if q is None:
            return {"success": False, "error": "task_queue service unavailable", "tasks": []}
        try:
            tasks = await asyncio.to_thread(
                q.list,
                queue_name=queue_name,
                status=TaskStatus.DEAD_LETTER,
                limit=clamp_limit(limit, default=50),
            )
            return {
                "success": True,
                "tasks": [t.to_dict() for t in tasks],
                "total": len(tasks),
            }
        except Exception as exc:
            logger.warning("task_queue.dead_letter.list failed: %s", exc)
            return {"success": False, "error": str(exc), "tasks": []}

    async def task_queue_dead_letter_requeue(
        task_id: str = "",
        **kwargs: Any,
    ) -> "dict[str, Any]":
        """把死信任务重新置为 pending 立即可执行。"""
        if not task_id:
            return {"success": False, "error": "task_id required"}
        q = _get_queue(context)
        if q is None:
            return {"success": False, "error": "task_queue service unavailable"}
        try:
            ok = await asyncio.to_thread(q.requeue_dead_letter, task_id)
            if not ok:
                return {"success": False, "error": "task not in dead_letter status"}
            return {"success": True}
        except Exception as exc:
            logger.warning("task_queue.dead_letter.requeue failed: %s", exc)
            return {"success": False, "error": str(exc)}

    registry.register("task_queue.enqueue", task_queue_enqueue)
    registry.register("task_queue.list", task_queue_list)
    registry.register("task_queue.get", task_queue_get)
    registry.register("task_queue.cancel", task_queue_cancel)
    registry.register("task_queue.stats", task_queue_stats)
    registry.register("task_queue.purge_completed", task_queue_purge_completed)
    registry.register("task_queue.dead_letter.list", task_queue_dead_letter_list)
    registry.register("task_queue.dead_letter.requeue", task_queue_dead_letter_requeue)
