"""InfrastructureProvider — 基础服务与包装层。

对应 AppContext 初始化阶段 2（基础服务层）+ 2.5（服务层包装）。
包含：reminder_service、observability_service、behavior_logger、file_watcher、
audit_trail、rollback_manager、news_service、guard_service 等。
"""

from __future__ import annotations

import logging
from typing import Any

from core.events import event_bus
from core.observability import ObservabilityService
from core.behavior_logger import BehaviorLogger
from core.file_watcher import FileWatcher
from core.audit_rollback import AuditTrail, RollbackManager
from services.automation_hub_service import AutomationHubService
from services.guard_service import GuardService
from services.provider_service import ProviderService
from services.extension_service import ExtensionService
from services.reminder import ReminderService
from services.content_organizer_service import ContentOrganizerService
from services.data_protection_service import DataProtectionService

logger = logging.getLogger("siyi.app_context.infra")


class InfrastructureProvider:
    """基础服务提供者：初始化和持有阶段 2+2.5 的基础服务实例。"""

    def __init__(
        self,
        *,
        local_data_store: Any,
        settings_repository: Any,
        alarms: list[dict[str, Any]] | None = None,
        personal_note_repository: Any = None,
        personal_task_repository: Any = None,
        personal_news_repository: Any = None,
        personal_inbox_repository: Any = None,
        reminder_repository: Any = None,
    ) -> None:
        # ── 2. 基础服务层 ──
        self.content_organizer_service: Any = None  # lazy init via get_content_organizer_service()
        self.reminder_service = ReminderService(
            alarms=alarms,
            data_store=local_data_store,
            reminder_repository=reminder_repository,
        )
        self.data_protection_service = DataProtectionService(local_data_store)
        self.observability_service = ObservabilityService(local_data_store)
        self.behavior_logger = BehaviorLogger(
            data_dir=local_data_store.base_dir,
            event_bus=event_bus,
        )
        self.behavior_logger.start()
        self.file_watcher = FileWatcher(behavior_logger=self.behavior_logger)
        self.audit_trail = AuditTrail(
            data_store=local_data_store,
            observability_service=self.observability_service,
        )
        self.rollback_manager = RollbackManager(
            data_store=local_data_store,
            audit_trail=self.audit_trail,
            note_repository=personal_note_repository,
            task_repository=personal_task_repository,
            inbox_repository=personal_inbox_repository,
            observability_service=self.observability_service,
        )

        # ── 2.5. 服务层包装 ──
        self.event_bus = event_bus
        self.automation_hub_service = AutomationHubService(settings_repository=settings_repository)
        self.guard_service = GuardService(
            settings_repository=settings_repository,
            observability_service=self.observability_service,
        )
        self.provider_service = ProviderService()
        self.extension_service = ExtensionService(data_store=local_data_store)

    def get_content_organizer_service(
        self,
        *,
        note_repository: Any = None,
        task_repository: Any = None,
        news_repository: Any = None,
        inbox_service: Any = None,
        settings_repository: Any = None,
    ) -> Any:
        """惰性创建 ContentOrganizerService（需要 AssistantInboxService，在阶段 8 初始化）。"""
        if self.content_organizer_service is None:
            self.content_organizer_service = ContentOrganizerService(
                note_repository=note_repository,
                task_repository=task_repository,
                news_repository=news_repository,
                inbox_service=inbox_service,
                settings_repository=settings_repository,
            )
        return self.content_organizer_service

    def stop(self) -> None:
        """停止基础服务（在 AppContext.stop_background_services 中调用）。"""
        try:
            self.file_watcher.stop()
        except Exception:
            logger.debug("file watcher stop skipped", exc_info=True)
        try:
            self.behavior_logger.stop()
        except Exception:
            logger.debug("behavior logger stop skipped", exc_info=True)
