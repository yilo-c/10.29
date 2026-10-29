"""Tests for desktop_web_bridge: AppBridge, service registry, security controls."""

from __future__ import annotations

import pytest
import sys
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from desktop.web_bridge import (
    AppBridge,
    AssistantService,
    BaseService,
    DatabaseService,
    DoctorService,
    InboxService,
    NotesService,
    PetService,
    SettingsService,
    SyncService,
    TasksService,
    _default_services,
    _err,
    _ok,
)


# ── Helpers ─────────────────────────────────────────────────────


def _make_bridge(**overrides) -> AppBridge:
    services = dict(_default_services())
    services.update(overrides)
    return AppBridge(services=services)


def _call(
    bridge: AppBridge,
    service: str,
    method: str,
    params: dict | None = None,
) -> dict:
    """Shorthand for bridge.call_service."""
    return bridge.call_service(service, method, params)


# ── Response envelope ────────────────────────────────────────────


class TestResponseEnvelope:
    def test_ok_response(self):
        result = _ok({"id": 1})
        assert result == {"ok": True, "data": {"id": 1}, "error": None}

    def test_ok_no_data(self):
        result = _ok()
        assert result == {"ok": True, "data": None, "error": None}

    def test_error_response(self):
        result = _err("TEST_ERROR", "something broke")
        assert result == {"ok": False, "data": None, "error": "TEST_ERROR: something broke"}

    def test_call_service_returns_envelope(self):
        bridge = _make_bridge()
        result = _call(bridge, "notes", "get_notes", {"filter": "all"})
        assert "ok" in result
        assert "data" in result
        assert "error" in result


# ── Service resolution ───────────────────────────────────────────


class TestServiceResolution:
    def test_unknown_service_returns_error(self):
        bridge = _make_bridge()
        result = _call(bridge, "nonexistent", "anything")
        assert result["ok"] is False
        assert "SERVICE_NOT_FOUND" in result["error"]

    def test_unknown_method_returns_error(self):
        bridge = _make_bridge()
        result = _call(bridge, "notes", "nonexistent_method")
        assert result["ok"] is False
        assert "METHOD_NOT_FOUND" in result["error"]

    def test_empty_service_name_returns_error(self):
        bridge = _make_bridge()
        result = _call(bridge, "", "get_notes")
        assert result["ok"] is False

    def test_empty_method_returns_error(self):
        bridge = _make_bridge()
        result = _call(bridge, "notes", "")
        assert result["ok"] is False


# ── Service call roundtrip ────────────────────────────────────────


class TestServiceCalls:
    def test_doctor_run_check(self):
        bridge = _make_bridge()
        result = _call(bridge, "doctor", "run_check", {"check_type": "deep"})
        assert result["ok"] is True
        assert result["data"]["check_type"] == "deep"
        assert result["data"]["status"] == "ok"

    def test_doctor_get_report(self):
        bridge = _make_bridge()
        result = _call(bridge, "doctor", "get_report", {"report_id": "r1"})
        assert result["ok"] is True
        assert result["data"]["report_id"] == "r1"

    def test_doctor_get_debug_assets(self):
        bridge = _make_bridge()
        result = _call(bridge, "doctor", "get_debug_assets")
        assert result["ok"] is True
        assert isinstance(result["data"]["hero"], list)
        assert isinstance(result["data"]["issues"], list)
        assert isinstance(result["data"]["suggestions"], list)

    def test_notes_get_notes(self):
        bridge = _make_bridge()
        result = _call(bridge, "notes", "get_notes", {"filter": "recent"})
        assert result["ok"] is True
        assert isinstance(result["data"], list)

    def test_notes_create_note(self):
        bridge = _make_bridge()
        result = _call(bridge, "notes", "create_note", {"title": "Test", "content": "Hello"})
        assert result["ok"] is True
        assert result["data"]["created"] is True
        assert result["data"]["title"] == "Test"

    def test_notes_delete_note(self):
        bridge = _make_bridge()
        result = _call(bridge, "notes", "delete_note", {"id": "n1"})
        assert result["ok"] is True
        assert result["data"]["deleted"] is True

    def test_tasks_get_tasks(self):
        bridge = _make_bridge()
        result = _call(bridge, "tasks", "get_tasks", {"filter": "all"})
        assert result["ok"] is True
        assert isinstance(result["data"], list)

    def test_tasks_complete_task(self):
        bridge = _make_bridge()
        result = _call(bridge, "tasks", "complete_task", {"id": "t1"})
        assert result["ok"] is True
        assert result["data"]["completed"] is True

    def test_settings_get_all(self):
        bridge = _make_bridge()
        result = _call(bridge, "settings", "get_all")
        assert result["ok"] is True
        assert "theme" in result["data"]

    def test_settings_update(self):
        bridge = _make_bridge()
        result = _call(bridge, "settings", "update", {"key": "theme", "value": "dark"})
        assert result["ok"] is True
        assert result["data"]["updated"] is True

    def test_sync_trigger_sync(self):
        """无 orchestrator 时诚实返回 started:False（9e0a9c5 修复后的契约）。

        历史陈账：旧实现调不存在的 trigger_sync(domain=) → AttributeError 被
        吞 → fallthrough 假成功 started:True；本测试曾断言该假成功，现反转。
        """
        bridge = _make_bridge()
        result = _call(bridge, "sync", "trigger_sync", {"domain": "notes"})
        assert result["ok"] is True
        assert result["data"]["started"] is False
        assert "orchestrator unavailable" in result["data"]["error"]

    def test_sync_trigger_sync_with_orchestrator(self):
        """orchestrator 在场且 sync_all 成功 → started:True（真成功路径）。"""

        class _FakeOrch:
            def sync_all(self, resources=None):
                return {"synced": 3, "resources": resources}

        sync_service = _default_services()["sync"]
        sync_service._orchestrator = _FakeOrch()
        bridge = AppBridge(services={**_default_services(), "sync": sync_service})
        result = _call(bridge, "sync", "trigger_sync", {"domain": "notes"})
        assert result["ok"] is True
        assert result["data"]["started"] is True
        assert result["data"]["result"]["synced"] == 3

    def test_sync_trigger_sync_orchestrator_failure(self):
        """sync_all 抛异常 → 诚实 started:False + error，不假装成功。"""

        class _BoomOrch:
            def sync_all(self, resources=None):
                raise RuntimeError("network down")

        sync_service = _default_services()["sync"]
        sync_service._orchestrator = _BoomOrch()
        bridge = AppBridge(services={**_default_services(), "sync": sync_service})
        result = _call(bridge, "sync", "trigger_sync", {"domain": "notes"})
        assert result["ok"] is True
        assert result["data"]["started"] is False
        assert "sync failed" in result["data"]["error"]

    def test_sync_get_status(self):
        bridge = _make_bridge()
        result = _call(bridge, "sync", "get_status")
        assert result["ok"] is True
        assert "status" in result["data"]

    def test_pet_launch(self):
        bridge = _make_bridge()
        result = _call(bridge, "pet", "launch", {"pet_id": "qinai"})
        assert result["ok"] is True
        assert result["data"]["launched"] is True

    def test_pet_set_mood(self):
        bridge = _make_bridge()
        result = _call(bridge, "pet", "set_mood", {"pet_id": "qinai", "mood": "happy"})
        assert result["ok"] is True
        assert result["data"]["mood"] == "happy"

    def test_inbox_get_items(self):
        bridge = _make_bridge()
        result = _call(bridge, "inbox", "get_items")
        assert result["ok"] is True
        assert isinstance(result["data"], list)

    def test_inbox_add_item(self):
        bridge = _make_bridge()
        result = _call(bridge, "inbox", "add_item", {"content": "New item", "source": "manual"})
        assert result["ok"] is True
        assert result["data"]["added"] is True

    def test_database_query(self):
        bridge = _make_bridge()
        result = _call(bridge, "database", "query", {"table": "notes"})
        assert result["ok"] is True
        assert isinstance(result["data"], list)

    def test_database_insert(self):
        bridge = _make_bridge()
        result = _call(bridge, "database", "insert", {"table": "notes", "data": {"title": "T"}})
        assert result["ok"] is True
        assert result["data"]["inserted"] is True

    @pytest.mark.skip("news module deleted")
    def test_news_get_feeds(self):
        bridge = _make_bridge()
        result = _call(bridge, "news", "get_feeds")
        assert result["ok"] is True
        assert isinstance(result["data"], list)

    @pytest.mark.skip("news module deleted")
    def test_news_mark_read(self):
        bridge = _make_bridge()
        result = _call(bridge, "news", "mark_read", {"item_id": "n1"})
        assert result["ok"] is True
        assert result["data"]["marked_read"] is True

    def test_assistant_ask(self):
        bridge = _make_bridge()
        result = _call(bridge, "assistant", "ask", {"query": "Hello", "context": {"language": "zh"}})
        assert result["ok"] is True
        assert result["data"]["query"] == "Hello"
        assert result["data"]["context_used"] is True

    def test_assistant_get_suggestions(self):
        bridge = _make_bridge()
        result = _call(bridge, "assistant", "get_suggestions")
        assert result["ok"] is True
        assert len(result["data"]) > 0

    def test_call_without_params(self):
        bridge = _make_bridge()
        result = bridge.call_service("doctor", "run_check")
        assert result["ok"] is True

    def test_call_with_null_params(self):
        bridge = _make_bridge()
        result = bridge.call_service("doctor", "run_check", None)
        assert result["ok"] is True


# ── Parameter validation ──────────────────────────────────────────


class TestParameterValidation:
    def test_call_with_wrong_param_types(self):
        bridge = _make_bridge()
        # Service method doesn't accept unknown kwargs → should return error
        result = _call(bridge, "doctor", "run_check", {"nonexistent_param": "x"})
        assert result["ok"] is False
        assert "INVALID_PARAMS" in result["error"]

    def test_call_with_non_dict_params(self):
        bridge = _make_bridge()
        # call_service expects dict as 3rd arg — simulate non-dict
        result = bridge.call_service("notes", "get_notes", "not_a_dict")
        assert result["ok"] is False
        assert "INVALID_PARAMS" in result["error"]


# ── Custom service registration ─────────────────────────────────────


class TestCustomService:
    def test_register_custom_service(self):
        bridge = _make_bridge()

        class MockService(BaseService):
            def __init__(self):
                super().__init__("mock")

            def ping(self) -> str:
                return "pong"

        bridge.register_service("mock", MockService())
        result = _call(bridge, "mock", "ping")
        assert result["ok"] is True
        assert result["data"] == "pong"

    def test_services_property_is_read_only_copy(self):
        bridge = _make_bridge()
        services = bridge.services
        services["_injected"] = "hack"
        assert "_injected" not in bridge.services


# ── Signals ────────────────────────────────────────────────────────


class TestSignals:
    def test_data_changed_signal_exists(self):
        bridge = _make_bridge()
        assert hasattr(bridge, "data_changed")
        # PyQtSignal has a 'emit' method
        assert callable(bridge.data_changed.emit)

    def test_system_notification_signal_exists(self):
        bridge = _make_bridge()
        assert callable(bridge.system_notification.emit)

    def test_pet_mood_changed_signal_exists(self):
        bridge = _make_bridge()
        assert callable(bridge.pet_mood_changed.emit)

    def test_sync_status_changed_signal_exists(self):
        bridge = _make_bridge()
        assert callable(bridge.sync_status_changed.emit)

    def test_signals_have_correct_signatures(self):
        bridge = _make_bridge()
        # Verify signal arity by checking their type info
        assert bridge.data_changed.signal  # pyqtSignal attribute
        assert bridge.system_notification.signal
        assert bridge.pet_mood_changed.signal
        assert bridge.sync_status_changed.signal


# ── Individual service unit tests ──────────────────────────────────


class TestDoctorService:
    def setup_method(self):
        self.svc = DoctorService()

    def test_run_check_default(self):
        result = self.svc.run_check()
        assert result["check_type"] == "quick"

    def test_run_check_deep(self):
        result = self.svc.run_check("deep")
        assert result["check_type"] == "deep"

    def test_get_report_default(self):
        result = self.svc.get_report()
        assert result["report_id"] == "latest"

    def test_get_debug_assets_structure(self):
        result = self.svc.get_debug_assets()
        assert "hero" in result
        assert "issues" in result
        assert "suggestions" in result


class TestAssistantService:
    def setup_method(self):
        self.svc = AssistantService()

    def test_ask_with_context(self):
        result = self.svc.ask("Hello", context={"lang": "zh"})
        assert result["context_used"] is True

    def test_ask_without_context(self):
        result = self.svc.ask("Hello")
        assert result["context_used"] is False

    def test_get_suggestions(self):
        result = self.svc.get_suggestions()
        assert len(result) > 0

    def test_execute_action(self):
        result = self.svc.execute_action("open_settings")
        assert result["executed"] is True


class TestSyncService:
    def setup_method(self):
        self.svc = SyncService()

    def test_trigger_sync_all(self):
        result = self.svc.trigger_sync()
        assert result["domain"] == "all"

    def test_trigger_sync_specific(self):
        result = self.svc.trigger_sync("notes")
        assert result["domain"] == "notes"

    def test_get_status(self):
        result = self.svc.get_status()
        assert result["status"] == "idle"

    def test_get_history(self):
        result = self.svc.get_history(5)
        assert len(result) == 1


class TestPetService:
    def setup_method(self):
        self.svc = PetService()

    def test_launch_default(self):
        result = self.svc.launch()
        assert result["pet_id"] == "default"

    def test_launch_specific(self):
        result = self.svc.launch("qinai")
        assert result["pet_id"] == "qinai"

    def test_close(self):
        result = self.svc.close("qinai")
        assert result["closed"] is True

    def test_set_mood(self):
        result = self.svc.set_mood("qinai", "happy")
        assert result["mood"] == "happy"

    def test_get_moods(self):
        result = self.svc.get_moods()
        assert "happy" in result
        assert "sad" in result


class TestSettingsService:
    def setup_method(self):
        self.svc = SettingsService()

    def test_get_all(self):
        result = self.svc.get_all()
        assert result["theme"] == "light"

    def test_update(self):
        result = self.svc.update("theme", "dark")
        assert result["updated"] is True

    def test_test_api_connection(self):
        result = self.svc.test_api_connection("openai", {"key": "test"})
        assert result["reachable"] is True


class TestDatabaseService:
    def setup_method(self):
        self.svc = DatabaseService()

    def test_query(self):
        result = self.svc.query("notes")
        assert isinstance(result, list)

    def test_insert(self):
        result = self.svc.insert("notes", {"title": "T"})
        assert result["inserted"] is True
        assert "id" in result

    def test_update(self):
        result = self.svc.update("notes", "n1", {"title": "T2"})
        assert result["updated"] is True

    def test_delete(self):
        result = self.svc.delete("notes", "n1")
        assert result["deleted"] is True


class TestInboxService:
    def setup_method(self):
        self.svc = InboxService()

    def test_get_items(self):
        result = self.svc.get_items()
        assert isinstance(result, list)

    def test_add_item(self):
        result = self.svc.add_item("Hello")
        assert result["added"] is True

    def test_summarize(self):
        result = self.svc.summarize()
        assert "total" in result


class TestNotesService:
    def setup_method(self):
        self.svc = NotesService()

    def test_get_notes(self):
        result = self.svc.get_notes()
        assert len(result) == 1

    def test_create_note(self):
        result = self.svc.create_note("Test", "Content")
        assert result["created"] is True

    def test_update_note(self):
        result = self.svc.update_note("n1", title="Updated")
        assert result["updated"] is True

    def test_delete_note(self):
        result = self.svc.delete_note("n1")
        assert result["deleted"] is True


class TestTasksService:
    def setup_method(self):
        self.svc = TasksService()

    def test_get_tasks(self):
        result = self.svc.get_tasks()
        assert len(result) == 1

    def test_create_task(self):
        result = self.svc.create_task("Test", "Description")
        assert result["created"] is True

    def test_update_task(self):
        result = self.svc.update_task("t1", title="Updated")
        assert result["updated"] is True

    def test_complete_task(self):
        result = self.svc.complete_task("t1")
        assert result["completed"] is True
