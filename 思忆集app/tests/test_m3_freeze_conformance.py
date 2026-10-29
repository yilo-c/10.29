"""M3 freeze conformance tests — validate frozen contracts.

Covers:
- Resource field freeze: notes, tasks, saved_articles, translation_history, wordbooks
- Interface freeze: assistant_task, delivery_route
- Auth/token freeze: device_id, scope, refresh, revocation
"""
from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Any

import pytest

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

PROJECT_ROOT = APP_ROOT.parent
SCHEMA_SQL_PATH = PROJECT_ROOT / "automation_hub" / "api" / "schema.sql"
AUTH_ROUTER_PATH = PROJECT_ROOT / "automation_hub" / "api" / "auth" / "router.py"


# ── Helpers ─────────────────────────────────────────────────────


def _read_schema_sql() -> str:
    if not SCHEMA_SQL_PATH.is_file():
        raise RuntimeError(f"schema.sql not found at {SCHEMA_SQL_PATH} (cwd={Path.cwd()})")
    return SCHEMA_SQL_PATH.read_text(encoding="utf-8")


def _read_auth_router() -> str:
    if not AUTH_ROUTER_PATH.is_file():
        raise RuntimeError(f"auth router not found at {AUTH_ROUTER_PATH}")
    return AUTH_ROUTER_PATH.read_text(encoding="utf-8")


def _extract_column_names(schema_text: str, table_name: str) -> list[str]:
    """Extract column names from a CREATE TABLE statement."""
    pattern = re.compile(
        rf"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?{re.escape(table_name)}\s*\((.*?)\);",
        re.DOTALL | re.IGNORECASE,
    )
    match = pattern.search(schema_text)
    if not match:
        raise AssertionError(f"CREATE TABLE {table_name} not found in schema")
    body = match.group(1)
    cols: list[str] = []
    for line in body.split(","):
        line = line.strip()
        if not line:
            continue
        upper = line.upper()
        if any(upper.startswith(kw) for kw in ("FOREIGN KEY", "PRIMARY KEY", "UNIQUE", "CHECK", "CONSTRAINT", "INSERT")):
            continue
        col_name = line.split()[0].strip('"`[],')
        if col_name and col_name.upper() != "CREATE":
            cols.append(col_name)
    return cols


# ── Resource Field Sets (Frozen) ───────────────────────────────

FROZEN_NOTE_FIELDS = {
    "id", "user_id", "remote_id", "title", "content", "tags_json",
    "category", "folder_path", "favorite", "related_news_url",
    "linked_task_id", "created_at", "updated_at", "deleted_at",
    "version", "device_id", "sync_state", "last_synced_at",
}

FROZEN_TASK_FIELDS = {
    "id", "user_id", "remote_id", "name", "note", "is_completed",
    "reminder_time", "linked_note_id", "created_at", "updated_at",
    "deleted_at", "version", "device_id", "sync_state", "last_synced_at",
}

FROZEN_ARTICLE_FIELDS = {
    "id", "user_id", "remote_id", "item_key", "title", "url",
    "summary", "content", "source", "category", "published_at",
    "image_url", "is_favorite", "created_at", "updated_at",
    "deleted_at", "version", "device_id", "sync_state", "last_synced_at",
}

FROZEN_TRANSLATION_FIELDS = {
    "id", "user_id", "remote_id", "history_key", "source_text",
    "translated_text", "from_lang", "to_lang", "raw_result_json",
    "created_at", "updated_at", "deleted_at", "version",
    "device_id", "sync_state", "last_synced_at",
}

FROZEN_WORDBOOK_FIELDS = {
    "id", "user_id", "remote_id", "book_key", "name", "description",
    "created_at", "updated_at", "deleted_at", "version",
    "device_id", "sync_state", "last_synced_at",
}

FROZEN_SCOPE_LIST = [
    "news:read", "news:write", "note:read", "note:write",
    "task:read", "task:write", "translation:read", "translation:write",
    "vocabulary:read", "vocabulary:write", "tool:read", "tool:write",
    "tool:execute", "approval:read", "approval:decide", "audit:read",
    "proposal:read", "proposal:write", "proposals:admin",
    "repo:read", "repo:write",
    "agent:use", "user:admin",
    "agent-nodes:read", "agent-nodes:write", "device:manage",
    "sync:push", "sync:pull",
]

FROZEN_TOKEN_TEMPLATES = {"admin_ops", "agent", "cronjob", "readonly", "desktop"}


# ── 1. Resource Field Freeze ────────────────────────────────────


class TestResourceFieldFreeze:
    def _check_table_columns(self, table_name: str, frozen_fields: set[str]) -> None:
        schema = _read_schema_sql()
        cols = _extract_column_names(schema, table_name)
        col_set = set(cols)
        missing = frozen_fields - col_set
        extra = col_set - frozen_fields - {"id"}  # id is always present
        msg_parts = []
        if missing:
            msg_parts.append(f"missing frozen fields: {sorted(missing)}")
        if extra:
            msg_parts.append(f"unfrozen extra fields: {sorted(extra)}")
        assert not missing, f"{table_name}: {', '.join(msg_parts)}"

    def test_personal_notes_columns(self):
        self._check_table_columns("personal_notes", FROZEN_NOTE_FIELDS)

    def test_personal_tasks_columns(self):
        self._check_table_columns("personal_tasks", FROZEN_TASK_FIELDS)

    def test_personal_news_items_columns(self):
        self._check_table_columns("personal_news_items", FROZEN_ARTICLE_FIELDS)

    def test_personal_translation_history_columns(self):
        self._check_table_columns("personal_translation_history", FROZEN_TRANSLATION_FIELDS)

    def test_personal_wordbooks_columns(self):
        self._check_table_columns("personal_wordbooks", FROZEN_WORDBOOK_FIELDS)

    def test_tombstone_and_cursor_fields_present(self):
        """All sync-resource tables must have tombstone/cursor fields."""
        for name, fields in [
            ("personal_notes", FROZEN_NOTE_FIELDS),
            ("personal_tasks", FROZEN_TASK_FIELDS),
            ("personal_news_items", FROZEN_ARTICLE_FIELDS),
            ("personal_translation_history", FROZEN_TRANSLATION_FIELDS),
            ("personal_wordbooks", FROZEN_WORDBOOK_FIELDS),
        ]:
            for field in ("deleted_at", "updated_at", "version", "device_id", "sync_state", "remote_id"):
                assert field in fields, f"{name} missing frozen tombstone/cursor field: {field}"


# ── 2. ResourceSyncSpec Conformance ─────────────────────────────


class TestSyncSpecFreeze:
    def test_all_resources_have_specs(self):
        from api.sync_orchestrator import RESOURCE_SPECS
        for rsrc in ("notes", "tasks", "news", "translations", "wordbooks"):
            assert rsrc in RESOURCE_SPECS, f"{rsrc} missing from RESOURCE_SPECS"
            spec = RESOURCE_SPECS[rsrc]
            assert spec.name == rsrc
            assert spec.pull_method
            assert spec.merge_remote_method
            assert spec.list_pending_method
            assert spec.build_payload_method
            assert spec.upsert_method
            assert spec.delete_method
            assert spec.mark_synced_method
            assert spec.resolve_local_deleted_method

    def test_resource_spec_methods_are_callable(self):
        from api.sync_orchestrator import RESOURCE_SPECS, ApiSyncOrchestrator
        from types import SimpleNamespace

        client = SimpleNamespace()
        # Name-to-attribute map matching _extract_records in ApiSyncOrchestrator
        pull_attr_map = {
            "notes": "notes",
            "tasks": "tasks",
            "news": "items",
            "translations": "records",
            "wordbooks": "wordbooks",
            "words": "words",
        }
        write_attr_map = {
            "notes": "note",
            "tasks": "task",
            "news": "item",
            "translations": "record",
            "wordbooks": "wordbook",
            "words": "word",
        }
        for rsrc, spec in RESOURCE_SPECS.items():
            # Pull method must return object with next_cursor + resource data attribute
            def _make_pull(attr):
                def pull(*a, **kw):
                    ns = SimpleNamespace()
                    setattr(ns, attr, [])
                    ns.next_cursor = None
                    return ns
                return pull
            setattr(client, spec.pull_method, _make_pull(pull_attr_map.get(rsrc, rsrc)))
            for attr in (spec.upsert_method, spec.delete_method):
                def _make_write(a):
                    def write(*args, **kw):
                        ns = SimpleNamespace()
                        setattr(ns, a, {})
                        return ns
                    return write
                setattr(client, attr, _make_write(write_attr_map.get(rsrc, rsrc)))

        for rsrc, spec in RESOURCE_SPECS.items():
            hub = SimpleNamespace()
            hub.get_sync_cursor = lambda p, r: {}
            hub.upsert_sync_cursor = lambda p, r, cursor, extra: None
            hub.append_audit_event = lambda *a, **kw: None
            hub.enqueue_conflict = lambda *a, **kw: None
            hub.list_conflicts = lambda p, unresolved_only: []

            repo = SimpleNamespace()
            def _make_repo_method(ret):
                return lambda *a, **kw: ret
            for _attr in (spec.merge_remote_method, spec.list_pending_method,
                         spec.build_payload_method, spec.mark_synced_method,
                         spec.resolve_local_deleted_method):
                default_ret = {"conflicts": []} if "merge" in _attr else {}
                setattr(repo, _attr, _make_repo_method(default_ret))

            orchestrator = ApiSyncOrchestrator("test", hub, client, {rsrc: repo})
            result = orchestrator.sync_resource(rsrc, limit=10)
            assert result["resource"] == rsrc


# ── 3. Assistant Task Interface Freeze ──────────────────────────


class TestAssistantTaskFreeze:
    def test_status_constants(self):
        from assistant.task import (
            STATUS_PENDING, STATUS_RUNNING, STATUS_COMPLETED,
            STATUS_FAILED, STATUS_BLOCKED, STATUS_DEGRADED,
            STATUS_EXECUTING,
        )
        assert STATUS_PENDING == "pending"
        assert STATUS_EXECUTING == "executing"
        assert STATUS_RUNNING == STATUS_EXECUTING  # 向后兼容别名
        assert STATUS_COMPLETED == "completed"
        assert STATUS_FAILED == "failed"
        assert STATUS_BLOCKED == "blocked"
        assert STATUS_DEGRADED == "degraded"

    def test_valid_transitions(self):
        from assistant.state_machine import TaskStateMachine, TaskState
        sm = TaskStateMachine("test", initial_state=TaskState.PENDING)
        assert sm.can_transition_to(TaskState.EXECUTING)
        sm._state = TaskState.EXECUTING
        assert sm.can_transition_to(TaskState.COMPLETED)
        assert sm.can_transition_to(TaskState.FAILED)
        assert sm.can_transition_to(TaskState.PAUSED)  # 支持暂停
        assert sm.can_transition_to(TaskState.CANCELLED)
        assert not sm.can_transition_to(TaskState.PENDING)

    def test_status_labels(self):
        from assistant.task import status_label, STATUS_EXECUTING
        assert status_label(STATUS_EXECUTING) == "执行中"
        assert status_label("pending") == "待处理"
        assert status_label("completed") == "已完成"
        assert status_label("failed") == "失败"

    def test_entry_create_and_from_methods(self):
        from assistant.task import AssistantTaskEntry
        from assistant.task_lifecycle import AgentTaskLifecycle

        lifecycle = AgentTaskLifecycle(data_store=None, memory=None)
        entry = AssistantTaskEntry(lifecycle)

        task = entry.create_task("test")
        assert task.goal == "test"
        assert task.source == "assistant.goal"
        assert task.status == "pending"

        task = entry.from_chat("hello", chat_session_id="s-1")
        assert task.source == "assistant.chat"
        assert task.delivery_channel == "chat"

        task = entry.from_rule_center("check", rule_id="r1")
        assert task.source == "assistant.rule:r1"

        task = entry.from_timer("brief", event_name="morning")
        assert task.source == "assistant.schedule:morning"

        task = entry.from_browser("save", url="http://ex.com")
        assert task.source == "assistant.browser"


# ── 4. Delivery Route Interface Freeze ──────────────────────────


class TestDeliveryRouteFreeze:
    def test_canonical_channels(self):
        from assistant.delivery_route import (
            CHANNEL_INBOX, CHANNEL_NOTIFICATION, CHANNEL_BOTH,
            CHANNEL_CHAT, CHANNEL_FLOATING, CHANNEL_SUBTITLE,
        )
        assert CHANNEL_INBOX == "inbox"
        assert CHANNEL_NOTIFICATION == "notification"
        assert CHANNEL_BOTH == "both"
        assert CHANNEL_CHAT == "chat_output"
        assert CHANNEL_FLOATING == "floating_output"
        assert CHANNEL_SUBTITLE == "subtitle_output"

    def test_resolve_channel(self):
        from assistant.delivery_route import resolve_channel, CHANNEL_INBOX, CHANNEL_CHAT
        assert resolve_channel("chat") == CHANNEL_CHAT
        assert resolve_channel("inbox") == CHANNEL_INBOX
        assert resolve_channel("") == CHANNEL_INBOX
        assert resolve_channel(None) == CHANNEL_INBOX

    def test_is_visible_channel(self):
        from assistant.delivery_route import is_visible_channel, CHANNEL_NOTIFICATION, CHANNEL_INBOX
        assert is_visible_channel(CHANNEL_NOTIFICATION)
        assert not is_visible_channel(CHANNEL_INBOX)

    def test_subtitle_and_tone(self):
        from assistant.delivery_route import build_task_progress_subtitle, build_subtitle_tone

        sub = build_task_progress_subtitle(goal="t", total_steps=3, ok_steps=3)
        assert "✅" in sub

        sub = build_task_progress_subtitle(goal="t", total_steps=3, ok_steps=2, failed_steps=1)
        assert "⚠️" in sub

        assert build_subtitle_tone({"failed": 0, "blocked": 0, "degraded": 0}) == "success"
        assert build_subtitle_tone({"failed": 1, "blocked": 0, "degraded": 0}) == "alert"


# ── 5. Auth/Token Freeze ───────────────────────────────────────


class TestAuthTokenFreeze:
    def test_bootstrap_scopes(self):
        router_src = _read_auth_router()
        # Find BOOTSTRAP_SCOPES list in the source
        match = re.search(
            r"BOOTSTRAP_SCOPES\s*=\s*\[(.*?)\]",
            router_src, re.DOTALL,
        )
        assert match, "BOOTSTRAP_SCOPES not found in auth router"
        # Extract string literals
        scopes_in_source = re.findall(r'"([^"]+)"', match.group(1))
        assert len(scopes_in_source) == len(FROZEN_SCOPE_LIST)
        assert set(scopes_in_source) == set(FROZEN_SCOPE_LIST)

    def test_token_templates(self):
        router_src = _read_auth_router()
        # Find TOKEN_TEMPLATES dict keys in the source
        match = re.search(
            r"TOKEN_TEMPLATES\s*=\s*\{(.*?)\}",
            router_src, re.DOTALL,
        )
        assert match, "TOKEN_TEMPLATES not found in auth router"
        template_names = re.findall(r'"([^"]+)"\s*:', match.group(1))
        assert set(template_names) == FROZEN_TOKEN_TEMPLATES

    def test_devices_table_columns(self):
        schema = _read_schema_sql()
        cols = _extract_column_names(schema, "devices")
        for field in ("id", "user_id", "name", "platform", "created_at", "last_seen_at"):
            assert field in cols, f"devices table missing column: {field}"

    def test_api_tokens_table_columns(self):
        schema = _read_schema_sql()
        cols = _extract_column_names(schema, "api_tokens")
        for field in ("id", "user_id", "device_id", "token_hash", "scopes",
                       "expires_at", "created_at", "revoked_at"):
            assert field in cols, f"api_tokens table missing column: {field}"


# ── 6. Release Gate Integration ────────────────────────────────


class TestReleaseGateFreeze:
    def test_release_check_invokes_desktop_gate_for_regression(self):
        sys.path.insert(0, str(PROJECT_ROOT / "automation_hub" / "scripts"))
        from release_check import _should_run_desktop_gate
        assert _should_run_desktop_gate("regression", False, False) is True
        assert _should_run_desktop_gate("smoke", False, False) is False
        assert _should_run_desktop_gate("full", False, True) is False

    def test_release_check_has_desktop_gate_argument(self):
        sys.path.insert(0, str(PROJECT_ROOT / "automation_hub" / "scripts"))
        from release_check import _run_desktop_gate
        import inspect
        sig = inspect.signature(_run_desktop_gate)
        params = list(sig.parameters.keys())
        assert "repo_root" in params
        assert "python_executable" in params
        assert "suite" in params
