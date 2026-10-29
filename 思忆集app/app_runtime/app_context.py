from __future__ import annotations

import logging
from typing import Any

from ai.client import JudgeAwareClient
from app_runtime._context_builder import AppContextBuilder
from app_runtime._context_config import ConfigContextMixin
from app_runtime._context_cron import CronContextMixin
from app_runtime._context_memory import MemoryContextMixin
from app_runtime.ai_client_consumers import apply_ai_client_bindings

logger = logging.getLogger("siyi.app_context")


class AppContext(CronContextMixin, MemoryContextMixin, ConfigContextMixin):
    """桌面端共享上下文，集中管理跨窗口复用的服务与状态。

    按领域拆分为子上下文以改善可维护性，所有属性保持向后兼容。
    """

    def __init__(self, alarms=None, data_base_dir=None, desknotes_data_dir=None, headless=False):
        # P0-10：按域构造，所有 self.* 属性赋值在 AppContextBuilder 内完成
        AppContextBuilder(
            self,
            alarms=alarms,
            data_base_dir=data_base_dir,
            desknotes_data_dir=desknotes_data_dir,
            headless=headless,
        ).build_all()
        # P1-4：领域子门面缓存（惰性创建）
        self._domain_facades: dict[str, Any] = {}

    # ── 类型声明：对应属性由 AppContextBuilder.build_all() 在构造期动态注入/惰性初始化 ──

    # 惰性服务 backing（属性 getter 首次访问时初始化；构造期置 None）
    _weather_service: Any | None = None
    _youdao_translate_api: Any | None = None
    _translate_service: Any | None = None
    _job_hunter_service: Any | None = None
    _mobile_bridge_manager: Any | None = None

    # 构造期由 build_all() 注入的服务
    ai_manager: Any = None
    browser_assistant_bridge: Any = None
    file_watcher: Any = None
    judge_scheduler: Any = None
    tag_service: Any = None
    info_source_registry: Any = None

    # ── P1-4：领域子门面（新代码优先经门面取依赖，降低全量上下文耦合） ──

    def _facade(self, key: str, cls):
        facade = self._domain_facades.get(key)
        if facade is None:
            facade = cls(self)
            self._domain_facades[key] = facade
        return facade

    @property
    def chat(self):
        """聊天/对话域门面（ai_client/存储/观测/笔记任务仓储）。"""
        from app_runtime.context_facades import ChatFacade

        return self._facade("chat", ChatFacade)

    @property
    def memory(self):
        """记忆/画像域门面（记忆索引/用户画像/收件箱服务）。"""
        from app_runtime.context_facades import MemoryFacade

        return self._facade("memory", MemoryFacade)

    @property
    def sync(self):
        """同步域门面（云同步桥/同步编排）。"""
        from app_runtime.context_facades import SyncFacade

        return self._facade("sync", SyncFacade)

    @property
    def automation(self):
        """自动化域门面（cron/task_queue/自治管理器/进化编排）。"""
        from app_runtime.context_facades import AutomationFacade

        return self._facade("automation", AutomationFacade)

    # ── 属性 ──

    @property
    def alarms(self):
        return self.reminder_service.list_alarms()

    @alarms.setter
    def alarms(self, value):
        self.reminder_service.replace_alarms(value)

    @property
    def navigation_state(self) -> str:
        if not hasattr(self, "_navigation_state"):
            self._navigation_state = "chat"
        return self._navigation_state

    @navigation_state.setter
    def navigation_state(self, value: str) -> None:
        self._navigation_state = value

    @property
    def attachment_layer(self):
        if self._headless:
            return None
        if not hasattr(self, "_attachment_layer"):
            import importlib

            mod = importlib.import_module("ui.attachment_layer")
            self._attachment_layer = mod.AttachmentLayer(
                sync_orchestrator=self.api_sync_orchestrator,
            )
        return self._attachment_layer

    @attachment_layer.setter
    def attachment_layer(self, value):
        self._attachment_layer = value

    @property
    def knowledge_base_bridge(self):
        """延迟初始化，避免 transformers/sentence-transformers 导入阻塞启动。"""
        if self._kb_bridge is None:
            from knowledge.retrieval.bridge import KnowledgeBaseBridge

            self._kb_bridge = KnowledgeBaseBridge(offline_embedding=True)
        return self._kb_bridge

    @knowledge_base_bridge.setter
    def knowledge_base_bridge(self, value):
        self._kb_bridge = value

    # ── 工厂方法 ──

    def create_automation_hub_client(self, base_url=None, api_token=None, session=None):
        from integrations.automation_hub_client import create_automation_hub_client

        return create_automation_hub_client(
            settings_repository=self.settings_repository,
            base_url=base_url,
            api_token=api_token,
            session=session,
        )

    # ── AI 客户端重建 ──

    def recreate_ai_client(self):
        new_client = self.ai_manager.recreate_ai_client(
            settings_repository=self.settings_repository,
        )
        self.ai_client = new_client
        self.service_registry_manager.register(JudgeAwareClient, new_client)
        self.memory_manager.update_ai_client(new_client)
        # 6 个消费者（intent_router / browser_assistant_bridge /
        # autonomous_assistant_manager / copilot_engine / suggestion_engine /
        # inbox_capture_service）的注入通过声明式 binding 表完成 — 详见
        # app_runtime/ai_client_consumers.py。
        apply_ai_client_bindings(self, new_client)
        # 批5（2026-09-18）provider 写后热生效：缓存过 ai_client 的消费者（当前
        # 为 assistant 的 butler agent 缓存）必须主动失效，否则在 TTL 内仍用旧
        # provider 路由（实测：改 provider 后要重启后端才生效）。
        # 这里只发事件、不 import assistant —— 订阅方在 assistant 层（依赖倒置），
        # 内核契约 I1 的"四区禁止 import assistant"边界不破。
        try:
            from core.events import AppEvent, event_bus

            event_bus.emit(AppEvent(event_type="ai_client.recreated", payload={"source": "AppContext.recreate_ai_client"}))
        except Exception:
            logger.warning("emit ai_client.recreated failed", exc_info=True)

    @property
    def weather_service(self) -> Any | None:
        """天气服务（惰性初始化）。"""
        if self._weather_service is None:
            try:
                from services.weather_service import WeatherService

                self._weather_service = WeatherService(
                    settings_repository=self.settings_repository,
                    default_city="广州",
                    default_city_label="广州",
                )
            except Exception as exc:
                logger.warning("WeatherService init failed: %s", exc)
        return self._weather_service

    @property
    def youdao_translate_api(self) -> Any | None:
        """有道翻译 API（惰性初始化，从设置读取密钥）。"""
        if self._youdao_translate_api is None:
            try:
                from services.translate_service import create_youdao_translate_api

                self._youdao_translate_api = create_youdao_translate_api(
                    settings_repository=self.settings_repository,
                )
            except Exception as exc:
                logger.warning("YoudaoTranslateAPI init failed: %s", exc)
        return self._youdao_translate_api

    @property
    def translate_service(self) -> Any | None:
        """翻译服务（惰性初始化）：本地 AI 优先，有道回退。"""
        if not hasattr(self, "_translate_service") or self._translate_service is None:
            try:
                from services.translate_service import TranslateService

                self._translate_service = TranslateService(
                    ai_client=self.ai_client,
                    youdao_api=self.youdao_translate_api,
                    ai_text_service=getattr(self, "ai_text_service", None),
                )
            except Exception as exc:
                logger.warning("TranslateService init failed: %s", exc)
                self._translate_service = None
        return self._translate_service

    @property
    def job_hunter_service(self) -> Any | None:
        """求职猎手服务（惰性初始化）。"""
        if not hasattr(self, "_job_hunter_service") or self._job_hunter_service is None:
            try:
                from services.job_hunter import JobHunterService

                self._job_hunter_service = JobHunterService(
                    ai_client=self.ai_client,
                    settings_repository=self.settings_repository,
                )
            except Exception as exc:
                logger.warning("JobHunterService init failed: %s", exc)
                self._job_hunter_service = None
        return self._job_hunter_service

    @job_hunter_service.setter
    def job_hunter_service(self, value: Any) -> None:
        self._job_hunter_service = value

    @property
    def mobile_bridge_manager(self) -> Any | None:
        """移动桥接管理器（惰性初始化）。"""
        if self._mobile_bridge_manager is None:
            try:
                from cross_device.mobile_bridge_manager import MobileBridgeManager

                self._mobile_bridge_manager = MobileBridgeManager(
                    settings_repository=self.settings_repository,
                    app_context=self,
                )
            except Exception as exc:
                logger.warning("MobileBridgeManager init failed: %s", exc)
        return self._mobile_bridge_manager

    # ── P3.2: P2.8/P2.9 服务属性（由 headless_main.py 显式注入）──

    @property
    def feedback_service(self) -> Any | None:
        """用户反馈学习服务（P2.8）。

        由 ``headless_main.py`` 启动时显式注入；handler 通过此属性访问，
        避免在 backend 层函数内 import + 构造（违反分层）。

        ``_feedback_service`` 由 ``AppContextBuilder.build_lazy_tools`` 初始化为 ``None``，
        因此可直接访问，无需 ``getattr`` 防御。
        """
        return self._feedback_service

    @feedback_service.setter
    def feedback_service(self, value: Any) -> None:
        self._feedback_service = value

    @property
    def notification_dispatcher(self) -> Any | None:
        """通知分发器（P2.9）。

        由 ``headless_main.py`` 启动时显式注入并 ``start()`` 订阅 EventBus；
        handler 通过此属性访问，懒构建仅作兜底。

        ``_notification_dispatcher`` 由 ``AppContextBuilder.build_lazy_tools`` 初始化为 ``None``，
        因此可直接访问，无需 ``getattr`` 防御。
        """
        return self._notification_dispatcher

    @notification_dispatcher.setter
    def notification_dispatcher(self, value: Any) -> None:
        self._notification_dispatcher = value

    # ── M1: AgentGateway（执行内核可替换端口，由 headless_main.py 显式注入）──

    @property
    def agent_gateway(self) -> Any | None:
        """执行内核网关（M1，内核可替换端口）。

        功能路径经此端口提交 agent 任务；具体内核（DSH / Claude Code /
        Codex）由 settings 键 ``agent_kernel.type`` 选择，换内核零改动。
        见 ``docs/kernel-layering-design-2026-08-15.md``。

        ``_agent_gateway`` 由 ``AppContextBuilder.build_lazy_tools`` 初始化为 ``None``。
        """
        return self._agent_gateway

    @agent_gateway.setter
    def agent_gateway(self, value: Any) -> None:
        self._agent_gateway = value

    # ── 助手运行时 ──

    def prepare_assistant_runtime(self):
        return self.assistant_runtime_manager.prepare_runtime()

    def get_assistant_runtime_status(self):
        return self.assistant_runtime_manager.get_status_snapshot()

    # ── 桥接服务生命周期 ──

    def start_browser_assistant_bridge(self, avoid_port: int | None = None):
        return self.browser_assistant_bridge.start(avoid_port=avoid_port)

    def stop_browser_assistant_bridge(self):
        return self.browser_assistant_bridge.stop()

    def get_browser_assistant_bridge_status(self):
        return self.browser_assistant_bridge.get_status_snapshot()

    def start_file_watcher(self, directories: list[str] | None = None) -> None:
        self.file_watcher.set_watched_dirs(directories or [])
        self.file_watcher.start()

    def stop_file_watcher(self) -> None:
        self.file_watcher.stop()

    def start_web_bridge_service(self, parent=None):
        if self.web_bridge_service is None:
            return False
        return self.web_bridge_service.start(parent=parent)

    def stop_web_bridge_service(self):
        if self.web_bridge_service is None:
            return
        return self.web_bridge_service.stop()

    def get_web_bridge_service_status(self):
        if self.web_bridge_service is None:
            return {"status": "headless", "enabled": False}
        return self.web_bridge_service.get_status_snapshot()

    def start_automation_hub(self) -> bool:
        """Start the automation-hub FastAPI backend as a subprocess."""
        try:
            mgr = self.mobile_bridge_manager
            if mgr is None:
                logger.error("automation hub manager unavailable")
                return False
            if mgr.state.running:
                logger.info("automation hub already running on port %d", mgr.state.port)
                return True
            return mgr.start()
        except Exception as exc:
            logger.warning("automation hub start failed: %s", exc)
            return False

    def get_sync_summary(self, resources=None):
        orchestrator = getattr(self, "api_sync_orchestrator", None)
        if orchestrator is None:
            return {
                "provider": "",
                "last_sync_time": None,
                "resources": [],
                "total_pending": 0,
                "total_conflicts": 0,
            }
        return orchestrator.get_sync_summary(resources=resources)

    # ── 超级标签（委托给 MemoryManager） ──

    def suggest_supertag(self, text: str) -> dict | None:
        return self.judge_scheduler.suggest_supertag(text, tag_service=self.tag_service)

    # ── 后台服务停止 ──

    def stop_background_services(self):
        try:
            self.stop_cron_service()
        except Exception:
            logger.debug("cron stop skipped", exc_info=True)
        try:
            self.subagent_manager.shutdown()
        except Exception:
            logger.debug("subagent stop skipped", exc_info=True)
        try:
            self.cross_device_manager.shutdown()
        except Exception:
            logger.debug("cross device session shutdown skipped", exc_info=True)
        try:
            # 修复 B8: 检查私有属性而非 property，避免触发不必要的懒初始化
            # （加载 transformers 模型后立即关闭，浪费资源且延长关闭时间）
            if self._kb_bridge is not None:
                self.knowledge_base_bridge.close()
        except Exception:
            logger.debug("knowledge base bridge close skipped", exc_info=True)
        try:
            self._tools.stop()
        except Exception:
            logger.debug("tooling provider stop skipped", exc_info=True)
        try:
            self.memory_manager.close()
        except Exception:
            logger.debug("memory context close skipped", exc_info=True)
        try:
            if self._mobile_bridge_manager is not None:
                self._mobile_bridge_manager.stop()
        except Exception:
            logger.debug("mobile bridge manager stop skipped", exc_info=True)
        try:
            self._infra.stop()
        except Exception:
            logger.debug("infrastructure provider stop skipped", exc_info=True)

    # ── ContentOrganizerService（惰性创建）──

    def get_content_organizer_service(self):
        return self._infra.get_content_organizer_service(
            note_repository=self.personal_note_repository,
            task_repository=self.personal_task_repository,
            inbox_service=self.assistant_inbox_service,
            settings_repository=self.settings_repository,
        )

    # ── 助手方法 ──
    # 调用方应直接访问 context.autonomous_assistant_manager.xxx()
    # 以下委托方法已消除，不再维护转发层。

    # ── AI 路由（委托给 AiManager） ──

    def process_with_local_router(self, user_input: str, context: dict | None = None):
        return self.ai_manager.process_with_local_router(user_input, context)

    def judge_route_task(self, task: str, context: dict | None = None):
        return self.ai_manager.judge_route_task(task, context)

    def get_judge_stats(self):
        return self.ai_manager.get_judge_stats()

    def get_local_router_status(self):
        return self.ai_manager.get_local_router_status()

    # ── 审计回滚接口 ──
    # 调用方应直接访问 context.autonomous_assistant_manager.xxx()

    # ── 权限矩阵接口 ──
    # 调用方应直接访问 context.autonomous_assistant_manager.xxx()

    # ── 规则中心接口 ──
    # 调用方应直接访问 context.autonomous_assistant_manager.xxx()
