"""Agent 链反思循环：自我反思、失败处理、Human-in-the-loop 确认、前置检查。

Extracted from agent_chain.py — AgentChainEngine reflection, confirmation,
prerequisite checking, and failure handling methods.
"""
from __future__ import annotations

import json
import logging
from collections.abc import Callable
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Protocol

from ._nodes import (
    MAX_REFLECTION_LOG,
    TIMEOUT_CONFIRMATION_MEDIUM, TIMEOUT_CONFIRMATION_HIGH,
    NODE_STATUS_COMPLETED, NODE_STATUS_FAILED,
    DELEGATE_LOCAL, DELEGATE_CLOUD, DELEGATE_MCP,
    ConfirmationLevel, ReflectionAction,
)
from ..models import ChainNode

logger = logging.getLogger("siyi.assistant.agent_chain.reflector")


class _ReflectorHost(Protocol):
    """ReflectorMixin 依赖的宿主契约 — AgentChainEngine.__init__ 注入 AI/MCP 客户端、
    确认/通知回调、链状态字段；RunnerMixin 提供 _execute_node。"""
    _nodes: dict[str, ChainNode]
    _mcp_client: Any
    _ai_client: Any
    _confirm_callback: Callable[[dict[str, Any]], dict[str, Any]] | None
    _chain_id: str
    _notify_callback: Callable[[str, str], None] | None
    _reflection_log: list[dict[str, Any]]
    _reflection_log_lock: Any  # threading.Lock

    def _execute_node(
        self,
        node: ChainNode,
        execute_callback: Callable[[ChainNode], dict[str, Any]] | None = None,
    ) -> dict[str, Any]: ...


class ReflectorMixin:
    """Mixin providing reflection, failure handling, confirmation, and prereq methods.

    Expects self._ai_client, self._mcp_client, self._nodes, self._chain_status,
    self._confirm_callback, self._notify_callback set by AgentChainEngine.__init__.

    Collaborates with RunnerMixin (_execute_node in _handle_execution_failure).
    """

    if TYPE_CHECKING:
        _nodes: dict[str, ChainNode]
        _mcp_client: Any
        _ai_client: Any
        _confirm_callback: Callable[[dict[str, Any]], dict[str, Any]] | None
        _chain_id: str
        _notify_callback: Callable[[str, str], None] | None
        _reflection_log: list[dict[str, Any]]
        _reflection_log_lock: Any  # threading.Lock

        def _execute_node(
            self,
            node: ChainNode,
            execute_callback: Callable[[ChainNode], dict[str, Any]] | None = None,
        ) -> dict[str, Any]: ...

    def check_prerequisites(self, node: ChainNode) -> dict[str, Any]:
        """检查节点执行的前置条件。"""
        issues: list[str] = []

        for dep_id in node.depends_on:
            dep_node = self._nodes.get(dep_id)
            if dep_node is None:
                issues.append(f"依赖节点 {dep_id} 不存在")
            elif dep_node.status != NODE_STATUS_COMPLETED:
                issues.append(f"依赖节点 {dep_node.description} 尚未完成（{dep_node.status}）")

        if node.delegate_to == DELEGATE_MCP and self._mcp_client is None:
            issues.append("MCP 客户端不可用，无法执行 MCP 委派")
        if node.delegate_to in (DELEGATE_LOCAL, DELEGATE_CLOUD) and self._ai_client is None:
            issues.append("AI 客户端不可用，无法执行 AI 委派")

        if issues:
            return {"passed": False, "reason": "; ".join(issues), "details": {"issues": issues}}
        return {"passed": True, "reason": "", "details": {}}

    def _request_confirmation_if_needed(self, node: ChainNode) -> dict[str, Any]:
        """在关键节点请求用户确认。

        P1-4: 在请求中传递 chain_id，便于前端关联到具体链执行。
        超时统一返回 reject（由 _agent_chain_confirm_callback 处理）。
        """
        if node.risk_level == "low":
            self._notify(f"ℹ️ 执行：{node.description}", ConfirmationLevel.LOW)
            return {"action": "approve", "reason": "低风险操作，默认继续"}

        if self._confirm_callback is None:
            return {"action": "approve", "reason": "无确认回调，默认放行"}

        timeout_map = {
            ConfirmationLevel.MEDIUM: TIMEOUT_CONFIRMATION_MEDIUM,
            ConfirmationLevel.HIGH: TIMEOUT_CONFIRMATION_HIGH,
        }
        timeout = timeout_map.get(node.risk_level, TIMEOUT_CONFIRMATION_MEDIUM)

        request = {
            "type": "node_confirmation",
            "node_id": node.node_id,
            "description": node.description,
            "action_id": node.action_id,
            "risk_level": node.risk_level,
            "delegate_to": node.delegate_to,
            "detail": self._build_confirmation_detail(node),
            "timeout_seconds": timeout,
            "chain_id": self._chain_id,  # P1-4: 传递链 ID
        }

        try:
            response = self._confirm_callback(request)
            action = str(response.get("action", "reject") or "reject")

            if action == "approve":
                return {"action": "approve", "reason": "用户已确认"}
            elif action == "reject":
                return {"action": "reject", "reason": response.get("reason", "用户拒绝")}
            else:
                # P1-4: 不再返回 timeout，统一为 reject
                return {"action": "reject", "reason": response.get("reason", "用户未响应，默认拒绝")}
        except Exception as exc:
            logger.warning("确认回调异常", exc_info=True)
            if node.risk_level == ConfirmationLevel.LOW:
                return {"action": "approve", "reason": f"确认回调异常，默认放行: {exc}"}
            return {"action": "reject", "reason": f"确认回调异常，已拒绝: {exc}"}

    @staticmethod
    def _build_confirmation_detail(node: ChainNode) -> str:
        """构建确认详情文本。"""
        parts = [f"操作：{node.description}"]
        if node.action_id:
            parts.append(f"动作：{node.action_id}")
        parts.append(f"执行者：{node.delegate_to}")
        parts.append(f"风险级别：{node.risk_level}")
        if node.payload:
            parts.append(f"参数：{json.dumps(node.payload, ensure_ascii=False)}")
        return "\n".join(parts)

    def _reflect(self, node: ChainNode, exec_result: dict[str, Any]) -> dict[str, Any]:
        """执行后的自我反思：验证结果、检查异常、决定后续动作。"""
        errors: list[str] = []
        data = exec_result.get("data", {})

        if not data:
            errors.append("结果为空")

        text_result = ""
        if isinstance(data, dict):
            text_result = str(data.get("text", "") or data.get("result", "") or "")
            if data.get("error"):
                errors.append(str(data["error"]))
        elif isinstance(data, str):
            text_result = data

        if not text_result.strip():
            errors.append("结果内容为空")

        if errors:
            return {
                "passed": False,
                "summary": "；".join(errors),
                "errors": errors,
                "action": ReflectionAction.RETRY if node.retry_count < node.max_retries else ReflectionAction.ADJUST,
                "adjustment": {"retry_count": node.retry_count} if node.retry_count < node.max_retries else None,
            }

        return {
            "passed": True,
            "summary": "执行成功，结果符合预期",
            "errors": [],
            "action": ReflectionAction.CONTINUE,
            "adjustment": None,
        }

    def _handle_execution_failure(
        self,
        node: ChainNode,
        exec_result: dict[str, Any],
    ) -> dict[str, Any]:
        """处理执行失败：重试 → 重规划 → 停止。

        P1-3: 三级降级策略：
        1. RETRY：可重试错误 + retry_count < max_retries → 重试同一节点
        2. ADJUST：重试耗尽 或 不可重试错误 → 触发重规划（由 _runner.execute_chain 处理）
        3. STOP：调用方在重规划次数超限后判定（不在本方法内终止）
        """
        error = exec_result.get("error", "未知错误")

        if node.retry_count < node.max_retries and self._is_retryable(error):
            node.retry_count += 1
            reflection = {
                "passed": False,
                "summary": f"错误：{error}，正在重试（{node.retry_count}/{node.max_retries}）",
                "errors": [error],
                "action": ReflectionAction.RETRY,
                "adjustment": {"retry_count": node.retry_count},
            }
            retry_result = self._execute_node(node)
            if retry_result.get("success", False):
                node.status = NODE_STATUS_COMPLETED
                node.result = retry_result.get("data", {})
                return {
                    "status": NODE_STATUS_COMPLETED,
                    "error": "",
                    "reflection": reflection,
                    "retries": node.retry_count,
                    "retry_success": True,
                    "retry_result": retry_result,
                }
            # 重试失败 → 更新 error 进入 ADJUST 分支
            error = retry_result.get("error", error) or error

        # P1-3: 重试耗尽或不可重试 → 返回 ADJUST 动作，由 execute_chain 决定是否重规划
        reflection = {
            "passed": False,
            "summary": f"重试耗尽/不可重试：{error}，建议重规划",
            "errors": [error],
            "action": ReflectionAction.ADJUST,
            "adjustment": {
                "original_node_id": node.node_id,
                "failed_delegate": node.delegate_to,
                "error": error,
            },
        }
        self._log_reflection(node, False, error, ReflectionAction.ADJUST)
        return {
            "status": NODE_STATUS_FAILED,
            "error": error,
            "reflection": reflection,
            "retries": node.retry_count,
            "needs_replan": True,  # P1-3: 提示 execute_chain 触发重规划
        }

    @staticmethod
    def _is_retryable(error: str) -> bool:
        """判断错误是否可重试。"""
        non_retryable = [
            "权限", "permission", "denied", "forbidden",
            "未找到", "not found", "404",
            "无效", "invalid", "validation",
            "超时", "timeout",
            "不可用", "unavailable",
        ]
        error_lower = error.lower()
        return not any(p in error_lower for p in non_retryable)

    def _log_reflection(
        self,
        node: ChainNode,
        passed: bool,
        summary: str,
        action: str,
    ) -> None:
        """记录反思日志。"""
        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "chain_id": self._chain_id,
            "node_id": node.node_id,
            "description": node.description,
            "passed": passed,
            "summary": summary,
            "action": action,
            "risk_level": node.risk_level,
            "delegate_to": node.delegate_to,
        }
        with self._reflection_log_lock:
            self._reflection_log.append(entry)
            if len(self._reflection_log) > MAX_REFLECTION_LOG:
                self._reflection_log = self._reflection_log[-MAX_REFLECTION_LOG:]

    def _notify(self, message: str, level: str = ConfirmationLevel.LOW) -> None:
        """发送通知（低风险提示、进度更新等）。"""
        if self._notify_callback is not None:
            try:
                self._notify_callback(message, level)
            except Exception:
                logger.warning("发送通知回调失败 (level=%s)", level)
