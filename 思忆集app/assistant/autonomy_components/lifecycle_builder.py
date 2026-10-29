"""Composition root for AutonomousAssistantManager."""

from __future__ import annotations

from services.capability_types import CapabilityRegistry
from ..capability_router import AssistantCapabilityRouter
from ..checkpoint import CheckpointManager
from ..codex import CodexMissionOrchestrator
from ..computer_use import ComputerUseEngine
from ..events import AssistantEventLayer
from ..execution import AssistantExecutionLayer
from ..execution_history import AuditLogWriter, ExecutionHistoryStore
from ..execution_plan import ExecutionPlanStore
from ..planner import AssistantPlannerLayer
from ..react_engine import ReActEngine
from ..task_lifecycle import AgentTaskLifecycle
from ..task_manager import TaskManager
from ..security_layer import AssistantSecurityLayer
from memory.layer import AssistantMemoryLayer
from core.audit_rollback import AuditTrail, RollbackManager, WriteGuard
from core.permission_matrix import PermissionMatrix
from services.rule_center import RuleCenter


class AutonomyLifecycleBuilder:
    @staticmethod
    def build_into(manager, **deps) -> None:
        data_store = deps["data_store"]
        settings_repository = deps["settings_repository"]
        observability_service = deps["observability_service"]
        manager.data_store = data_store
        manager.settings_repository = settings_repository
        manager.user_profile_service = deps.get("user_profile_service")
        manager.intent_router = deps.get("intent_router")
        manager.inbox_service = deps.get("inbox_service")
        manager.observability_service = observability_service
        manager.permission_matrix = PermissionMatrix(data_store=data_store, observability_service=observability_service)
        manager.audit_trail = AuditTrail(data_store=data_store, observability_service=observability_service)
        manager.write_guard = WriteGuard(audit_trail=manager.audit_trail, observability_service=observability_service)
        manager.rollback_manager = RollbackManager(
            data_store=data_store,
            audit_trail=manager.audit_trail,
            note_repository=deps["note_repository"],
            task_repository=deps["task_repository"],
            inbox_repository=deps["inbox_repository"],
            observability_service=observability_service,
        )
        manager.rule_center = RuleCenter(data_store=data_store, settings_repository=settings_repository, observability_service=observability_service)
        manager.memory = AssistantMemoryLayer(data_store, observability_service=observability_service)
        manager.security = AssistantSecurityLayer(
            settings_repository=settings_repository,
            observability_service=observability_service,
            integration_hub=deps.get("integration_hub"),
            permission_matrix=manager.permission_matrix,
        )
        manager.capability_registry = CapabilityRegistry()
        for group in CapabilityRegistry.create_default_groups():
            manager.capability_registry.register_group(group)
        manager.planner = AssistantPlannerLayer(settings_repository=settings_repository, user_profile_service=manager.user_profile_service)
        manager.planner.set_capability_registry(manager.capability_registry)
        manager.router = AssistantCapabilityRouter(
            data_store=data_store,
            settings_repository=settings_repository,
            observability_service=observability_service,
            note_repository=deps["note_repository"],
            task_repository=deps["task_repository"],
            news_repository=deps.get("news_repository"),
            inbox_repository=deps["inbox_repository"],
            integration_hub=deps.get("integration_hub"),
            mcp_manager=deps.get("mcp_manager"),
            write_guard=manager.write_guard,
            capability_registry=manager.capability_registry,
            ai_client=deps.get("ai_client"),
            agent_directory=deps.get("agent_directory"),
            evolution_orchestrator=deps.get("evolution_orchestrator"),
            tech_watch_collector=deps.get("tech_watch_collector"),
            tech_watch_judge=deps.get("tech_watch_judge"),
            tech_watch_store=deps.get("tech_watch_store"),
            # 2026-09-11 修复：memory_index_service 由 AutonomousAssistantManager
            # 从 AppContext 一路转传到这里（autonomy.py:96），却漏传给 router，
            # 致使 router.memory_index_service 恒为 None → memory.recall 直接
            # 返回 memory_index_unavailable（生产 trace.db 实证）。此处补齐装配。
            memory_index_service=deps.get("memory_index_service"),
        )
        manager.executor = AssistantExecutionLayer(
            data_store=data_store,
            capability_router=manager.router,
            observability_service=observability_service,
            settings_repository=settings_repository,
            inbox_service=manager.inbox_service,
        )
        manager.computer_use = ComputerUseEngine(capability_router=manager.router, ai_client=deps.get("ai_client"))
        manager.react_engine = (
            ReActEngine(
                primary_client=deps.get("ai_client"),
                capability_router=manager.router,
                capability_registry=manager.capability_registry,
                max_iterations=8,
            )
            if deps.get("ai_client")
            else None
        )
        manager.events = AssistantEventLayer(
            data_store=data_store,
            settings_repository=settings_repository,
            observability_service=observability_service,
            rule_center=manager.rule_center,
            user_profile_service=manager.user_profile_service,
        )
        manager.codex = CodexMissionOrchestrator(
            data_store=data_store,
            planner=manager.planner,
            executor=manager.executor,
            security=manager.security,
            memory=manager.memory,
            observability_service=observability_service,
            settings_repository=settings_repository,
        )
        manager.checkpoint_manager = CheckpointManager(data_store=data_store)
        manager.history_store = ExecutionHistoryStore(data_store=data_store)
        manager.plan_store = ExecutionPlanStore(data_store=data_store)
        manager.audit_logger = AuditLogWriter(data_store=data_store)
        manager.executor.checkpoint_manager = manager.checkpoint_manager
        manager.task_lifecycle = AgentTaskLifecycle(
            data_store=data_store,
            memory=manager.memory,
            user_profile_service=manager.user_profile_service,
            checkpoint_manager=manager.checkpoint_manager,
            history_store=manager.history_store,
        )
        manager.task_manager = TaskManager(
            data_store=data_store,
            task_lifecycle=manager.task_lifecycle,
            executor=manager.executor,
            planner=manager.planner,
            security=manager.security,
            checkpoint_manager=manager.checkpoint_manager,
            history_store=manager.history_store,
            plan_store=manager.plan_store,
            audit_logger=manager.audit_logger,
            events=manager.events,
        )
