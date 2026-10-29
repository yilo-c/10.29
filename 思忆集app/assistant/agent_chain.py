"""Agent 链引擎：多步 Agent 链编排、自我反思循环、Human-in-the-loop、子任务委派。

MCP-13: Judge 意图深度判断与反思循环

核心工作流：
  L3 目标 → 拆解为 DAG → 展示计划给用户 → 逐节点执行（前置检查→委派→执行→反思）
  → 高风险节点暂停等确认 → 结果汇总

参考：
  - Windsurf Cascade 自动 Agent 模式（多步 planning + 状态持续）
  - AutoGPT 自我反思循环（每步执行后验证）
  - LangGraph StateGraph（有状态 DAG + Human-in-the-loop）
  - CrewAI 任务委派（按任务类型选择执行者）
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from threading import Lock
from typing import Any

from .chain._planner import PlannerMixin
from .chain._runner import RunnerMixin
from .chain._reflector import ReflectorMixin
from .chain._state import StateMixin
from .chain._nodes import (
    MAX_RETRIES_DEFAULT,
    MAX_REFLECTION_LOG,
    TIMEOUT_CONFIRMATION_LOW,
    TIMEOUT_CONFIRMATION_MEDIUM,
    TIMEOUT_CONFIRMATION_HIGH,
    DELEGATE_LOCAL,
    DELEGATE_CLOUD,
    DELEGATE_MCP,
    DELEGATE_AUTOMATION,
    NODE_STATUS_PENDING,
    NODE_STATUS_RUNNING,
    NODE_STATUS_COMPLETED,
    NODE_STATUS_FAILED,
    NODE_STATUS_SKIPPED,
    CHAIN_STATUS_PENDING,
    CHAIN_STATUS_RUNNING,
    CHAIN_STATUS_PAUSED,
    CHAIN_STATUS_COMPLETED,
    CHAIN_STATUS_FAILED,
    CHAIN_STATUS_CANCELLED,
    ConfirmationLevel,
    ReflectionAction,
)
from .models import AgentChainPlan, ChainNode, IntentDepth

logger = logging.getLogger("siyi.assistant.agent_chain")


class AgentChainEngine(PlannerMixin, RunnerMixin, ReflectorMixin, StateMixin):
    """Agent 链引擎：编排多步 Agent 任务的执行。

    职责：
    1. 将 L3 目标拆解为 DAG 节点 (build_chain)
    2. 展示执行计划给用户确认 (present_plan)
    3. 按拓扑序执行节点 (execute_chain)
    4. 每步执行后自我反思 (reflect)
    5. 高风险节点执行前暂停等用户确认 (request_confirmation)
    6. 子任务委派给合适的执行者 (delegate)
    7. 汇总执行结果并记录反思日志
    """

    def __init__(
        self,
        ai_client: Any | None = None,
        mcp_client: Any | None = None,
        capability_router: Any | None = None,
        confirm_callback: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
        notify_callback: Callable[[str, str], None] | None = None,
    ):
        self._ai_client = ai_client
        self._mcp_client = mcp_client
        self._capability_router = capability_router
        self._confirm_callback = confirm_callback
        self._notify_callback = notify_callback

        self._chain_status: str = CHAIN_STATUS_PENDING
        self._chain_id: str = ""
        self._nodes: dict[str, ChainNode] = {}
        self._reflection_log: list[dict[str, Any]] = []
        self._reflection_log_lock = Lock()
        self._started_at: str = ""

    # ═══════════════════════════════════════════════════════════
    # Task 2: Agent 链编排
    # ═══════════════════════════════════════════════════════════

    def build_chain(
        self,
        goal: str,
        context: dict[str, Any] | None = None,
    ) -> AgentChainPlan:
        """将 L3 目标拆解为 DAG 节点执行计划。

        使用 LLM 分析目标，生成子步骤列表，确定依赖关系和执行模式。

        Args:
            goal: 用户输入的复杂目标
            context: 可选的上下文信息

        Returns:
            AgentChainPlan 执行计划
        """
        nodes = self._decompose_goal(goal, context or {})
        plan = self._build_plan(goal, nodes, context or {})
        logger.info(
            "Agent 链计划已生成: %s → %d 步 (风险%s)",
            goal[:50], plan.total_steps, plan.highest_risk,
        )
        return plan

    # ═══════════════════════════════════════════════════════════
    # Task 2: 执行计划展示
    # ═══════════════════════════════════════════════════════════

    def present_plan(self, plan: AgentChainPlan) -> dict[str, Any]:
        """展示执行计划给用户确认（Windsurf Cascade 风格）。

        Returns:
            包含计划摘要的字典
        """
        return {
            "action": "present_plan",
            "goal": plan.goal,
            "summary": plan.summary_text(),
            "data": plan.to_dict(),
            "requires_confirmation": plan.highest_risk in ("medium", "high"),
        }


__all__ = [
    "AgentChainEngine",
    "AgentChainPlan",
    "ChainNode",
    "ConfirmationLevel",
    "ReflectionAction",
    "IntentDepth",
]
