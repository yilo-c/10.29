# -*- coding: utf-8 -*-
"""进程生命周期管理 RPC 方法。

暴露由 GenericToolBridge 注册的动态工具进程状态，支持：
    process.list      — 列出所有被追踪的进程
    process.status    — 获取单个进程详情与最近输出
    process.output    — 获取指定进程的最近 stdout/stderr
    process.terminate — 终止指定进程
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("siyi.backend.handlers.process")


def register_process(registry, context) -> None:
    """注册进程生命周期管理相关的 JSON-RPC 方法。"""

    def _get_lifecycle_manager():
        """获取 ProcessLifecycleManager 实例。"""
        manager = getattr(context, "process_lifecycle_manager", None)
        if manager is not None:
            return manager

        autonomy = getattr(context, "autonomous_assistant_manager", None)
        if autonomy is not None:
            router = getattr(autonomy, "router", None)
            if router is not None:
                return getattr(router, "process_lifecycle_manager", None)
        return None

    async def process_list(alive_only: bool = False, **kwargs: Any) -> dict[str, Any]:
        """列出所有被追踪的进程。"""
        manager = _get_lifecycle_manager()
        if manager is None:
            return {"success": False, "error": "process lifecycle manager not available"}

        processes = manager.list_processes(alive_only=bool(alive_only))
        return {
            "success": True,
            "alive_only": bool(alive_only),
            "count": len(processes),
            "processes": processes,
        }

    async def process_status(process_id: str = "", **kwargs: Any) -> dict[str, Any]:
        """获取单个进程的详细状态与最近输出。"""
        process_id = str(process_id or "").strip()
        if not process_id:
            return {"success": False, "error": "process_id required"}

        manager = _get_lifecycle_manager()
        if manager is None:
            return {"success": False, "error": "process lifecycle manager not available"}

        status = manager.get_status(process_id)
        if status is None:
            return {"success": False, "error": "process not found", "process_id": process_id}
        return {"success": True, "process": status}

    async def process_output(
        process_id: str = "",
        stream: str = "stdout",
        limit: int = 50,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """获取指定进程的最近输出行。"""
        process_id = str(process_id or "").strip()
        if not process_id:
            return {"success": False, "error": "process_id required"}
        if stream not in ("stdout", "stderr"):
            return {"success": False, "error": "stream must be stdout or stderr"}
        manager = _get_lifecycle_manager()
        if manager is None:
            return {"success": False, "error": "process lifecycle manager not available"}

        lines = manager.get_recent_output(process_id, stream=stream, limit=int(limit))
        return {
            "success": True,
            "process_id": process_id,
            "stream": stream,
            "limit": int(limit),
            "lines": lines,
        }

    async def process_terminate(process_id: str = "", **kwargs: Any) -> dict[str, Any]:
        """终止指定进程。"""
        process_id = str(process_id or "").strip()
        if not process_id:
            return {"success": False, "error": "process_id required"}

        manager = _get_lifecycle_manager()
        if manager is None:
            return {"success": False, "error": "process lifecycle manager not available"}

        result = manager.terminate(process_id)
        return result

    registry.register("process.list", process_list)
    registry.register("process.status", process_status)
    registry.register("process.output", process_output)
    registry.register("process.terminate", process_terminate)
    logger.info("Process lifecycle RPC methods registered")
