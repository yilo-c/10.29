"""AppContext builder.

P0-10: 把 AppContext.__init__ 中按域拆出的 15 个初始化段
封装到独立 Builder 类，按命名领域阶段顺序执行：

    storage → infrastructure → ai → profile → memory → tooling →
    desktop_services → autonomy → cross_device → cron_jobs →
    bridges → copilot → config → character → lazy_tools → finalize

每个 build_* 方法只在 ``self.ctx``（AppContext 实例）上原地赋值，
不引入任何新属性 / 顺序，从而保留 100% 向后兼容（约 176 个公共属性）。

引入 Builder 的目的：
* 让 __init__ 体只剩"目录"，便于审阅与定位；
* 让单元测试可以拦截 / 替换某一阶段；
* 后续若要拆出 ChatContext / SyncContext 子上下文，可在这里改造。
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Optional

from core.error_grading import (
    ANCHOR_STARTUP_BEGIN,
    ANCHOR_STARTUP_BRIDGE,
    ANCHOR_STARTUP_DB_INIT,
    ANCHOR_STARTUP_OK,
    startup_anchor,
)
from core.observability import SEVERITY_INFO

logger = logging.getLogger("siyi.app_runtime.context_builder")


class AppContextStartupError(RuntimeError):
    """启动期自检失败：关键依赖属性缺失，后端应 fail-fast 而非带病运行。"""


class AppContextBuilder:
    """按领域有序构造 AppContext。

    所有阶段都直接修改传入的 ``ctx``，不返回新对象。调用者负责传入
    一个尚未初始化属性的 ``AppContext`` 实例（即 ``__init__`` 内部的
    ``self``），并依次调用 ``build_all`` 或单个阶段方法。
    """

    def __init__(
        self,
        ctx: Any,
        *,
        alarms: Any = None,
        data_base_dir: Optional[Any] = None,
        desknotes_data_dir: Optional[Any] = None,
        headless: bool = False,
    ) -> None:
        self.ctx = ctx
        self.alarms = alarms
        self.data_base_dir = data_base_dir
        self.desknotes_data_dir = desknotes_data_dir
        self.headless = headless

    # ── 入口 ──────────────────────────────────────────────────────

    def build_all(self) -> None:
        startup_anchor(SEVERITY_INFO, ANCHOR_STARTUP_BEGIN, "AppContext initialization started")
        ctx = self.ctx
        ctx._desknotes_dir = self.desknotes_data_dir
        ctx._headless = self.headless

        self.build_storage()
        self.build_infrastructure()
        self.build_ai()
        self.build_profile()
        self.build_memory()
        self.build_tooling()
        self.build_desktop_services()
        self.build_autonomy()
        self.build_cross_device()
        self.build_cron_jobs()
        self.build_bridges()
        self.build_copilot()
        self.build_config()
        self.build_character()
        self.build_lazy_tools()
        self.finalize()

        startup_anchor(SEVERITY_INFO, ANCHOR_STARTUP_OK, "AppContext fully initialized")

    # ── 1. 数据存储 ──────────────────────────────────────────────

    def build_storage(self) -> None:
        from data.app_storage_context import StorageContext

        ctx = self.ctx
        ctx._storage = StorageContext(self.data_base_dir, desknotes_data_dir=self.desknotes_data_dir)
        s = ctx._storage
        ctx.local_data_store = s.local_data_store
        ctx.personal_task_repository = s.personal_task_repository
        ctx.personal_note_repository = s.personal_note_repository
        ctx.translation_history_repository = s.translation_history_repository
        ctx.vocabulary_repository = s.vocabulary_repository
        ctx.settings_repository = s.settings_repository
        ctx.reminder_repository = s.reminder_repository
        ctx.personal_inbox_repository = s.personal_inbox_repository
        ctx.improvement_proposal_repository = s.improvement_proposal_repository
        ctx.chat_session_repository = s.chat_session_repository
        ctx.info_item_repository = s.info_item_repository
        startup_anchor(SEVERITY_INFO, ANCHOR_STARTUP_DB_INIT, "storage context ready")

        # P1.8: 初始化全局默认 SecretResolver（让所有调用方共享 settings_repository + data_dir）
        # 这样 GenericToolBridge._default_secret_resolver / web_bridge._decrypt_if_secure 等
        # 使用 get_default_resolver() 的位置也能完成 settings 加密 token 的解密 + 迁移。
        try:
            from core.secret_resolver import SecretResolver, set_default_resolver

            data_dir = (
                ctx.local_data_store.base_dir if hasattr(ctx.local_data_store, "base_dir") and ctx.local_data_store.base_dir else self.data_base_dir
            )
            set_default_resolver(
                SecretResolver(
                    settings_repository=ctx.settings_repository,
                    data_dir=data_dir,
                )
            )
        except Exception:
            logger.exception("SecretResolver initialization failed; falling back to env-only resolution")

    # ── 2. 基础设施 ──────────────────────────────────────────────

    def build_infrastructure(self) -> None:
        from app_runtime.providers import InfrastructureProvider

        ctx = self.ctx
        ctx._infra = InfrastructureProvider(
            local_data_store=ctx.local_data_store,
            settings_repository=ctx.settings_repository,
            alarms=self.alarms,
            personal_note_repository=ctx.personal_note_repository,
            personal_task_repository=ctx.personal_task_repository,
            personal_inbox_repository=ctx.personal_inbox_repository,
            reminder_repository=ctx.reminder_repository,
        )
        infra = ctx._infra
        ctx.reminder_service = infra.reminder_service
        ctx.data_protection_service = infra.data_protection_service
        ctx.observability_service = infra.observability_service
        ctx.behavior_logger = infra.behavior_logger
        ctx.file_watcher = infra.file_watcher
        ctx.audit_trail = infra.audit_trail
        ctx.rollback_manager = infra.rollback_manager
        ctx.event_bus = infra.event_bus
        # #29: 把 EventBus 注入底层 SQLite 存储，使锁等待告警能进入通知管道
        sqlite_kv = getattr(ctx.local_data_store, "_sqlite", None)
        if sqlite_kv is not None and ctx.event_bus is not None:
            sqlite_kv.set_event_bus(ctx.event_bus)
        ctx.automation_hub_service = infra.automation_hub_service
        ctx.guard_service = infra.guard_service
        ctx.provider_service = infra.provider_service
        ctx.extension_service = infra.extension_service

    # ── 3 + 3.5. AI 与运行时 ─────────────────────────────────────

    def build_ai(self) -> None:
        from ai.client import JudgeAwareClient
        from app_runtime.ai_manager import AiManager
        from app_runtime.service_registry_manager import ServiceRegistryManager
        from assistant.runtime import AssistantRuntimeManager

        ctx = self.ctx
        ctx.ai_manager = AiManager(
            data_store=ctx.local_data_store,
            settings_repository=ctx.settings_repository,
        )
        ctx.cost_tracker = ctx.ai_manager.cost_tracker
        ctx.budget_manager = ctx.ai_manager.budget_manager
        ctx.ai_client = ctx.ai_manager.ai_client
        ctx.local_ai_router = ctx.ai_manager.local_ai_router
        ctx.judge_scheduler = ctx.ai_manager.judge_scheduler
        ctx.service_registry_manager = ServiceRegistryManager()
        ctx.service_registry_manager.register(JudgeAwareClient, ctx.ai_client)

        ctx.assistant_runtime_manager = AssistantRuntimeManager(
            ctx.settings_repository,
            observability_service=ctx.observability_service,
        )

    # ── 4. 用户画像 ──────────────────────────────────────────────

    def build_profile(self) -> None:
        from assistant.profile.user_profile import UserProfileService
        from services.habit_learner import HabitLearner

        ctx = self.ctx
        ctx.user_profile_service = UserProfileService(
            data_store=ctx.local_data_store,
            settings_repository=ctx.settings_repository,
        )
        ctx.habit_learner = HabitLearner(
            data_store=ctx.local_data_store,
            user_profile_service=ctx.user_profile_service,
        )

    # ── 4.5. 记忆系统 ────────────────────────────────────────────

    def build_memory(self) -> None:
        from app_runtime.memory_manager import MemoryManager

        ctx = self.ctx
        ctx.memory_manager = MemoryManager(
            data_store=ctx.local_data_store,
            data_base_dir=self.data_base_dir,
            ai_client=ctx.ai_client,
            settings_repository=ctx.settings_repository,
            user_profile_service=ctx.user_profile_service,
            note_repository=ctx.personal_note_repository,
            task_repository=ctx.personal_task_repository,
            inbox_repository=ctx.personal_inbox_repository,
            behavior_logger=ctx.behavior_logger,
            info_item_repository=ctx.info_item_repository,
            ai_text_service=getattr(ctx, "ai_text_service", None),
        )
        mm = ctx.memory_manager
        ctx.memory_index_service = mm.memory_index_service
        ctx.tag_service = mm.tag_service
        ctx.knowledge_graph_store = mm.knowledge_graph_store
        ctx.knowledge_graph_extractor = mm.knowledge_graph_extractor
        ctx.memory_extractor = mm.memory_extractor
        ctx.memory_scheduler = mm.memory_scheduler
        ctx.memory_linker = mm.memory_linker
        ctx.behavior_memory_extractor = mm.behavior_memory_extractor

        # P1.7-3: 初始化 NarrativeInjector（用于 chat.send 注入记忆叙事）
        try:
            from memory.narrative.narrative_store import NarrativeStore
            from memory.narrative.narrative_injector import NarrativeInjector

            narrative_dir = (
                ctx.local_data_store.base_dir if hasattr(ctx.local_data_store, "base_dir") and ctx.local_data_store.base_dir else self.data_base_dir
            )
            narrative_store = NarrativeStore(data_dir=narrative_dir or "")
            ctx.narrative_injector = NarrativeInjector(narrative_store=narrative_store)
        except Exception:
            logger.exception("NarrativeInjector initialization failed; chat.send will run without narrative injection")
            ctx.narrative_injector = None

    # ── 5 + 6. 工具 / 生命周期 / Cron ────────────────────────────

    def build_tooling(self) -> None:
        from app_runtime.providers._tooling_provider import ToolingProvider
        from services.cron_service import CronService
        from services.info_source_registry import InfoSourceRegistry

        ctx = self.ctx
        # R2-A1：资讯源注册表单例装配（此前全仓无任何地方给 context 赋该属性，
        # 导致 backend/handlers/_info_sources.py 与 InfoCollectorService 各自新建
        # 实例，info.sources.* RPC 的用户配置无法被采集器/cron 消费）。
        # 与 settings 仓储联动：持久化到 settings 的开关/自定义源对所有消费点可见。
        ctx.info_source_registry = InfoSourceRegistry(
            settings_repository=ctx.settings_repository,
        )
        ctx._tools = ToolingProvider(
            local_data_store=ctx.local_data_store,
            settings_repository=ctx.settings_repository,
            ai_client=ctx.ai_client,
            observability_service=ctx.observability_service,
            personal_note_repository=ctx.personal_note_repository,
            personal_task_repository=ctx.personal_task_repository,
            translation_history_repository=ctx.translation_history_repository,
            vocabulary_repository=ctx.vocabulary_repository,
            personal_inbox_repository=ctx.personal_inbox_repository,
            improvement_proposal_repository=ctx.improvement_proposal_repository,
            get_tech_watch_db_path=ctx._storage.get_tech_watch_db_path,
            create_hub_client=ctx.create_automation_hub_client,
        )
        t = ctx._tools
        ctx.tech_watch_store = t.tech_watch_store
        ctx.web_search_service = t.web_search_service
        ctx.web_fetch_service = t.web_fetch_service
        ctx.tech_watch_collector = t.tech_watch_collector
        ctx.tech_watch_judge = t.tech_watch_judge
        ctx.api_integration_hub = t.api_integration_hub
        ctx.api_provider_registry = t.api_provider_registry
        ctx.youdao_oauth_provider = t.youdao_oauth_provider
        ctx.api_sync_orchestrator = t.api_sync_orchestrator
        ctx.lifecycle_recovery_service = t.lifecycle_recovery_service
        ctx.process_lifecycle_manager = t.process_lifecycle_manager
        t.run_recovery()

        ctx.cron_service = CronService(
            data_store=ctx.local_data_store,
            observability_service=ctx.observability_service,
            executor=ctx._execute_cron_action,
            settings_repository=ctx.settings_repository,
            event_bus=ctx.event_bus,
        )

    # ── 7. 桌面窗口服务 ──────────────────────────────────────────

    def build_desktop_services(self) -> None:
        from desktop.doctor_service import DesktopDoctorService
        from plugins.protocol import PluginRegistry
        from services.inbox_capture_service import InboxCaptureService
        from services.private_space_service import PrivateSpaceService
        from workbench.service import PersonalWorkbenchService

        ctx = self.ctx
        ctx.plugin_registry = PluginRegistry()
        self._bridge_lifecycle_plugins(ctx)
        ctx.desktop_doctor_service = DesktopDoctorService(
            data_store=ctx.local_data_store,
            settings_repository=ctx.settings_repository,
            app_context=ctx,
            plugin_registry=ctx.plugin_registry,
        )
        ctx.workbench_service = PersonalWorkbenchService(
            ctx.settings_repository,
            ctx.personal_note_repository,
            ctx.personal_inbox_repository,
            ctx.improvement_proposal_repository,
        )
        ctx.inbox_capture_service = InboxCaptureService(
            workbench_service=ctx.workbench_service,
            inbox_repository=ctx.personal_inbox_repository,
            observability_service=ctx.observability_service,
            ai_client=ctx.ai_client,
            settings_repository=ctx.settings_repository,
            ai_text_service=getattr(ctx, "ai_text_service", None),
        )
        ctx.private_space_service = PrivateSpaceService(
            data_store=ctx.local_data_store,
            note_repository=ctx.personal_note_repository,
            task_repository=ctx.personal_task_repository,
        )

    def _bridge_lifecycle_plugins(self, ctx: Any) -> None:
        """打通插件三体系：生命周期 → 扩展点 → 服务注册。

        生命周期版 PluginRegistry（plugins/registry.py）此前从未被接线。
        这里在启动装配时：实例化生命周期注册表 → 扫描插件目录 discover →
        逐个 enable → 桥接为已接线的 ExtensionPointRegistry 扩展点 spec。
        任意一步失败只告警，不影响主流程启动。
        """
        try:
            from plugins.registry import get_lifecycle_registry

            lifecycle = get_lifecycle_registry()
            plugins = lifecycle.list_plugins()
            if not plugins:
                logger.debug("未发现可加载的 lifecycle 插件（plugins/installed 为空）")
                return
            for plugin in plugins:
                plugin_id = plugin.manifest.id
                try:
                    lifecycle.enable(plugin_id)
                except Exception as exc:
                    logger.warning("插件 enable 失败 %s: %s", plugin_id, exc)
                try:
                    ctx.plugin_registry.register_lifecycle_plugin(plugin)
                except Exception as exc:
                    logger.warning("插件桥接扩展点失败 %s: %s", plugin_id, exc)
        except Exception as exc:
            logger.warning("插件三体系接线失败（不影响主流程）: %s", exc)

    # ── 8 + 8.5. 自主助手 + Agent 目录 ──────────────────────────

    def build_autonomy(self) -> None:
        from assistant.autonomy import AutonomousAssistantManager
        from assistant.bridging.cc_bridge import CCBridge
        from services.effort_estimator import EffortEstimator
        from assistant.inbox import AssistantInboxService
        from assistant.intent_router import AssistantIntentRouter
        from services.path_comparator import PathComparator
        from assistant.source_registry import AssistantSourceRegistry
        from assistant.task_optimizer import TaskOptimizer
        from evolution.orchestrator import EvolutionOrchestrator
        from memory.decision import DecisionMemory
        from orchestration.setup import create_default_directory
        from services.ai_service import create_fallback_client
        from services.info_radar import InfoRadar
        from services.subagent_service import SubagentManager
        from services.suggestion_engine import SuggestionEngine

        ctx = self.ctx
        ctx.assistant_inbox_service = AssistantInboxService(
            data_store=ctx.local_data_store,
            user_profile_service=ctx.user_profile_service,
        )
        ctx.intent_router = AssistantIntentRouter(
            ai_client=ctx.ai_client,
            user_profile_service=ctx.user_profile_service,
        )
        ctx.source_registry = AssistantSourceRegistry(
            data_store=ctx.local_data_store,
            user_profile_service=ctx.user_profile_service,
        )
        ctx.decision_memory = DecisionMemory(data_store=ctx.local_data_store)
        ctx.effort_estimator = EffortEstimator()
        ctx.task_optimizer = TaskOptimizer()
        ctx.task_optimizer.set_speed_estimator(ctx.effort_estimator)
        ctx.path_comparator = PathComparator()
        ctx.evolution_orchestrator = EvolutionOrchestrator(
            settings_repository=getattr(ctx, "settings_repository", None),
        )
        ctx.suggestion_engine = SuggestionEngine(
            user_profile_service=ctx.user_profile_service,
            note_repository=ctx.personal_note_repository,
            task_repository=ctx.personal_task_repository,
            evolution_orchestrator=ctx.evolution_orchestrator,
            ai_client=ctx.ai_client,
            event_bus=ctx.event_bus,
        )
        ctx.info_radar = InfoRadar(
            user_profile_service=ctx.user_profile_service,
            web_search_service=ctx.web_search_service,
        )
        ctx.subagent_manager = SubagentManager(
            data_store=ctx.local_data_store,
            settings_repository=ctx.settings_repository,
            observability_service=ctx.observability_service,
            ai_client_factory=lambda: create_fallback_client(
                settings_repository=ctx.settings_repository,
                cache_path=(
                    str(Path(ctx.local_data_store.base_dir).joinpath("ai_cache.json"))
                    if hasattr(ctx.local_data_store, "base_dir") and ctx.local_data_store.base_dir
                    else None
                ),
            ),
            note_repository=ctx.personal_note_repository,
            inbox_repository=ctx.personal_inbox_repository,
            memory_index_service=ctx.memory_index_service,
        )
        # 多 Agent 协作编排器（按需 import 避免循环依赖）
        from assistant.multi_agent_orchestrator import MultiAgentOrchestrator

        ctx.multi_agent_orchestrator = MultiAgentOrchestrator(ctx)
        ctx.cc_bridge = CCBridge(
            settings_repository=ctx.settings_repository,
            memory_service=ctx.memory_index_service,
            working_dir=(ctx.local_data_store.base_dir if hasattr(ctx.local_data_store, "base_dir") else None),
        )
        ctx._kb_bridge = None  # 延迟初始化：避免 transformers 导入阻塞

        ctx.agent_directory = create_default_directory(
            settings_repository=ctx.settings_repository,
            memory_service=ctx.memory_index_service,
            working_dir=(ctx.local_data_store.base_dir if hasattr(ctx.local_data_store, "base_dir") else None),
            data_store=ctx.local_data_store,
            observability_service=ctx.observability_service,
            settings_repository_for_codex=ctx.settings_repository,
        )

        ctx.autonomous_assistant_manager = AutonomousAssistantManager(
            data_store=ctx.local_data_store,
            settings_repository=ctx.settings_repository,
            observability_service=ctx.observability_service,
            note_repository=ctx.personal_note_repository,
            task_repository=ctx.personal_task_repository,
            inbox_repository=ctx.personal_inbox_repository,
            integration_hub=ctx.api_integration_hub,
            sync_orchestrator=ctx.api_sync_orchestrator,
            subagent_manager=ctx.subagent_manager,
            memory_index_service=ctx.memory_index_service,
            user_profile_service=ctx.user_profile_service,
            intent_router=ctx.intent_router,
            inbox_service=ctx.assistant_inbox_service,
            source_registry=ctx.source_registry,
            evolution_orchestrator=ctx.evolution_orchestrator,
            tech_watch_collector=ctx.tech_watch_collector,
            tech_watch_judge=ctx.tech_watch_judge,
            tech_watch_store=ctx.tech_watch_store,
            ai_client=ctx.ai_client,
            agent_directory=ctx.agent_directory,
            process_lifecycle_manager=ctx.process_lifecycle_manager,
        )
        manager = ctx.autonomous_assistant_manager
        manager.task_optimizer = ctx.task_optimizer
        manager.effort_estimator = ctx.effort_estimator
        manager.decision_memory = ctx.decision_memory
        manager.cost_tracker = ctx.cost_tracker
        manager.budget_manager = ctx.budget_manager
        manager.path_comparator = ctx.path_comparator
        manager.router.path_comparator = ctx.path_comparator
        manager.router.cost_tracker = ctx.cost_tracker
        manager.router.budget_manager = ctx.budget_manager
        manager.router.cc_bridge = ctx.cc_bridge
        # P0.2: 注入桌面服务到 router，供 ProactiveTriggerEngine 使用
        manager.router.desktop_doctor_service = getattr(ctx, "desktop_doctor_service", None)
        manager.router.workbench_service = getattr(ctx, "workbench_service", None)
        # weather_service 是惰性 property，直接访问即可
        manager.router.weather_service = getattr(ctx, "weather_service", None)
        # 设为 None 避免此时触发 transformers 导入
        manager.router.knowledge_base_bridge = ctx._kb_bridge
        # 本地资源工具（vocab.* / knowledge.query）依赖：适配层懒读取，
        # 此处构建后注入即可生效
        manager.router.vocabulary_repository = getattr(ctx, "vocabulary_repository", None)
        manager.router.knowledge_graph_store = getattr(ctx, "knowledge_graph_store", None)
        manager.router.info_item_repository = getattr(ctx, "info_item_repository", None)
        # R2-A1：把共享资讯源注册表注入 router，使 workflow.publish_article /
        # info.collect 等 router 侧采集路径与 info.sources.* RPC 看到同一份配置。
        manager.router.info_source_registry = ctx.info_source_registry
        ctx.subagent_manager.capability_executor = manager.router.knowledge_domain.execute_subagent_steps

    # ── 9. 跨设备 ────────────────────────────────────────────────

    def build_cross_device(self) -> None:
        from app_runtime.cross_device_manager import CrossDeviceManager

        ctx = self.ctx
        ctx.cross_device_manager = CrossDeviceManager(
            data_store=ctx.local_data_store,
            sync_orchestrator=ctx.api_sync_orchestrator,
        )
        ctx.cross_device_session = ctx.cross_device_manager.session
        ctx.cross_device_router = ctx.cross_device_manager.router

        manager = ctx.autonomous_assistant_manager
        manager.router.cross_device_session = ctx.cross_device_session
        manager.router.cross_device_router = ctx.cross_device_router
        manager.router.attachment_layer = ctx.attachment_layer
        if hasattr(manager.router, "capability_registry") and manager.router.capability_registry is not None:
            ctx.cross_device_router.set_capability_registry(manager.router.capability_registry)

    # ── 10. Cron Jobs ────────────────────────────────────────────

    def build_cron_jobs(self) -> None:
        ctx = self.ctx
        ctx._init_cron_jobs()
        manager = ctx.autonomous_assistant_manager
        if hasattr(manager.router, "set_cron_service"):
            manager.router.set_cron_service(ctx.cron_service)
        # P2-9: 把 CronService 注入 TaskManager，实现任务定时调度桥接
        if hasattr(manager, "task_manager") and manager.task_manager is not None:
            manager.task_manager._cron_service = ctx.cron_service

    # ── 11. 桥接服务 ─────────────────────────────────────────────

    def build_bridges(self) -> None:
        from browser.assistant_bridge import BrowserAssistantBridgeService

        ctx = self.ctx
        ctx.browser_assistant_bridge = BrowserAssistantBridgeService(
            settings_repository=ctx.settings_repository,
            observability_service=ctx.observability_service,
            workbench_service=ctx.workbench_service,
            inbox_capture_service=ctx.inbox_capture_service,
            subagent_manager=ctx.subagent_manager,
            memory_index_service=ctx.memory_index_service,
            note_repository=ctx.personal_note_repository,
            task_repository=ctx.personal_task_repository,
            inbox_repository=ctx.personal_inbox_repository,
            proposal_repository=ctx.improvement_proposal_repository,
            translation_repository=ctx.translation_history_repository,
            ai_service=ctx.ai_client,
            behavior_logger=ctx.behavior_logger,
            memory_extractor=ctx.memory_extractor,
            chat_session_repository=ctx.chat_session_repository,
            vocabulary_repository=ctx.vocabulary_repository,
        )
        startup_anchor(SEVERITY_INFO, ANCHOR_STARTUP_BRIDGE, "browser bridge service created")
        if not self.headless:
            from desktop.web_bridge import _create_real_services
            from services.web_bridge_service import WebBridgeService

            ctx.web_bridge_service = WebBridgeService(services=_create_real_services(ctx))
        else:
            ctx.web_bridge_service = None

    # ── 12. CopilotEngine ────────────────────────────────────────

    def build_copilot(self) -> None:
        from core.events import event_bus
        from services.copilot_engine import CopilotEngine

        ctx = self.ctx
        ctx.copilot_engine = CopilotEngine(
            event_bus=event_bus,
            ai_client=ctx.ai_client,
            capability_router=ctx.intent_router,
            settings_repo=ctx.settings_repository,
        )
        ctx.copilot_engine.register_event_handlers()

    # ── 13. Config ───────────────────────────────────────────────

    def build_config(self) -> None:
        from app_runtime.config_manager import ConfigManager

        ctx = self.ctx
        ctx.config_manager = ConfigManager(
            settings_repository=ctx.settings_repository,
            desktop_doctor_service=ctx.desktop_doctor_service,
            plugin_registry=ctx.plugin_registry,
        )

    # ── 14. 角色状态 ─────────────────────────────────────────────

    def build_character(self) -> None:
        from assistant.character_runtime import CharacterRenderer, EmotionState

        ctx = self.ctx
        ctx.character_renderer = CharacterRenderer()
        ctx.current_emotion = EmotionState.neutral()

    # ── 14.5. 工具服务占位符（惰性初始化）─────────────────────────

    def build_lazy_tools(self) -> None:
        ctx = self.ctx
        ctx._weather_service = None
        ctx._youdao_translate_api = None
        ctx._translate_service = None
        ctx._mobile_bridge_manager = None
        # P3.2: 预留 P2.8/P2.9 服务槽位，由 headless_main.py 启动时显式注入
        # （与 weather_service 等懒加载模式一致；避免 handler 路径 setattr 凭空冒出）
        ctx._feedback_service = None
        ctx._notification_dispatcher = None
        ctx._agent_gateway = None  # M1: AgentGateway 执行内核端口（headless_main.py 显式注入）

    # ── 15. 终态注册 ─────────────────────────────────────────────

    def finalize(self) -> None:
        from services.copilot_engine import CopilotEngine

        ctx = self.ctx
        ctx.service_registry_manager.register_all(ctx)
        # CopilotEngine 单独注册以保持顺序清晰
        ctx.service_registry_manager.register(CopilotEngine, ctx.copilot_engine)
        # 启动期 fail-fast 自检（Task #6）：关键属性缺失立即失败，
        # 避免像 P0-2 那样把 AttributeError 推迟到首个 cron tick 才暴露。
        self.validate_critical_attributes()

    # ── 启动期 fail-fast 自检（Task #6）─────────────────────────────

    # 关键属性白名单：handler / cron / 主动管家路径强依赖的 AppContext 属性。
    # 一旦某依赖被改名或漏赋值，build_all 完成后立即抛 AppContextStartupError，
    # 由 headless_main.py 的未捕获异常触发 fail-fast（见 headless_main.py:106）。
    _CRITICAL_ATTRIBUTES: tuple[str, ...] = (
        "local_data_store",
        "settings_repository",
        "personal_note_repository",
        "personal_task_repository",
        "personal_inbox_repository",
        "event_bus",
        "ai_client",
        "ai_manager",
        "assistant_inbox_service",  # P0-2 根因：cron_actions 曾误用不存在的 inbox_service
        "intent_router",
        "autonomous_assistant_manager",
        "cron_service",
        "plugin_registry",
        "service_registry_manager",
        "memory_index_service",
        "user_profile_service",
    )

    def validate_critical_attributes(self) -> None:
        """校验 build_all 后所有关键属性均已赋值（fail-fast）。

        仅检查非 None 的属性存在性；惰性属性（如 weather_service）不在此列，
        因为它们设计为首次访问时才构造。
        """
        ctx = self.ctx
        missing = [name for name in self._CRITICAL_ATTRIBUTES if not hasattr(ctx, name)]
        if missing:
            raise AppContextStartupError("AppContext 启动自检失败，缺失关键属性: " + ", ".join(missing))
        # 额外校验属性未因赋值 None 而失效（hasattr 对显式 None 仍为 True）
        none_attrs = [name for name in self._CRITICAL_ATTRIBUTES if getattr(ctx, name, None) is None]
        if none_attrs:
            raise AppContextStartupError("AppContext 启动自检失败，关键属性为 None: " + ", ".join(none_attrs))
        logger.info("AppContext 启动自检通过（%d 项关键属性）", len(self._CRITICAL_ATTRIBUTES))
