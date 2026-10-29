"""服务注册域：管理 service_registry 的注册生命周期。"""

from __future__ import annotations

from app_runtime.service_registry import service_registry


class ServiceRegistryManager:
    """管理 service_registry 的注册生命周期。

    支持 register_all() 批量注册和 register(key, service) 按需注册。
    所有 service_type 的引用使用惰性 import，避免启动时触发重依赖链。
    """

    def __init__(self) -> None:
        self._registry = service_registry

    def register(self, service_type, instance, name: str | None = None) -> None:
        """按需注册单个服务。"""
        self._registry.register(service_type, instance, name=name)

    def register_all(self, context) -> None:
        """从已初始化的 AppContext 批量注册所有服务。

        所有 service_type 是惰性 import，仅在首次需要时解析。
        """
        from ai.client import JudgeAwareClient
        from api.integration_hub import ApiIntegrationHub
        from assistant.autonomy import AutonomousAssistantManager
        from assistant.inbox import AssistantInboxService
        from assistant.intent_router import AssistantIntentRouter
        from assistant.judge import JudgeScheduler
        from assistant.local_router import LocalAIRouter
        from assistant.runtime import AssistantRuntimeManager
        from assistant.profile.user_profile import UserProfileService
        from core.observability import ObservabilityService
        from cross_device.capability_router import CrossDeviceCapabilityRouter
        from cross_device.session import CrossDeviceSessionManager
        from desktop.doctor_service import DesktopDoctorService
        from evolution.orchestrator import EvolutionOrchestrator
        from memory.decision import DecisionMemory
        from memory.index import AssistantMemoryIndexService
        from plugins.protocol import PluginRegistry
        from services.copilot_engine import CopilotEngine
        from services.data_protection_service import DataProtectionService
        from services.habit_learner import HabitLearner
        from services.reminder import ReminderService
        from services.subagent_service import SubagentManager
        from services.web_fetch import WebFetchService
        from services.web_search import WebSearchService
        from workbench.service import PersonalWorkbenchService

        entries = [
            (JudgeAwareClient, context.ai_client, None),
            (ReminderService, context.reminder_service, None),
            (ObservabilityService, context.observability_service, None),
            (UserProfileService, context.user_profile_service, None),
            (LocalAIRouter, context.local_ai_router, None),
            (AssistantIntentRouter, context.intent_router, None),
            (AssistantInboxService, context.assistant_inbox_service, None),
            (AutonomousAssistantManager, context.autonomous_assistant_manager, None),
            (SubagentManager, context.subagent_manager, None),
            (AssistantRuntimeManager, context.assistant_runtime_manager, None),
            (DataProtectionService, context.data_protection_service, None),
            (PersonalWorkbenchService, context.workbench_service, None),
            (WebSearchService, context.web_search_service, None),
            (WebFetchService, context.web_fetch_service, None),
            (HabitLearner, context.habit_learner, None),
            (DesktopDoctorService, context.desktop_doctor_service, None),
            (ApiIntegrationHub, context.api_integration_hub, None),
            (PluginRegistry, context.plugin_registry, None),
            (CrossDeviceSessionManager, context.cross_device_session, None),
            (CrossDeviceCapabilityRouter, context.cross_device_router, None),
            (DecisionMemory, context.decision_memory, None),
            (EvolutionOrchestrator, context.evolution_orchestrator, None),
            (JudgeScheduler, context.judge_scheduler, "main"),
            (CopilotEngine, context.copilot_engine, None),
            (AssistantMemoryIndexService, context.memory_index_service, None),
        ]
        for service_type, instance, name in entries:
            if instance is not None:
                self._registry.register(service_type, instance, name=name)
