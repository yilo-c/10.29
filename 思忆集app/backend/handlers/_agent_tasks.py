"""External agent orchestration JSON-RPC handlers — agent.list / schedule / cancel / run_now / history.

Bridges `AgentDirectory` (orchestration drivers) with `CronService` (scheduled execution).
Cron-scheduled `agent:*` jobs are dispatched through `AppContext._execute_cron_action`
→ `AssistantCapabilityRouter` → `agent.delegate` handler, so we don't need a new router action here.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from app_runtime.app_context import AppContext
from backend.i18n import t

logger = logging.getLogger("siyi.backend.handlers.agent_tasks")


def register_agent_tasks(registry, context: AppContext) -> None:
    """Register agent orchestration RPC methods."""

    def _directory():
        return getattr(context, "agent_directory", None)

    def _cron():
        return getattr(context, "cron_service", None)

    # ── agent.list ───────────────────────────────────────────────
    async def agent_list(**kwargs: Any) -> dict[str, Any]:
        """List all registered external AI agents and their availability."""
        directory = _directory()
        if directory is None:
            return {"success": False, "error": t("agent.directory_unavailable"), "agents": []}
        try:
            infos = await asyncio.to_thread(directory.list_infos)
        except Exception as exc:
            logger.warning("agent.list failed: %s", exc)
            return {"success": False, "error": str(exc), "agents": []}
        agents = [info.to_dict() for info in infos]
        return {"success": True, "agents": agents, "count": len(agents)}

    # @deprecated (S11, 2026-08-07): 僵尸接口，前端未调用。见 UNUSED_METHODS_BASELINE。
    registry.register("agent.list", agent_list)

    # ── agent.schedule ────────────────────────────────────────────
    async def agent_schedule(
        agent_id: str = "",
        task: str = "",
        schedule: str = "",
        run_at: str = "",
        repeat: str = "none",
        name: str = "",
        timeout: int = 300,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Schedule an agent task as a cron job.

        The cron service will dispatch `agent.delegate` with `{agent_id, task, timeout}`.
        """
        agent_id = str(agent_id or "").strip()
        task = str(task or "").strip()
        if not agent_id:
            return {"success": False, "error": t("error.missing_param", name="agent_id")}
        if not task:
            return {"success": False, "error": t("error.missing_param", name="task")}
        directory = _directory()
        if directory is None:
            return {"success": False, "error": t("agent.directory_unavailable")}
        if directory.get_driver(agent_id) is None:
            return {"success": False, "error": t("agent.not_registered", agent_id=agent_id)}
        cron = _cron()
        if cron is None:
            return {"success": False, "error": t("agent.cron_unavailable")}
        job_name = (name or f"agent:{agent_id}").strip()
        payload = {"agent_id": agent_id, "task": task, "timeout": int(timeout)}
        try:
            job = await asyncio.to_thread(
                cron.add_job,
                job_name,
                "agent.delegate",
                payload,
                schedule,
                run_at,
                repeat,
            )
        except Exception as exc:
            logger.warning("agent.schedule failed: %s", exc)
            return {"success": False, "error": str(exc)}
        return {"success": True, "job": job}

    registry.register("agent.schedule", agent_schedule)

    # ── agent.cancel ──────────────────────────────────────────────
    async def agent_cancel(job_id: str = "", **kwargs: Any) -> dict[str, Any]:
        """Cancel a previously scheduled agent cron job."""
        job_id = str(job_id or "").strip()
        if not job_id:
            return {"success": False, "error": t("error.missing_param", name="job_id")}
        cron = _cron()
        if cron is None:
            return {"success": False, "error": t("agent.cron_unavailable")}
        try:
            removed = await asyncio.to_thread(cron.remove_job, job_id)
        except Exception as exc:
            logger.warning("agent.cancel failed: %s", exc)
            return {"success": False, "error": str(exc)}
        return {"success": removed, "job_id": job_id}

    registry.register("agent.cancel", agent_cancel)

    # ── agent.run_now ─────────────────────────────────────────────
    async def agent_run_now(
        agent_id: str = "",
        task: str = "",
        context_data: dict[str, Any] | None = None,
        timeout: int = 300,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Execute an agent task immediately and return the invocation result."""
        agent_id = str(agent_id or "").strip()
        task = str(task or "").strip()
        if not agent_id:
            return {"success": False, "error": t("error.missing_param", name="agent_id")}
        if not task:
            return {"success": False, "error": t("error.missing_param", name="task")}
        directory = _directory()
        if directory is None:
            return {"success": False, "error": t("agent.directory_unavailable")}
        driver = directory.get_driver(agent_id)
        if driver is None:
            return {"success": False, "error": t("agent.not_registered", agent_id=agent_id)}
        if not driver.is_available:
            return {"success": False, "error": t("agent.unavailable", agent_id=agent_id)}
        try:
            invocation = await driver.execute(
                task,
                context_data or {},
                timeout=int(timeout),
            )
        except Exception as exc:
            logger.warning("agent.run_now(%s) failed: %s", agent_id, exc)
            return {"success": False, "error": f"{type(exc).__name__}: {exc}"}
        try:
            directory.record_invocation(
                invocation.invocation_id,
                agent_id,
                task,
                invocation.status,
            )
        except Exception:
            logger.debug("agent.run_now: failed to record invocation", exc_info=True)
        return {
            "success": invocation.status in ("completed", "success"),
            "invocation": invocation.to_dict(),
        }

    registry.register("agent.run_now", agent_run_now)

    # ── agent.history ─────────────────────────────────────────────
    async def agent_history(limit: int = 20, **kwargs: Any) -> dict[str, Any]:
        """Return recent agent invocation history (in-memory, last 500)."""
        directory = _directory()
        if directory is None:
            return {"success": False, "error": t("agent.directory_unavailable"), "invocations": []}
        try:
            invocations = await asyncio.to_thread(
                directory.get_recent_invocations,
                max(1, int(limit)),
            )
        except Exception as exc:
            logger.warning("agent.history failed: %s", exc)
            return {"success": False, "error": str(exc), "invocations": []}
        return {"success": True, "invocations": invocations, "count": len(invocations)}

    registry.register("agent.history", agent_history)
