# -*- coding: utf-8 -*-
"""定时任务管理 JSON-RPC 方法。

暴露 CronService 的管理能力到前端：
    cron.list    — 列出定时任务
    cron.add     — 添加定时任务
    cron.remove  — 删除定时任务
    cron.toggle  — 启用/禁用任务
    cron.run_now — 立即执行一次任务
    cron.status  — 获取调度器状态
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from backend.handlers._list_guard import clamp_limit

logger = logging.getLogger("siyi.backend.handlers.cron")


def register_cron(registry, context) -> None:
    """Register cron-related JSON-RPC methods."""

    def _cron_service():
        return getattr(context, "cron_service", None)

    async def cron_list(include_disabled: bool = False, **kwargs: Any) -> "dict[str, Any]":
        """列出所有定时任务。"""
        cron = _cron_service()
        if cron is None:
            return {"success": False, "error": "cron service unavailable", "jobs": []}
        try:
            jobs = await asyncio.to_thread(cron.list_jobs, bool(include_disabled))
            return {"success": True, "jobs": jobs, "total": len(jobs)}
        except Exception as exc:
            logger.warning("cron.list failed: %s", exc)
            return {"success": False, "error": str(exc), "jobs": []}

    async def cron_add(
        name: str = "",
        action_id: str = "",
        payload: "dict[str, Any] | None" = None,
        schedule: str = "",
        run_at: str = "",
        repeat: str = "",
        max_retries: int = 0,
        retry_backoff: int = 60,
        run_every_days: int = 0,
        **kwargs: Any,
    ) -> "dict[str, Any]":
        """添加一个定时任务。

        Args:
            name: 任务显示名称
            action_id: 要执行的动作 ID（如 "tool.zcode.open"）
            payload: 动作参数
            schedule: cron 表达式（repeat=cron 时必填，5 字段格式：分 时 日 月 周）
            run_at: 一次性执行时间或 daily/weekly/monthly 的执行时刻（HH:MM）
            repeat: 重复模式：none/daily/weekly/monthly/interval/cron
            run_every_days: interval 模式下的间隔天数（每 N 天执行一次）
            max_retries: 失败时最大重试次数
            retry_backoff: 重试基准退避秒数（指数退避：backoff * 2^(attempt-1)，上限 3600s）
        """
        name = str(name or "").strip()
        action_id = str(action_id or "").strip()
        if not name:
            return {"success": False, "error": "name required"}
        if not action_id:
            return {"success": False, "error": "action_id required"}
        cron = _cron_service()
        if cron is None:
            return {"success": False, "error": "cron service unavailable"}
        try:
            job = await asyncio.to_thread(
                cron.add_job,
                name,
                action_id,
                dict(payload or {}),
                schedule,
                run_at,
                repeat,
                int(max_retries or 0),
                int(retry_backoff or 60),
                int(run_every_days or 0),
            )
            return {"success": True, "job": job}
        except Exception as exc:
            logger.warning("cron.add failed: %s", exc)
            return {"success": False, "error": str(exc)}

    async def cron_remove(job_id: str = "", **kwargs: Any) -> "dict[str, Any]":
        """删除指定定时任务。"""
        job_id = str(job_id or "").strip()
        if not job_id:
            return {"success": False, "error": "job_id required"}
        cron = _cron_service()
        if cron is None:
            return {"success": False, "error": "cron service unavailable"}
        try:
            removed = await asyncio.to_thread(cron.remove_job, job_id)
            return {"success": removed, "job_id": job_id}
        except Exception as exc:
            logger.warning("cron.remove failed: %s", exc)
            return {"success": False, "error": str(exc)}

    async def cron_toggle(
        job_id: str = "",
        enabled: bool = True,
        **kwargs: Any,
    ) -> "dict[str, Any]":
        """启用或禁用指定定时任务。"""
        job_id = str(job_id or "").strip()
        if not job_id:
            return {"success": False, "error": "job_id required"}
        cron = _cron_service()
        if cron is None:
            return {"success": False, "error": "cron service unavailable"}
        try:
            toggled = await asyncio.to_thread(cron.toggle_job, job_id, bool(enabled))
            return {"success": toggled, "job_id": job_id, "enabled": bool(enabled)}
        except Exception as exc:
            logger.warning("cron.toggle failed: %s", exc)
            return {"success": False, "error": str(exc)}

    async def cron_update(
        job_id: str = "",
        name: "str | None" = None,
        action_id: "str | None" = None,
        payload: "dict[str, Any] | None" = None,
        schedule: "str | None" = None,
        run_at: "str | None" = None,
        repeat: "str | None" = None,
        max_retries: "int | None" = None,
        retry_backoff: "int | None" = None,
        run_every_days: "int | None" = None,
        **kwargs: Any,
    ) -> "dict[str, Any]":
        """更新指定定时任务。"""
        job_id = str(job_id or "").strip()
        if not job_id:
            return {"success": False, "error": "job_id required"}
        cron = _cron_service()
        if cron is None:
            return {"success": False, "error": "cron service unavailable"}
        try:
            job = await asyncio.to_thread(
                cron.update_job,
                job_id,
                name,
                action_id,
                dict(payload) if payload is not None else None,
                schedule,
                run_at,
                repeat,
                int(max_retries) if max_retries is not None else None,
                int(retry_backoff) if retry_backoff is not None else None,
                int(run_every_days) if run_every_days is not None else None,
            )
            if job is None:
                return {"success": False, "error": "job not found"}
            return {"success": True, "job": job}
        except Exception as exc:
            logger.warning("cron.update failed: %s", exc)
            return {"success": False, "error": str(exc)}

    async def cron_run_now(job_id: str = "", **kwargs: Any) -> "dict[str, Any]":
        """立即执行一次指定定时任务（不影响原有调度）。

        N-A（2026-09-17）杜绝假成功：旧实现调 cron._run_job——任务不存在或
        已禁用时静默 return，handler 仍恒报 success:true（生产实测
        {"job_id":"nonexistent"} → {"success": true}）。现改调如实回传的
        cron.run_job_now，并以 PRIORITY_MANUAL_RUN(+1) 入队：手动「立即执行」
        插队到冷启动补跑/周期任务之前，不再被分钟级网络任务队头阻塞（J3 根因）。
        dispatched 仅表示成功入队（真实结果由 AC-2 终态回写 history）；
        直跑路径带回真实执行成败。
        """
        job_id = str(job_id or "").strip()
        if not job_id:
            return {"success": False, "error": "job_id required"}
        cron = _cron_service()
        if cron is None:
            return {"success": False, "error": "cron service unavailable"}
        try:
            from services.cron_service import PRIORITY_MANUAL_RUN

            outcome = await asyncio.to_thread(cron.run_job_now, job_id, priority=PRIORITY_MANUAL_RUN)
            outcome = dict(outcome or {})
            if not outcome.get("triggered"):
                return {
                    "success": False,
                    "job_id": job_id,
                    "error": outcome.get("error") or "job not found or disabled",
                    "reason": outcome.get("reason") or "not_triggered",
                }
            response: dict[str, Any] = {
                "success": bool(outcome.get("success", True)),
                "job_id": job_id,
                "mode": outcome.get("mode") or "",
            }
            if not response["success"]:
                response["error"] = outcome.get("error") or "job execution failed"
            return response
        except Exception as exc:
            logger.warning("cron.run_now failed: %s", exc)
            return {"success": False, "error": str(exc)}

    async def cron_status(**kwargs: Any) -> "dict[str, Any]":
        """获取定时任务调度器状态。"""
        cron = _cron_service()
        if cron is None:
            return {"success": False, "error": "cron service unavailable"}
        try:
            status = await asyncio.to_thread(cron.get_status_snapshot)
            return {"success": True, "status": status}
        except Exception as exc:
            logger.warning("cron.status failed: %s", exc)
            return {"success": False, "error": str(exc)}

    async def cron_history(
        job_id: str = "",
        limit: int = 20,
        **kwargs: Any,
    ) -> "dict[str, Any]":
        """获取指定定时任务的执行历史。"""
        job_id = str(job_id or "").strip()
        if not job_id:
            return {"success": False, "error": "job_id required"}
        cron = _cron_service()
        if cron is None:
            return {"success": False, "error": "cron service unavailable"}
        try:
            history = await asyncio.to_thread(cron.get_job_history, job_id, clamp_limit(limit, default=20))
            return {"success": True, "job_id": job_id, "history": history}
        except Exception as exc:
            logger.warning("cron.history failed: %s", exc)
            return {"success": False, "error": str(exc)}

    async def cron_toggle_batch(
        job_ids: "list[str] | None" = None,
        enabled: bool = False,
        **kwargs: Any,
    ) -> "dict[str, Any]":
        """U2（S3-5）：批量启用/禁用定时任务（一键静音自主任务的后端原语）。

        Args:
            job_ids: 要切换的 job id 列表（由前端按分类挑选）
            enabled: 目标状态（false=静音/禁用，true=恢复）
        """
        if not job_ids or not isinstance(job_ids, list):
            return {"success": False, "error": "job_ids list required"}
        cron = _cron_service()
        if cron is None:
            return {"success": False, "error": "cron service unavailable"}
        try:
            outcome = await asyncio.to_thread(cron.toggle_jobs, [str(x) for x in job_ids], bool(enabled))
            changed = outcome.get("changed") or []
            return {
                "success": True,
                "changed_count": len(changed),
                "changed": changed,
                "not_found": outcome.get("not_found") or [],
                "enabled": bool(enabled),
            }
        except Exception as exc:
            logger.warning("cron.toggle_batch failed: %s", exc)
            return {"success": False, "error": str(exc)}

    registry.register("cron.list", cron_list)
    registry.register("cron.add", cron_add)
    registry.register("cron.remove", cron_remove)
    registry.register("cron.toggle", cron_toggle)
    # S2-1 棘轮：新增注册方法必须带参数 schema。
    registry.register_with_schema(
        "cron.toggle_batch",
        cron_toggle_batch,
        schema={
            "type": "object",
            "properties": {
                "job_ids": {"type": "array", "items": {"type": "string"}},
                "enabled": {"type": "boolean"},
            },
        },
    )
    registry.register("cron.update", cron_update)
    registry.register("cron.run_now", cron_run_now)
    registry.register("cron.status", cron_status)
    registry.register("cron.history", cron_history)
    logger.info("Cron management RPC methods registered")
