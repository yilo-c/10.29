from __future__ import annotations

import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from memory.index import AssistantMemoryIndexService
from data.local_storage import LocalDataStore
from repositories import LocalPersonalInboxRepository, LocalPersonalNoteRepository, LocalPersonalTaskRepository


class _SettingsRepo:
    def __init__(self, values=None):
        self._values = dict(values or {})

    def get_setting(self, key, default=None):
        return self._values.get(key, default)


class _ProfileService:
    def __init__(self, focus_topics=None):
        self._focus_topics = list(focus_topics or [])

    def get_focus_topics(self):
        return list(self._focus_topics)


def test_memory_index_recall_merges_multiple_sources(tmp_path):
    store = LocalDataStore(base_dir=str(tmp_path))
    note_repo = LocalPersonalNoteRepository(store)
    task_repo = LocalPersonalTaskRepository(store)
    inbox_repo = LocalPersonalInboxRepository(store)
    note_repo.add_note({"title": "Agent 绗旇", "content": "记录浜嗗代理妫€索方妗堛€"})
    task_repo.add_task({"title": "研究 Agent", "description": "整理 AI Agent 工具閾"})
    inbox_repo.add_entry({"title": "Agent 收件", "content": "鍒繕了回顾执琛岃褰"})
    store.save_json(
        "assistant_decisions",
        [{"time": datetime.now(timezone.utc).isoformat(), "goal": "查看 Agent 璺嚎", "steps": [{"action_id": "web.search"}], "summary": {"status": "ok"}}],
    )

    service = AssistantMemoryIndexService(
        data_store=store,
        settings_repository=_SettingsRepo({"assistant_focus_topics": ["AI Agent"]}),
        note_repository=note_repo,
        task_repository=task_repo,
        inbox_repository=inbox_repo,
    )

    service.rebuild_index()
    result = service.recall("Agent", top_k=6)

    source_types = {item["source_type"] for item in result["items"]}
    assert {"note", "task", "inbox", "decision"}.issubset(source_types)


def test_memory_index_recall_supports_source_and_time_filters(tmp_path):
    store = LocalDataStore(base_dir=str(tmp_path))
    note_repo = LocalPersonalNoteRepository(store)
    task_repo = LocalPersonalTaskRepository(store)
    inbox_repo = LocalPersonalInboxRepository(store)
    old_time = (datetime.now(timezone.utc) - timedelta(days=20)).isoformat()
    recent_time = datetime.now(timezone.utc).isoformat()
    note_id = note_repo.add_note({"title": "旧笔璁", "content": "Agent 旧资鏂", "updated_at": old_time, "created_at": old_time})
    task_repo.add_task({"title": "鏈€近任鍔", "description": "Agent 新方妗", "updated_at": recent_time, "created_at": recent_time})
    note_repo.update_note(note_id, {"updated_at": old_time}, keep_version=False)

    service = AssistantMemoryIndexService(
        data_store=store,
        note_repository=note_repo,
        task_repository=task_repo,
        inbox_repository=inbox_repo,
    )

    service.rebuild_index()
    recent_tasks = service.recall("Agent", source_filters=["task"], days=7)

    assert recent_tasks["count"] == 1
    assert recent_tasks["items"][0]["source_type"] == "task"


def test_memory_index_recall_uses_profile_focus_topics_for_empty_query(tmp_path):
    store = LocalDataStore(base_dir=str(tmp_path))
    note_repo = LocalPersonalNoteRepository(store)
    note_repo.add_note({"title": "AI Agent 周报", "content": "整理 Agent 相关进展。"})
    note_repo.add_note({"title": "鍥壓", "content": "记录多肉浇水。"})

    service = AssistantMemoryIndexService(
        data_store=store,
        note_repository=note_repo,
        user_profile_service=_ProfileService(["AI Agent"]),
    )

    service.rebuild_index()
    result = service.recall("", top_k=3)

    assert result["effective_query"] == "AI Agent"
    assert result["items"][0]["title"] == "AI Agent 周报"
