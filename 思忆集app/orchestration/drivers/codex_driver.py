"""CodexDriver — Codex 任务驱动。

基于现有的 CodexMissionOrchestrator，包装为 AbstractAgentDriver 接口。
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Callable

from ..agent_driver import (
    AbstractAgentDriver,
    AgentCapability,
    AgentInfo,
    AgentInvocation,
    AgentStatus,
    AgentType,
    _new_id,
    _now,
)

logger = logging.getLogger("siyi.orchestration.drivers.codex")

# 延迟导入避免循环依赖
_CodexMissionOrchestrator = None


def _get_codex_orchestrator():
    global _CodexMissionOrchestrator
    if _CodexMissionOrchestrator is None:
        from assistant.codex import CodexMissionOrchestrator
        _CodexMissionOrchestrator = CodexMissionOrchestrator
    return _CodexMissionOrchestrator


class CodexDriver:
    """Codex 任务驱动。

    基于 CodexMissionOrchestrator 提供任务规划/审批/执行/验证链路。
    适合结构化编码任务。
    """

    agent_info = AgentInfo(
        agent_id="codex",
        agent_type=AgentType.CODEX,
        display_name="Codex",
        description="Codex 任务编排 — 结构化编码任务的规划、审批、执行与验证",
        capabilities=[
            AgentCapability.CODE_GENERATION,
            AgentCapability.TASK_PLANNING,
            AgentCapability.REASONING,
        ],
        version="",
        is_available=False,
        status=AgentStatus.UNAVAILABLE,
        max_concurrent=1,
        cost_rank=5,
    )

    def __init__(
        self,
        data_store=None,
        planner=None,
        security=None,
        memory=None,
        observability_service=None,
        settings_repository=None,
    ):
        self._data_store = data_store
        self._planner = planner
        self._security = security
        self._memory = memory
        self._observability_service = observability_service
        self._settings_repository = settings_repository
        self._orchestrator = None
        self._init_orchestrator()

    def _init_orchestrator(self):
        """初始化 CodexMissionOrchestrator。需要 executor，这里设置一个标记。"""
        # CodexMissionOrchestrator 需要 executor，会在 execute 时注入
        self.agent_info.is_available = True
        self.agent_info.status = AgentStatus.IDLE

    async def execute(
        self,
        task: str,
        context: dict[str, Any] | None = None,
        *,
        timeout: int = 300,
        progress_callback: Callable[[str], None] | None = None,
    ) -> AgentInvocation:
        invocation_id = _new_id()
        started_at = _now()
        self.agent_info.status = AgentStatus.BUSY

        if not self._data_store or not self._planner or not self._security or not self._memory:
            return AgentInvocation(
                invocation_id=invocation_id,
                agent_id="codex",
                task=task,
                status="failed",
                error="CodexDriver 未完整初始化（缺少 data_store/planner/security/memory）",
                started_at=started_at,
                completed_at=_now(),
            )

        try:
            import time
            start_time = time.monotonic()

            Orchestrator = _get_codex_orchestrator()

            # 从 assistant 延迟获取 capability_router 作为 executor
            executor = None
            if context and "executor" in context:
                executor = context["executor"]

            orchestrator = Orchestrator(
                data_store=self._data_store,
                planner=self._planner,
                executor=executor,
                security=self._security,
                memory=self._memory,
                observability_service=self._observability_service,
                settings_repository=self._settings_repository,
            )

            # 执行任务
            options = dict(context or {})
            result = orchestrator.run(
                goal=task,
                approved_actions=set(options.get("approved_actions", [])),
                force=bool(options.get("force", False)),
                options=options,
            )

            duration = time.monotonic() - start_time
            self.agent_info.invocation_count += 1
            self.agent_info.last_used_at = _now()

            summary = result.get("summary", {})
            ok_count = summary.get("ok", 0) if isinstance(summary, dict) else 0
            status = "completed" if result.get("status") == "success" else result.get("status", "failed")

            return AgentInvocation(
                invocation_id=invocation_id,
                agent_id="codex",
                task=task,
                context=dict(context or {}),
                status=status,
                result=result,
                error=result.get("verification", {}).get("status", "") if result.get("status") != "success" else "",
                started_at=started_at,
                completed_at=_now(),
                duration_seconds=round(duration, 2),
            )

        except asyncio.CancelledError:
            return AgentInvocation(
                invocation_id=invocation_id,
                agent_id="codex",
                task=task,
                status="cancelled",
                started_at=started_at,
                completed_at=_now(),
            )
        except Exception as exc:
            logger.exception("CodexDriver execute failed")
            return AgentInvocation(
                invocation_id=invocation_id,
                agent_id="codex",
                task=task,
                status="failed",
                error=f"{type(exc).__name__}: {exc}",
                started_at=started_at,
                completed_at=_now(),
            )
        finally:
            self.agent_info.status = AgentStatus.IDLE

    def cancel(self) -> bool:
        return False  # CodexMissionOrchestrator 不支持中断

    @property
    def is_available(self) -> bool:
        return self.agent_info.is_available
