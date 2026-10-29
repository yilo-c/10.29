from __future__ import annotations

import logging
import os

from data.local_storage import LocalDataStore
from repositories import (
    LocalChatSessionRepository,
    LocalImprovementProposalRepository,
    LocalInfoItemRepository,
    LocalPersonalInboxRepository,
    LocalPersonalNoteRepository,
    LocalPersonalTaskRepository,
    LocalReminderRepository,
    LocalSettingsRepository,
    LocalTranslationHistoryRepository,
    LocalVocabularyRepository,
)
from core.settings_schema import migrate_settings_repository
from core.error_grading import (
    ANCHOR_STARTUP_SETTINGS_MIGRATE,
    SEVERITY_INFO,
    startup_anchor,
)

logger = logging.getLogger("siyi.storage_context")


class StorageContext:
    """数据存储层上下文：管理 LocalDataStore + 所有 Repository。

    当 desknotes_data_dir 传入时，笔记和任务数据源自动切换到 desk-notes。
    所有服务实例同时注册到全局 ServiceRegistry，便于渐进式迁移。
    """

    def __init__(self, data_base_dir: str | None, desknotes_data_dir: str | None = None):
        from app_runtime.service_registry import service_registry

        self.local_data_store = LocalDataStore(data_base_dir)
        service_registry.register(LocalDataStore, self.local_data_store)

        self._desknotes_dir = desknotes_data_dir
        self._id_mapper = None

        if desknotes_data_dir:
            self._init_desknotes()
        else:
            self._init_local()

        service_registry.register(type(self.personal_task_repository), self.personal_task_repository)
        service_registry.register(type(self.personal_note_repository), self.personal_note_repository)
        self.translation_history_repository = LocalTranslationHistoryRepository(self.local_data_store)
        service_registry.register(LocalTranslationHistoryRepository, self.translation_history_repository)
        self.vocabulary_repository = LocalVocabularyRepository(self.local_data_store)
        service_registry.register(LocalVocabularyRepository, self.vocabulary_repository)
        self.settings_repository = LocalSettingsRepository(self.local_data_store)
        service_registry.register(LocalSettingsRepository, self.settings_repository)
        self.reminder_repository = LocalReminderRepository(self.local_data_store)
        service_registry.register(LocalReminderRepository, self.reminder_repository)

        migrate_result = migrate_settings_repository(self.settings_repository)
        if migrate_result.get("changed"):
            logger.info("settings schema migration applied: version=%s", migrate_result.get("version"))
        startup_anchor(
            SEVERITY_INFO, ANCHOR_STARTUP_SETTINGS_MIGRATE, "settings migration done",
            changed=migrate_result.get("changed"),
        )

        self.personal_inbox_repository = LocalPersonalInboxRepository(self.local_data_store)
        service_registry.register(LocalPersonalInboxRepository, self.personal_inbox_repository)
        self.improvement_proposal_repository = LocalImprovementProposalRepository(self.local_data_store)
        service_registry.register(LocalImprovementProposalRepository, self.improvement_proposal_repository)

        self.chat_session_repository = LocalChatSessionRepository(self.local_data_store)
        service_registry.register(LocalChatSessionRepository, self.chat_session_repository)

        self.info_item_repository = LocalInfoItemRepository(self.local_data_store)
        service_registry.register(LocalInfoItemRepository, self.info_item_repository)

    # ── 初始化策略 ─────────────────────────────────────────────────────

    def _init_local(self):
        """使用默认的本地 JSON 文件存储（向后兼容）。"""
        self.personal_task_repository = LocalPersonalTaskRepository(self.local_data_store)
        self.personal_note_repository = LocalPersonalNoteRepository(self.local_data_store)

    def _init_desknotes(self):
        """使用 desk-notes 数据目录替代笔记/任务存储。"""
        from data.desknotes_id_map import IDMapper
        from data.repositories.desknotes_note_adapter import DeskNotesNoteAdapter
        from data.repositories.desknotes_task_adapter import DeskNotesTaskAdapter

        self._id_mapper = IDMapper(self._desknotes_dir)
        self._id_mapper.sync_from_stores()

        self.personal_note_repository = DeskNotesNoteAdapter(self._desknotes_dir, self._id_mapper)
        self.personal_task_repository = DeskNotesTaskAdapter(self._desknotes_dir, self._id_mapper)

    # ── 公开属性 ───────────────────────────────────────────────────────

    @property
    def id_mapper(self):
        return self._id_mapper

    def get_tech_watch_db_path(self) -> str:
        return os.path.join(self.local_data_store.base_dir, "tech_watch.db")
