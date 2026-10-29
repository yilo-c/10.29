from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Protocol
from sync.note_sync import build_note_sync_payload, ensure_note_sync_metadata, get_or_create_local_device_id
from sync.task_sync import build_task_sync_payload, ensure_task_sync_metadata
from sync.translation_sync import build_translation_sync_payload, ensure_translation_sync_metadata
from sync.vocabulary_sync import (
    build_word_sync_payload,
    build_wordbook_key,
    build_wordbook_sync_payload,
    ensure_word_sync_metadata,
    ensure_wordbook_sync_metadata,
)

# Protocol type aliases (defined in _protocols.py)
from ._protocols import (
    ImprovementProposalRepository,
    PersonalInboxRepository,
    PersonalNewsRepository,
    PersonalNoteRepository,
    PersonalTaskRepository,
    ReminderRepository,
    SettingsRepository,
    TranslationHistoryRepository,
    VocabularyRepository,
    ChatSessionRepository,
)

# Import all local implementations
from ._task import *
from ._settings import *
from ._inbox import *
from ._proposal import *
from ._note import *
from ._translation import *
from ._vocab import *
from ._reminder import *
from ._chat_session import *
from ._info_item import *
