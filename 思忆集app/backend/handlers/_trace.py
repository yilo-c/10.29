"""Trace / 断点续跑 JSON-RPC handlers（P1-1 + P1-2）。

方法：
- ``trace.list``               — 任务运行列表（可按类型/状态过滤）
- ``trace.get``                — 单任务运行详情（含步骤）
- ``trace.stats``              — 近 N 天运行统计（成功率/类型/状态分布）
- ``assistant.unfinished_tasks`` — 列出可续跑的未完成任务（checkpoint）
- ``assistant.resume_task``    — 从 checkpoint 续跑指定任务（**M5 弃用**：assistant/ 退役后由内核 checkpoint 事件流承接）
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from backend.handlers._list_guard import clamp_limit

logger = logging.getLogger("siyi.backend.handlers.trace")


def _get_recorder(context) -> Any | None:
    # P1-4 示范：经领域门面取依赖（chat 域含观测）
    return getattr(getattr(context, "chat", context), "trace_recorder", None)


def _get_autonomy(context) -> Any | None:
    return getattr(getattr(context, "automation", context), "autonomous_assistant_manager", None)


def register_trace(registry, context) -> None:
    """注册执行观测与断点续跑相关 JSON-RPC 方法。"""

    # ── trace.list ──
    async def trace_list(limit: int = 50, task_type: str = "", status: str = "", **kwargs: Any) -> dict[str, Any]:
        recorder = _get_recorder(context)
        if recorder is None:
            return {"success": False, "error": "trace recorder not initialized", "runs": []}
        try:
            runs = await asyncio.to_thread(
                recorder.list_runs,
                # 帧守卫（2026-10-15）：SQL LIMIT 负值在 SQLite = 无上限，clamp 归一
                limit=clamp_limit(limit, default=50),
                task_type=str(task_type or ""),
                status=str(status or ""),
            )
        except Exception as exc:
            logger.warning("trace.list failed: %s", exc)
            return {"success": False, "error": str(exc), "runs": []}
        return {"success": True, "runs": runs, "count": len(runs)}

    registry.register("trace.list", trace_list)

    # ── trace.get ──
    async def trace_get(task_id: str = "", **kwargs: Any) -> dict[str, Any]:
        recorder = _get_recorder(context)
        if recorder is None:
            return {"success": False, "error": "trace recorder not initialized"}
        if not task_id:
            return {"success": False, "error": "missing task_id"}
        try:
            run = await asyncio.to_thread(recorder.get_run, str(task_id))
        except Exception as exc:
            logger.warning("trace.get failed: %s", exc)
            return {"success": False, "error": str(exc)}
        if run is None:
            return {"success": False, "error": f"run {task_id} not found"}
        return {"success": True, "run": run}

    registry.register("trace.get", trace_get)

    # ── trace.stats ──
    async def trace_stats(days: int = 7, **kwargs: Any) -> dict[str, Any]:
        recorder = _get_recorder(context)
        if recorder is None:
            return {"success": False, "error": "trace recorder not initialized"}
        try:
            stats = await asyncio.to_thread(recorder.get_stats, days=days)
        except Exception as exc:
            logger.warning("trace.stats failed: %s", exc)
            return {"success": False, "error": str(exc)}
        return {"success": True, "stats": stats}

    registry.register("trace.stats", trace_stats)

    # ── assistant.unfinished_tasks（P1-1：续跑可见） ──
    async def assistant_unfinished_tasks(**kwargs: Any) -> dict[str, Any]:
        """列出 checkpoint 中可续跑的未完成任务。"""
        autonomy = _get_autonomy(context)
        if autonomy is None:
            return {"success": False, "error": "assistant manager unavailable", "tasks": []}
        try:
            task_manager = getattr(autonomy, "task_manager", None)
            if task_manager is not None and hasattr(task_manager, "get_unfinished_tasks_info"):
                tasks = await asyncio.to_thread(task_manager.get_unfinished_tasks_info)
            else:
                tasks = await asyncio.to_thread(autonomy.scan_unfinished_tasks)
        except Exception as exc:
            logger.warning("assistant.unfinished_tasks failed: %s", exc)
            return {"success": False, "error": str(exc), "tasks": []}
        return {"success": True, "tasks": tasks, "count": len(tasks)}

    registry.register("assistant.unfinished_tasks", assistant_unfinished_tasks)

    # ── assistant.resume_task（P1-1：断点续跑） ──
    async def assistant_resume_task(task_id: str = "", **kwargs: Any) -> dict[str, Any]:
        """从最后 checkpoint 续跑指定任务。

        .. deprecated::
            J 项（2026-08-20）：``assistant/`` 已退役，butler 兜底大脑的断点续跑随之
            停用。续跑能力由 ``chat.resume``（M2，内核 checkpoint 事件流）承接。
            本方法不再调用 ``autonomy.recover_task``，直接返回 deprecated 错误，
            引导前端切换到 ``chat.resume``。
        """
        logger.warning("assistant.resume_task 已退役（J 项）：请改用 chat.resume（M2 内核 checkpoint 续跑）")
        return {
            "success": False,
            "reason": "assistant_resume_retired",
            "hint": "use chat.resume",
            "task_id": str(task_id or ""),
        }

    registry.register("assistant.resume_task", assistant_resume_task)
