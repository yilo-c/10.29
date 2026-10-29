from __future__ import annotations

import logging
import threading

from .agent_chain import AgentChainEngine
from .capability_router import AssistantCapabilityRouter
from assistant.bridging.codex import CodexMissionOrchestrator
from .dispatch_result import DispatchResult
from .events import AssistantEventLayer
from assistant.execution.execution import AssistantExecutionLayer
from .judge_mixin import AssistantJudgeMixin
from memory.layer import AssistantMemoryLayer
from .models import AgentTask, PlannedStep
from assistant.agents.planner import AssistantPlannerLayer
from .security_layer import AssistantSecurityLayer
from .task_manager import TaskManager
from core.settings_schema import KEY_ASSISTANT_ALLOW_SELF_UPDATE

# Phase 4: 5 个拆分组件
from .autonomy_components import (
    AutonomyDispatcher,
    AutonomyDecisionOptimizer,
    AutonomyTelemetry,
    AutonomyScheduler,
    AutonomyLifecycleBuilder,
)

logger = logging.getLogger("siyi.assistant.autonomy")


class AutonomousAssistantManager(AssistantJudgeMixin):
    """五层总控：事件层 → 计划层 → 安全层(权限矩阵) → 执行层(审计回滚) → 记忆层。

    Phase 4 架构演进：原 ~990 行类已拆分为 5 个聚焦组件（位于
    ``assistant/autonomy_components/``），本类保留为组合根 + 公共 API 装饰层。
    所有公开方法签名保持不变以维持向后兼容；测试通过 ``__new__`` 跳过
    ``__init__`` 后直接 mock ``manager.X`` 的契约也得到保留。

    拆分组件：
    - ``dispatcher`` — 用户输入分发（chat/execute/automate）
    - ``decision_optimizer`` — 决策优化与决策记忆
    - ``telemetry`` — Judge 路由审计
    - ``scheduler`` — 定时/系统事件触发
    - ``AutonomyLifecycleBuilder`` — 组件装配（替代 200 行 __init__ 模板）
    """

    def __init__(
        self,
        *,
        data_store,
        settings_repository,
        observability_service,
        note_repository,
        task_repository,
        news_repository=None,
        inbox_repository,
        integration_hub=None,
        sync_orchestrator=None,
        mcp_manager=None,
        subagent_manager=None,
        memory_index_service=None,
        user_profile_service=None,
        intent_router=None,
        inbox_service=None,
        source_registry=None,
        evolution_orchestrator=None,
        tech_watch_collector=None,
        tech_watch_judge=None,
        tech_watch_store=None,
        ai_client=None,
        agent_directory=None,  # ← Agent 编排目录
        process_lifecycle_manager=None,
    ):
        # 1. 先创建 5 个组件（仅持有 manager 反向引用，无其他副作用）
        #    这样 LifecycleBuilder 可以访问 dispatcher.COMPUTER_USE_KEYWORDS 等类属性
        self.dispatcher = AutonomyDispatcher(self)
        self.decision_optimizer = AutonomyDecisionOptimizer(self)
        self.telemetry = AutonomyTelemetry(self)
        self.scheduler = AutonomyScheduler(self)

        # 2. 委托给 LifecycleBuilder 装配所有 collaborator
        AutonomyLifecycleBuilder.build_into(
            self,
            data_store=data_store,
            settings_repository=settings_repository,
            observability_service=observability_service,
            note_repository=note_repository,
            task_repository=task_repository,
            news_repository=news_repository,
            inbox_repository=inbox_repository,
            integration_hub=integration_hub,
            sync_orchestrator=sync_orchestrator,
            mcp_manager=mcp_manager,
            subagent_manager=subagent_manager,
            memory_index_service=memory_index_service,
            user_profile_service=user_profile_service,
            intent_router=intent_router,
            inbox_service=inbox_service,
            source_registry=source_registry,
            evolution_orchestrator=evolution_orchestrator,
            tech_watch_collector=tech_watch_collector,
            tech_watch_judge=tech_watch_judge,
            tech_watch_store=tech_watch_store,
            ai_client=ai_client,
            agent_directory=agent_directory,
            process_lifecycle_manager=process_lifecycle_manager,
        )

        # 3. 保留公开 ai_client 引用：AppContext.recreate_ai_client 的声明式
        #    binding 依赖此字段存在才能完成注入（见 update_ai_client）。
        self.ai_client = ai_client

    def update_ai_client(self, new_client: object) -> None:
        """AI 客户端重建后同步更新本 manager 及持有客户端的子组件。

        由 ``apply_ai_client_bindings`` 调用（AIClientConsumer 协议）。
        不同步会导致重建 API Key 后 router / computer_use / react_engine
        继续使用旧客户端。
        """
        self.ai_client = new_client
        router = getattr(self, "router", None)
        if router is not None and hasattr(router, "ai_client"):
            router.ai_client = new_client
        computer_use = getattr(self, "computer_use", None)
        if computer_use is not None and getattr(computer_use, "ai_client", None) is not None:
            computer_use.ai_client = new_client
        react_engine = getattr(self, "react_engine", None)
        if react_engine is not None and getattr(react_engine, "_client", None) is not None:
            react_engine._client = new_client

    # ── 公开 API：薄壳委托给组件 ──────────────────────────────

    def run_goal(
        self,
        goal: str,
        source: str = "assistant.goal",
        delivery_channel: str | None = None,
        chat_session_id: str | None = None,
        cancellation_event: threading.Event | None = None,
    ) -> dict[str, object]:
        self._ensure_planner_ai_client()
        profile_snapshot = self.task_lifecycle.get_user_profile_snapshot()
        steps = self.planner.plan_goal(goal, user_profile=profile_snapshot)
        steps = self.decision_optimizer.optimize_decision_path(goal, steps, profile_snapshot)
        result = self.task_lifecycle.execute_and_record(
            goal=goal,
            steps=steps,
            source=source,
            executor=self.executor,
            authorize_callback=self.security.authorize_step,
            delivery_channel=delivery_channel,
            profile_snapshot=profile_snapshot,
            chat_session_id=chat_session_id,
            cancellation_event=cancellation_event,
        )
        self.decision_optimizer.record_decision(goal, steps, result, profile_snapshot)
        return result

    def dispatch_user_input(
        self,
        text: str,
        *,
        source: str = "assistant.goal",
        delivery_channel: str | None = None,
        chat_session_id: str | None = None,
        cancellation_event: threading.Event | None = None,
    ) -> DispatchResult:
        """统一入口：chat/execute/automate 三合一分类（委托给 AutonomyDispatcher）。"""
        return self.dispatcher.dispatch_user_input(
            text,
            source=source,
            delivery_channel=delivery_channel,
            chat_session_id=chat_session_id,
            cancellation_event=cancellation_event,
        )

    # ── Computer Use 关键字（保留为类属性以兼容旧调用方） ─────
    # 实际定义位于 AutonomyDispatcher，这里仅做别名以维持向后兼容
    @property
    def COMPUTER_USE_KEYWORDS(self) -> frozenset[str]:
        return self.dispatcher.COMPUTER_USE_KEYWORDS

    @property
    def COMPUTER_USE_TRIGGERS(self) -> frozenset[str]:
        return self.dispatcher.COMPUTER_USE_TRIGGERS

    def _is_computer_use_goal(self, text: str) -> bool:
        """保留供旧调用方 / 测试使用；新代码路径请使用 ExecutionRouter。"""
        return self.dispatcher._is_computer_use_goal(text)

    def _handle_execute(
        self,
        text: str,
        classification: object,
        *,
        source: str = "assistant.goal",
        delivery_channel: str | None = None,
        chat_session_id: str | None = None,
        cancellation_event: threading.Event | None = None,
    ) -> DispatchResult:
        """处理 EXECUTE 意图（委托给 dispatcher）。"""
        return self.dispatcher._handle_execute(
            text,
            classification,
            source=source,
            delivery_channel=delivery_channel,
            chat_session_id=chat_session_id,
            cancellation_event=cancellation_event,
        )

    def _handle_automate(self, text: str, classification: object) -> DispatchResult:
        """处理 AUTOMATE 意图（委托给 dispatcher）。"""
        return self.dispatcher._handle_automate(text, classification)

    @staticmethod
    def _is_simple_chat_question(text: str) -> bool:
        """判断是否为简单聊天消息（委托给统一 chat_detector）。"""
        from .chat_detector import is_chat_only as _is_chat_only_unified

        return _is_chat_only_unified(text)

    def _record_judge_decision(self, text: str, judge_result: dict) -> None:
        """记录 Judge 路由决策（委托给 telemetry 组件）。"""
        self.telemetry.record_judge_decision(text, judge_result)

    def _handle_chat(self, text: str, classification: object) -> DispatchResult:
        """处理 CHAT 意图（委托给 dispatcher）。"""
        return self.dispatcher._handle_chat(text, classification)

    def plan_goal(self, goal: str) -> list[PlannedStep]:
        self._ensure_planner_ai_client()
        return self.planner.plan_goal(goal)

    def plan_codex_goal(self, goal: str, workspace_context: dict[str, object] | None = None) -> dict[str, object]:
        self._ensure_planner_ai_client()
        return self.codex.plan(goal, workspace_context=workspace_context)

    def run_codex_goal(
        self,
        goal: str,
        approved_actions: set | None = None,
        force: bool = False,
        options: dict[str, object] | None = None,
    ) -> dict[str, object]:
        self._ensure_planner_ai_client()
        return self.codex.run(goal, approved_actions=approved_actions, force=force, options=options)

    def list_codex_runs(self, limit: int = 20) -> list[dict[str, object]]:
        return self.codex.list_runs(limit=limit)

    def pre_check_steps(self, steps: list[PlannedStep]) -> list[dict[str, object]]:
        results = []
        for step in steps:
            auth = self.security.authorize_step(step, source="assistant.precheck", consume_quota=False)
            results.append({"step_id": step.step_id, "action_id": step.action_id, "auth": auth})
        return results

    def run_steps_approved(
        self,
        steps: list[PlannedStep],
        approved_actions: set,
        goal: str = "",
        chat_session_id: str | None = None,
        cancellation_event: threading.Event | None = None,
    ) -> dict[str, object]:
        def _authorize_with_approval(step, source="assistant.plan"):
            return self.security.authorize_step(step, source=source, pre_approved=approved_actions)

        profile_snapshot = self.task_lifecycle.get_user_profile_snapshot()
        return self.task_lifecycle.execute_and_record(
            goal=goal or "approved_execution",
            steps=steps,
            source="assistant.approved",
            executor=self.executor,
            authorize_callback=_authorize_with_approval,
            profile_snapshot=profile_snapshot,
            chat_session_id=chat_session_id,
            cancellation_event=cancellation_event,
        )

    def _ensure_planner_ai_client(self) -> None:
        if getattr(self.planner, "_ai_client", None) is not None:
            return
        try:
            from services.ai_service import OpenAICompatibleClient, resolve_openai_compatible_config

            config = resolve_openai_compatible_config(settings_repository=self.settings_repository)
            if config.api_key and config.base_url:
                client = OpenAICompatibleClient(
                    api_key=config.api_key,
                    api_base=config.base_url,
                    model=config.model,
                    timeout=config.timeout,
                )
                self.planner.set_ai_client(client)
                logger.info("Planner AI client 已连接: %s", config.base_url)
        except Exception as exc:
            logger.warning("无法为 Planner 创建 AI client，将使用关键词兜底: %s", exc)
            if self.observability_service:
                self.observability_service.track_exception("assistant.planner_client_failed", exc)

    def run_system_event(self, event_name: str, payload: dict[str, object] | None = None) -> dict[str, object]:
        """触发系统事件（委托给 scheduler 组件）。"""
        return self.scheduler.run_system_event(event_name, payload)

    def run_due_schedules(self) -> list[dict[str, object]]:
        """扫描并执行到期事件（委托给 scheduler 组件）。"""
        return self.scheduler.run_due_schedules()

    def suggest_upgrade_policy(self) -> dict[str, object]:
        channel = str(self.settings_repository.get_setting("assistant_upgrade_channel", "stable") or "stable")
        allow_self_update = bool(self.settings_repository.get_setting(KEY_ASSISTANT_ALLOW_SELF_UPDATE, False))
        return {
            "channel": channel,
            "allow_self_update": allow_self_update,
            "allowed_update_actions": ["rule_update", "config_update", "plugin_install"],
            "policy": "自升级只允许规则更新、配置调整和插件安装；核心 Python 文件保持人工审批 + 回滚点。",
        }

    def list_audit_entries(self, limit: int = 100, action_id: str = "", status: str = ""):
        return self.audit_trail.list_entries(limit=limit, action_id=action_id, status=status)

    def rollback_entry(self, entry_id: str):
        return self.rollback_manager.rollback(entry_id)

    def list_rollbackable(self, limit: int = 50):
        return self.audit_trail.list_rollbackable(limit=limit)

    def get_permission_matrix(self):
        return self.permission_matrix.get_matrix()

    def update_permission(self, action_id: str, updates: dict[str, object]) -> bool:
        return self.permission_matrix.update_entry(action_id, updates)

    def get_remaining_quota(self, action_id: str) -> int:
        return self.permission_matrix.get_remaining_quota(action_id)

    def list_rules(self):
        return self.rule_center.list_rules()

    def add_rule(self, **kwargs):
        return self.rule_center.add_rule(**kwargs)

    def update_rule(self, rule_id: str, updates: dict[str, object]) -> bool:
        return self.rule_center.update_rule(rule_id, updates)

    def remove_rule(self, rule_id: str) -> bool:
        return self.rule_center.remove_rule(rule_id)

    def toggle_rule(self, rule_id: str, enabled: bool | None = None) -> bool:
        return self.rule_center.toggle_rule(rule_id, enabled)

    # ═══════════════════════════════════════════════════════════
    # MCP-5: 任务状态机 + Checkpoint 持久化公开 API
    # ═══════════════════════════════════════════════════════════

    def plan_and_execute(
        self,
        goal: str,
        *,
        source: str = "assistant.goal",
        confirm_before_execute: bool = True,
        authorized_actions: set[str] | None = None,
    ) -> dict[str, object]:
        """一体化：规划 → （可选确认） → 执行。"""
        self._ensure_planner_ai_client()
        return self.task_manager.plan_and_execute(
            goal,
            source=source,
            confirm_before_execute=confirm_before_execute,
            authorized_actions=authorized_actions,
        )

    def execute_transactional(
        self,
        goal: str,
        steps: list[PlannedStep],
        *,
        file_paths: list[str] | None = None,
        authorized_actions: set[str] | None = None,
    ) -> dict[str, object]:
        """以原子事务方式执行步骤。"""
        profile_snapshot = self.task_lifecycle.get_user_profile_snapshot()
        task = self.task_lifecycle.build_agent_task(
            goal,
            source="assistant.transactional",
            profile_snapshot=profile_snapshot,
        )
        task.set_steps(steps)
        task.is_transactional = True

        if authorized_actions is not None:

            def _auth(step, _source="assistant.transactional"):
                return self.security.authorize_step(step, source=_source, pre_approved=authorized_actions)
        else:
            _auth = self.security.authorize_step

        return self.task_manager.execute_transactional(
            task,
            steps,
            file_paths=file_paths,
            authorize_callback=_auth,
            user_profile=profile_snapshot,
        )

    def generate_plan(
        self,
        goal: str,
        steps: list[PlannedStep],
        *,
        reasoning: str = "",
    ) -> dict[str, object]:
        """生成执行计划摘要。"""
        plan = self.task_manager.generate_plan(goal, steps, reasoning=reasoning)
        return plan.get_plan_summary()

    def confirm_plan(
        self,
        plan_id: str,
        *,
        approved: bool = True,
        disabled_steps: list[str] | None = None,
        reorder: list[str] | None = None,
        reject_reason: str = "",
    ) -> dict[str, object] | None:
        """用户确认/编辑/拒绝计划。"""
        if self.plan_store is None:
            return None
        data = self.plan_store.load_plan(plan_id)
        if data is None:
            return None
        # 重建 ExecutionPlan 对象
        from .execution_plan import ExecutionPlan

        plan = ExecutionPlan(
            plan_id=data["plan_id"],
            goal=data["goal"],
            source=data.get("source", "judge"),
            reasoning=data.get("reasoning", ""),
        )
        for s in data.get("steps", []):
            plan.add_step(
                step_id=s["step_id"],
                action_id=s["action_id"],
                description=s.get("description", ""),
                risk_level=s.get("risk_level", "low"),
                estimated_seconds=s.get("estimated_seconds"),
                file_paths=s.get("file_paths", []),
            )
        self.task_manager.confirm_plan(
            plan,
            approved=approved,
            disabled_steps=disabled_steps,
            reorder=reorder,
            reject_reason=reject_reason,
        )
        return plan.get_plan_summary()

    def scan_unfinished_tasks(self) -> list[dict[str, object]]:
        """扫描所有未完成的任务 checkpoint。"""
        return self.task_manager.get_unfinished_tasks_info()

    def recover_task(self, task_id: str, *, confirmed: bool = True) -> dict[str, object]:
        """从 checkpoint 恢复任务执行。"""
        return self.task_manager.recover_task(task_id, confirmed=confirmed)

    def get_execution_history(
        self,
        *,
        task_id: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict[str, object]]:
        """查询执行历史。"""
        return self.task_manager.get_history(task_id=task_id, limit=limit, offset=offset)

    def get_replay_data(self, record_id: str) -> dict[str, object] | None:
        """获取执行重放数据。"""
        return self.task_manager.get_replay_data(record_id)

    def rollback_execution(self, record_id: str) -> dict[str, object]:
        """回滚指定执行记录。"""
        return self.task_manager.rollback_execution(record_id)

    def cleanup_checkpoints(self, *, before_days: int | None = None) -> int:
        """归档旧 checkpoint。"""
        return self.task_manager.cleanup_checkpoints(before_days=before_days)

    # ═══════════════════════════════════════════════════════════
    # MCP-13: Judge 意图深度 + Agent 链编排（实现在 judge_mixin.py）
    # ═══════════════════════════════════════════════════════════

    # 兼容别名：旧调用方可能直接访问 manager.COMPUTER_USE_*
    # （已通过 @property 委托给 dispatcher，无需在此重复定义）

    # ── 测试兼容：__new__ 跳过 __init__ 时懒创建组件 ──────────
    def __getattr__(self, name: str):
        """``__new__`` 构造的测试实例没有走 ``__init__``，访问 ``dispatcher`` 等
        组件属性时懒创建并缓存，保持测试契约不变。

        注意：``__getattr__`` 只在常规属性查找失败时调用，正常 ``__init__`` 路径
        不会触发，零开销。
        """
        if name == "dispatcher":
            d = AutonomyDispatcher(self)
            object.__setattr__(self, name, d)
            return d
        if name == "decision_optimizer":
            d = AutonomyDecisionOptimizer(self)
            object.__setattr__(self, name, d)
            return d
        if name == "telemetry":
            d = AutonomyTelemetry(self)
            object.__setattr__(self, name, d)
            return d
        if name == "scheduler":
            d = AutonomyScheduler(self)
            object.__setattr__(self, name, d)
            return d
        raise AttributeError(f"{type(self).__name__!r} object has no attribute {name!r}")


__all__ = [
    "AgentTask",
    "AgentChainEngine",
    "AssistantCapabilityRouter",
    "AssistantEventLayer",
    "AssistantExecutionLayer",
    "AssistantMemoryLayer",
    "AssistantPlannerLayer",
    "AssistantSecurityLayer",
    "AutonomousAssistantManager",
    "CodexMissionOrchestrator",
    "DispatchResult",
    "PlannedStep",
    "TaskManager",
]
