from __future__ import annotations

import logging
import time
import uuid
from typing import TYPE_CHECKING, Any, Protocol

from assistant.routing.judge import JudgeScheduler
from .models import AgentChainPlan, IntentDepth

logger = logging.getLogger("siyi.assistant.autonomy")


class AssistantJudgeHost(Protocol):
    """AssistantJudgeMixin 依赖的宿主契约 — AutonomousAssistantManager 由 LifecycleBuilder 注入。"""

    planner: Any
    agent_chain: Any


class AssistantJudgeMixin:
    """Judge 意图深度分类 + Agent 链编排 Mixin。

    混合到 AutonomousAssistantManager，提供 MCP-13 能力。
    依赖宿主提供：self.planner, self.router, self.agent_chain, self.inbox_service, self.task_lifecycle
    """

    if TYPE_CHECKING:
        planner: Any
        agent_chain: Any

    # ═══════════════════════════════════════════════════════════
    # Judge 意图深度分类
    # ═══════════════════════════════════════════════════════════

    def classify_goal_depth(self, goal: str) -> dict[str, object]:
        """对目标进行意图深度三级分类。

        Returns:
            {"depth": "L1"|"L2"|"L3", "label": str, "reasoning": str, "confidence": float}
        """
        judge = self._get_judge()
        depth, reasoning, confidence = judge.classify_intent_depth(goal)
        return {
            "depth": depth.value,
            "label": IntentDepth.label(depth),
            "reasoning": reasoning,
            "confidence": confidence,
        }

    def _get_judge(self) -> JudgeScheduler:
        """P1-1: 获取 JudgeScheduler 实例。

        优先使用 lifecycle_builder 注入的 self.judge_scheduler（已装配
        cost_tracker + budget_manager，路由决策受预算监管）；
        测试环境（``__new__`` 跳过 ``__init__``）回退到懒创建。
        """
        judge = getattr(self, "judge_scheduler", None)
        if judge is not None:
            return judge
        judge = getattr(self, "_judge", None)
        if judge is None:
            judge = JudgeScheduler(
                ai_client=getattr(self.planner, "_ai_client", None),
            )
            self._judge = judge  # type: ignore[attr-defined]
        return judge

    def judge_and_route(self, goal: str) -> dict[str, object]:
        """判断意图深度并路由到合适的执行路径。

        L1 → 返回直接回答标记（由上层对话处理）
        L2 → 调用路由执行单步工具
        L3 → 启动 Agent 链

        Returns:
            {"depth": str, "route": str, "plan": dict|None, ...}
        """
        depth_info = self.classify_goal_depth(goal)
        depth_str = depth_info.get("depth", "L2")

        if depth_str == "L1":
            return {
                "depth": "L1",
                "route": "direct_answer",
                "label": "直接回答",
                "message": "简单问答，由对话模型直接回答",
                **depth_info,
            }

        if depth_str == "L2":
            # P1-1: 统一用注入的 judge_scheduler（含预算监管），
            # 不再每次懒创建无 cost/budget 的 JudgeScheduler
            judge = self._get_judge()
            decision = judge.route(goal)
            return {
                "depth": "L2",
                "route": "single_tool",
                "label": "单步工具调用",
                "decision": decision.to_dict(),
                **depth_info,
            }

        # P1-2: L3 真实执行 —— 从 present_plan 改为 run_agent_chain
        chain_plan = self.agent_chain.build_chain(goal)
        plan_presentation = self.agent_chain.present_plan(chain_plan)
        execution_result = self.run_agent_chain(goal, plan=chain_plan, confirmed=True)
        return {
            "depth": "L3",
            "route": "agent_chain",
            "label": "多步 Agent 链",
            "plan": plan_presentation,
            "execution_result": execution_result,
            **depth_info,
        }

    # ═══════════════════════════════════════════════════════════
    # Agent 链编排
    # ═══════════════════════════════════════════════════════════

    def run_agent_chain(
        self,
        goal: str,
        *,
        plan: object = None,
        confirmed: bool = True,
    ) -> dict[str, object]:
        """执行 Agent 链（L3 任务）。

        Args:
            goal: 任务目标
            plan: 可选的外部提供的计划（如用户编辑后）
            confirmed: 用户是否已确认执行计划

        Returns:
            执行结果汇总
        """
        chain_plan = plan
        if chain_plan is None:
            chain_plan = self.agent_chain.build_chain(goal)

        if not isinstance(chain_plan, AgentChainPlan):
            return {
                "success": False,
                "error": "无效的执行计划",
                "chain_id": self.agent_chain.chain_id,
            }

        def _precheck(node):
            return self.agent_chain.check_prerequisites(node)

        def _execute(node):
            from .models import PlannedStep

            step = PlannedStep(
                step_id=node.node_id,
                action_id=node.action_id,
                payload=node.payload,
                risk_level=node.risk_level,
            )
            try:
                result = self.router.execute(step)
                return {"success": True, "data": {"result": result, "node_id": node.node_id}}
            except Exception as exc:
                return {"success": False, "error": str(exc)}

        result = self.agent_chain.execute_chain(
            chain_plan,
            precheck_callback=_precheck,
            execute_callback=_execute,
        )

        try:
            reflection_log = self.agent_chain.get_reflection_log()
            if reflection_log and hasattr(self, "task_lifecycle") and self.task_lifecycle is not None:
                memory = getattr(self.task_lifecycle, "memory", None)
                if memory is not None and hasattr(memory, "append_decision"):
                    memory.append_decision(
                        goal,
                        [
                            {
                                "step": r.get("description"),
                                "result": "passed" if r.get("passed") else "failed",
                            }
                            for r in reflection_log
                        ],
                        {"reflection_log": reflection_log},
                    )
        except Exception as exc:
            logger.warning("Agent链反思日志记录失败: %s", exc, exc_info=True)

        return result

    def get_agent_chain_status(self) -> dict[str, object]:
        """获取当前 Agent 链状态。"""
        return {
            "chain_id": self.agent_chain.chain_id,
            "status": self.agent_chain.chain_status,
            "reflection_log_count": len(self.agent_chain.get_reflection_log()),
        }

    def pause_agent_chain(self) -> dict[str, object]:
        """暂停当前 Agent 链。"""
        paused = self.agent_chain.pause_chain()
        return {"success": paused, "status": self.agent_chain.chain_status}

    def resume_agent_chain(self) -> dict[str, object]:
        """恢复已暂停的 Agent 链。"""
        resumed = self.agent_chain.resume_chain()
        return {"success": resumed, "status": self.agent_chain.chain_status}

    def cancel_agent_chain(self, reason: str = "用户取消") -> dict[str, object]:
        """取消当前 Agent 链。"""
        cancelled = self.agent_chain.cancel_chain(reason)
        return {"success": cancelled, "status": self.agent_chain.chain_status}

    # ═══════════════════════════════════════════════════════════
    # P1-4: 人工确认漏斗修复
    # ═══════════════════════════════════════════════════════════

    # 轮询确认响应的间隔（秒）
    _CONFIRM_POLL_INTERVAL = 2.0
    # 无 inbox_service 时轮询的总超时（秒）—— 短超时快速 fail
    _CONFIRM_FALLBACK_TIMEOUT = 5.0

    def _agent_chain_confirm_callback(self, request: dict[str, object]) -> dict[str, object]:
        """P1-4: Agent 链的 Human-in-the-loop 确认回调。

        低风险默认放行；中高风险：
        1. 投递确认请求到 inbox（前端可订阅 ``inbox.delivered`` 弹窗）
        2. 轮询 inbox 等待用户响应
        3. 超时 → 默认**拒绝**（不再返回 timeout 静默 fail）
        """
        risk_level = str(request.get("risk_level", "low"))
        node_id = str(request.get("node_id", ""))
        chain_id = str(request.get("chain_id", ""))
        description = str(request.get("description", ""))
        detail = str(request.get("detail", ""))
        timeout_seconds = int(request.get("timeout_seconds", 60))

        if risk_level == "low":
            return {"action": "approve", "reason": "低风险默认放行"}

        inbox = getattr(self, "inbox_service", None)

        # 无 inbox 服务时快速拒绝（不再静默 timeout）
        if inbox is None:
            logger.warning(
                "P1-4 确认请求无 inbox_service: node=%s risk=%s → 默认拒绝",
                node_id, risk_level,
            )
            return {
                "action": "reject",
                "reason": "无 inbox 服务，无法请求用户确认，默认拒绝",
            }

        # 生成唯一确认 ID
        confirm_id = f"confirm_{node_id}_{uuid.uuid4().hex[:6]}"

        # 1. 投递确认请求到 inbox
        try:
            inbox.deliver_confirmation_request(
                confirm_id=confirm_id,
                title=f"⚠️ 需要确认: {description[:50]}",
                content=detail or f"风险级别 {risk_level}，需要用户确认",
                risk_level=risk_level,
                detail=detail,
                chain_id=chain_id,
                node_id=node_id,
                timeout_seconds=timeout_seconds,
            )
        except Exception:
            logger.warning("P1-4 投递确认请求到 inbox 失败", exc_info=True)
            return {
                "action": "reject",
                "reason": "投递确认请求失败，默认拒绝",
            }

        logger.info(
            "P1-4 Agent 链需要用户确认: %s (risk=%s, confirm_id=%s, timeout=%ds)",
            description, risk_level, confirm_id, timeout_seconds,
        )

        # 2. 轮询 inbox 等待响应
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            try:
                response = inbox.check_confirmation_response(confirm_id)
                if response is not None:
                    action = str(response.get("action", "reject"))
                    reason = str(response.get("reason", ""))
                    logger.info(
                        "P1-4 收到用户确认响应: confirm_id=%s action=%s",
                        confirm_id, action,
                    )
                    if action == "approve":
                        return {"action": "approve", "reason": reason or "用户已确认"}
                    return {"action": "reject", "reason": reason or "用户拒绝"}
            except Exception:
                logger.debug("P1-4 轮询确认响应异常", exc_info=True)
            time.sleep(self._CONFIRM_POLL_INTERVAL)

        # 3. 超时 → 默认拒绝（不再返回 timeout）
        logger.warning(
            "P1-4 确认超时 → 默认拒绝: confirm_id=%s node=%s timeout=%ds",
            confirm_id, node_id, timeout_seconds,
        )
        # 投递超时拒绝通知到 inbox（可见反馈）
        try:
            inbox.deliver(
                title=f"❌ 确认超时已拒绝: {description[:40]}",
                content=f"节点 {node_id} 等待 {timeout_seconds}s 未收到用户响应，已默认拒绝执行。",
                channel="inbox",
                priority="high" if risk_level == "high" else "normal",
                source="confirmation_timeout",
                source_id=confirm_id,
                tags=["confirmation_timeout", f"risk_{risk_level}"],
                metadata={
                    "confirm_id": confirm_id,
                    "node_id": node_id,
                    "chain_id": chain_id,
                    "timeout_seconds": timeout_seconds,
                },
            )
        except Exception:
            logger.debug("P1-4 投递超时通知失败", exc_info=True)

        return {
            "action": "reject",
            "reason": f"确认超时（{timeout_seconds}s），默认拒绝",
        }

    def _agent_chain_notify_callback(self, message: str, level: str) -> None:
        """P1-4: Agent 链通知回调 —— 高优先级通知投递到 inbox + emit 事件。

        level 取值：``low`` / ``medium`` / ``high`` / ``urgent``
        - low/medium: 仅记录日志（避免噪音）
        - high/urgent: 投递到 inbox + emit ``agent.chain.notification`` 事件
        """
        logger.info("[Agent链通知] [%s] %s", level, message)

        if level not in ("high", "urgent"):
            return

        inbox = getattr(self, "inbox_service", None)
        if inbox is None:
            return

        try:
            from core.events import AppEvent, event_bus
            priority = "urgent" if level == "urgent" else "high"
            inbox.deliver(
                title=f"[Agent链] {level.upper()}",
                content=message[:500],
                channel="both" if level == "urgent" else "inbox",
                priority=priority,
                source="agent_chain_notify",
                tags=["agent_chain", level],
            )
            event_bus.emit(AppEvent(
                event_type="agent.chain.notification",
                source="assistant.agent_chain",
                payload={"message": message, "level": level},
            ))
        except Exception:
            logger.debug("P1-4 notify 回调投递失败", exc_info=True)

