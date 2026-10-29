"""Agent 链状态管理：链汇总、暂停/恢复/取消、反思日志。

Extracted from agent_chain.py — AgentChainEngine state management methods.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Protocol

from ._nodes import (
    NODE_STATUS_COMPLETED, NODE_STATUS_FAILED, NODE_STATUS_SKIPPED, NODE_STATUS_RUNNING,
    CHAIN_STATUS_RUNNING, CHAIN_STATUS_PAUSED, CHAIN_STATUS_CANCELLED,
)
from ..models import AgentChainPlan, ChainNode

logger = logging.getLogger("siyi.assistant.agent_chain.state")


class _StateHost(Protocol):
    """StateMixin 依赖的宿主契约 — AgentChainEngine.__init__ 注入链状态字段。"""
    _chain_status: str
    _chain_id: str
    _nodes: dict[str, ChainNode]
    _reflection_log: list[dict[str, Any]]
    _started_at: str


class StateMixin:
    """Mixin providing chain state management methods.

    Expects self._chain_status, self._chain_id, self._nodes, self._reflection_log,
    self._started_at set by AgentChainEngine.__init__.
    """

    if TYPE_CHECKING:
        _chain_status: str
        _chain_id: str
        _nodes: dict[str, ChainNode]
        _reflection_log: list[dict[str, Any]]
        _started_at: str

    def _build_chain_summary(
        self,
        plan: AgentChainPlan,
        outcomes: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """构建链执行汇总。"""
        total = len(plan.nodes)
        completed = sum(1 for o in outcomes if o.get("status") == NODE_STATUS_COMPLETED)
        failed = sum(1 for o in outcomes if o.get("status") == NODE_STATUS_FAILED)
        skipped = sum(1 for o in outcomes if o.get("status") == NODE_STATUS_SKIPPED)
        reflection_passed = sum(
            1 for o in outcomes
            if o.get("reflection", {}).get("passed", False)
        )
        reflection_failed = sum(
            1 for o in outcomes
            if o.get("reflection", {}).get("passed") is False
        )
        # P1-3: 统计重规划相关指标（可观测性）
        replan_triggered = sum(
            1 for o in outcomes
            if "_replan" in o.get("node_id", "")
        )
        replan_succeeded = sum(
            1 for o in outcomes
            if "_replan" in o.get("node_id", "")
            and o.get("status") == NODE_STATUS_COMPLETED
        )

        return {
            "chain_id": self._chain_id,
            "goal": plan.goal,
            "status": self._chain_status,
            "total": total,
            "completed": completed,
            "failed": failed,
            "skipped": skipped,
            "reflection_passed": reflection_passed,
            "reflection_failed": reflection_failed,
            "replan_triggered": replan_triggered,  # P1-3
            "replan_succeeded": replan_succeeded,   # P1-3
            "outcomes": outcomes,
            "reflection_log": self._reflection_log,
            "started_at": self._started_at,
            "completed_at": datetime.now(timezone.utc).isoformat(),
            "highest_risk": plan.highest_risk,
        }

    def get_reflection_log(self) -> list[dict[str, Any]]:
        """获取当前链的反思日志。"""
        return list(self._reflection_log)

    def pause_chain(self) -> bool:
        """暂停当前运行的 Agent 链。"""
        if self._chain_status != CHAIN_STATUS_RUNNING:
            return False
        self._chain_status = CHAIN_STATUS_PAUSED
        logger.info("Agent 链已暂停: %s", self._chain_id)
        return True

    def resume_chain(self) -> bool:
        """恢复已暂停的 Agent 链。"""
        if self._chain_status != CHAIN_STATUS_PAUSED:
            return False
        self._chain_status = CHAIN_STATUS_RUNNING
        logger.info("Agent 链已恢复: %s", self._chain_id)
        return True

    def cancel_chain(self, reason: str = "用户取消") -> bool:
        """取消当前运行的 Agent 链。"""
        self._chain_status = CHAIN_STATUS_CANCELLED
        for node in self._nodes.values():
            if node.status == NODE_STATUS_RUNNING:
                node.status = NODE_STATUS_FAILED
                node.error = reason
        logger.info("Agent 链已取消: %s - %s", self._chain_id, reason)
        return True

    @property
    def chain_status(self) -> str:
        return self._chain_status

    @property
    def chain_id(self) -> str:
        return self._chain_id
