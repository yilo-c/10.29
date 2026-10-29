"""Agent delegation capability mixin — extracted from capability_router.py."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any, Protocol

logger = logging.getLogger("siyi.assistant.autonomy.router.agent")


class AgentRouterHost(Protocol):
    """AgentRouterMixin 依赖的宿主契约 — AssistantCapabilityRouter 由 __init__ 注入。"""

    agent_directory: Any


class AgentRouterMixin:
    """Agent delegation and directory capability handlers."""

    if TYPE_CHECKING:
        agent_directory: Any

    def _agent_delegate(self, payload: dict[str, Any]) -> dict[str, Any]:
        """委派任务给指定 AI 客户端执行。"""
        agent_id = str(payload.get("agent_id") or "").strip()
        task = str(payload.get("task") or payload.get("goal") or "").strip()
        timeout = int(payload.get("timeout", 300))
        if not agent_id:
            return {"success": False, "error": "缺少 agent_id 参数"}
        if not task:
            return {"success": False, "error": "缺少 task 参数"}
        directory = self.agent_directory
        if directory is None:
            return {"success": False, "error": "AgentDirectory 未初始化"}
        driver = directory.get_driver(agent_id)
        if driver is None:
            available = directory.list_infos()
            return {
                "success": False,
                "error": f"Agent '{agent_id}' 未注册",
                "available_agents": [a.agent_id for a in available if a.is_available],
            }
        if not driver.is_available:
            return {"success": False, "error": f"Agent '{agent_id}' 当前不可用"}
        try:
            result = asyncio.run(driver.execute(task, timeout=timeout))
            directory.record_invocation(result.invocation_id, agent_id, task, result.status)
            return {
                "success": result.status in ("completed", "success"),
                "agent_id": agent_id,
                "invocation_id": result.invocation_id,
                "status": result.status,
                "result": result.result,
                "error": result.error,
                "duration_seconds": result.duration_seconds,
            }
        except Exception as exc:
            return {"success": False, "error": f"{type(exc).__name__}: {exc}"}

    def _agent_ask(self, payload: dict[str, Any]) -> dict[str, Any]:
        """向指定 AI 客户端提问/咨询。"""
        agent_id = str(payload.get("agent_id") or "").strip()
        question = str(payload.get("question") or payload.get("text") or "").strip()
        if not agent_id:
            return {"success": False, "error": "缺少 agent_id 参数"}
        if not question:
            return {"success": False, "error": "缺少 question 参数"}
        return self._agent_delegate({
            "agent_id": agent_id,
            "task": question,
            "timeout": payload.get("timeout", 120),
        })

    def _agent_directory(self, payload: dict[str, Any]) -> dict[str, Any]:
        """列出所有可用 AI 客户端及其能力。"""
        directory = self.agent_directory
        if directory is None:
            return {"success": False, "error": "AgentDirectory 未初始化"}
        capability_filter = str(payload.get("capability") or "").strip().lower()
        all_agents = directory.list_infos()
        if capability_filter:
            from orchestration.agent_driver import AgentCapability
            try:
                cap = AgentCapability(capability_filter)
                agents = directory.find_by_capability(cap)
            except ValueError:
                agents = [a for a in all_agents if capability_filter in a.agent_id]
        else:
            agents = all_agents
        result = []
        for a in agents:
            info = {"agent_id": a.agent_id, "name": a.name, "description": a.description, "is_available": a.is_available}
            if a.capabilities:
                info["capabilities"] = [str(c) for c in a.capabilities]
            if a.last_seen:
                info["last_seen"] = a.last_seen.isoformat() if hasattr(a.last_seen, "isoformat") else str(a.last_seen)
            result.append(info)
        return {"success": True, "agents": result, "count": len(result)}
