"""ToolingProvider — 工具/集成层服务。

对应 AppContext 初始化阶段 5（工具/集成层）+ 6（生命周期恢复）。
包含：tech_watch、web_search/fetch、cron、MCP、API 集成、同步编排等。
"""

from __future__ import annotations

import logging
from typing import Any

from integrations.youdao_oauth_provider import YoudaoOAuthProvider
from api.integration_hub import ApiIntegrationHub
from api.provider_registry import create_default_api_provider_registry
from api.sync_orchestrator import (
    RESOURCE_SPECS,
    STACK_ORCHESTRATOR,
    ApiSyncOrchestrator,
    note_stack_launch,
)
from core.error_grading import (
    ANCHOR_STARTUP_SYNC,
    ANCHOR_STARTUP_RECOVERY,
    startup_anchor,
)
from core.observability import SEVERITY_INFO
from services.process_lifecycle_manager import ProcessLifecycleManager
from services.tech_watch import TechWatchStore, TechWatchCollector
from services.web_fetch import WebFetchService
from services.web_search import WebSearchService
from services.lifecycle_recovery_service import LifecycleRecoveryService

logger = logging.getLogger("siyi.app_context.tooling")


class ToolingProvider:
    """工具/集成提供者：初始化和持有阶段 5+6 的工具服务实例。"""

    def __init__(
        self,
        *,
        local_data_store: Any,
        settings_repository: Any,
        ai_client: Any,
        observability_service: Any,
        personal_note_repository: Any = None,
        personal_task_repository: Any = None,
        personal_news_repository: Any = None,
        translation_history_repository: Any = None,
        vocabulary_repository: Any = None,
        personal_inbox_repository: Any = None,
        improvement_proposal_repository: Any = None,
        get_tech_watch_db_path: Any = None,
        create_hub_client: Any = None,
    ) -> None:
        # ── 5. 工具/集成层 ──
        db_path = get_tech_watch_db_path() if callable(get_tech_watch_db_path) else None
        self.tech_watch_store = TechWatchStore(db_path=db_path or "")

        self.web_search_service = WebSearchService(settings_repository=settings_repository)
        self.web_fetch_service = WebFetchService()
        self.tech_watch_collector = TechWatchCollector(
            store=self.tech_watch_store,
            web_search_service=self.web_search_service,
            web_fetch_service=self.web_fetch_service,
        )
        # 修错误绑定（2026-09-16 批3，task_queue 死信 last_error 实证）：此前这里
        # 把 tech_watch_judge 绑成了路由用 JudgeScheduler（只有 .route()，没有
        # .judge()），导致 tech_watch.judge cron 任务 100% 抛 AttributeError 进
        # 死信；旧 assistant/_router_content.py:181 同调用同炸（死路径）。真正的
        # 技术雷达评估器是 services.tech_watch.TechWatchJudge(store)。路由决策用
        # 的 JudgeScheduler 由 ai_manager.judge_scheduler 单独提供，不依赖本属性。
        from services.tech_watch import TechWatchJudge

        self.tech_watch_judge = TechWatchJudge(store=self.tech_watch_store)

        # 线 B（2026-08-31）：自研 mcp/ 客户端退役——MCPService 从未 init()/start()，
        # mcp_enabled 默认 False 且无前端入口。外部工具接入走 ToolSpec 注册表或内核自带 MCP 客户端。
        self.api_integration_hub = ApiIntegrationHub(
            data_store=local_data_store,
            observability_service=observability_service,
        )
        self.api_provider_registry = create_default_api_provider_registry()
        self.youdao_oauth_provider = YoudaoOAuthProvider(
            integration_hub=self.api_integration_hub,
            provider_registry=self.api_provider_registry,
            settings_repository=settings_repository,
        )
        hub_client = create_hub_client() if callable(create_hub_client) else None
        self.api_sync_orchestrator = ApiSyncOrchestrator(
            provider="automation_hub",
            integration_hub=self.api_integration_hub,
            automation_hub_client=hub_client,
            repository_map={
                "notes": personal_note_repository,
                "tasks": personal_task_repository,
                "news": personal_news_repository,
                "translations": translation_history_repository,
                "wordbooks": vocabulary_repository,
                "words": vocabulary_repository,
            },
        )
        startup_anchor(
            SEVERITY_INFO,
            ANCHOR_STARTUP_SYNC,
            "sync orchestrator created",
            resources=list(RESOURCE_SPECS.keys()),
        )
        # 波次④包 C（§9-4 双开互斥守卫显式化）：登记 A 栈启动态。A 栈「开」的判据
        # = automation_hub client 就绪（无 client 则 sync_all 不可用）；与 B 栈
        # （headless_main.py SyncBridge 构造点）的登记合流后，若两栈对重叠资源
        # （notes / tasks↔todos）同开则产出启动 WARN + 诊断标记（可同开、必告警）。
        note_stack_launch(
            STACK_ORCHESTRATOR,
            list(RESOURCE_SPECS.keys()),
            enabled=hub_client is not None,
        )

        # ── 6. 生命周期与恢复 ──
        self.lifecycle_recovery_service = LifecycleRecoveryService(
            data_store=local_data_store,
            inbox_repository=personal_inbox_repository,
            proposal_repository=improvement_proposal_repository,
            observability_service=observability_service,
        )
        self.process_lifecycle_manager = ProcessLifecycleManager()

    def run_recovery(self) -> dict[str, Any]:
        """在 __init__ 完成后执行生命周期恢复（需依赖准备就绪）。"""
        summary = self.lifecycle_recovery_service.recover_on_startup()
        if summary.get("total_recovered"):
            logger.warning("lifecycle recovery applied: %s", summary)
        startup_anchor(
            SEVERITY_INFO,
            ANCHOR_STARTUP_RECOVERY,
            "recovery check done",
            recovered=summary.get("total_recovered", 0),
        )
        return summary

    def stop(self) -> None:
        """停止工具服务。"""
        try:
            self.tech_watch_store.close()
        except Exception:
            logger.debug("tech watch store close skipped", exc_info=True)
        try:
            self.process_lifecycle_manager.shutdown()
        except Exception:
            logger.debug("process lifecycle manager shutdown skipped", exc_info=True)
