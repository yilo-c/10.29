"""Verification tests for CopilotEngine — import, API surface, ServiceRegistry integration."""
from __future__ import annotations

import sys
import os

_project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)


def test_copilot_engine_can_be_imported():
    from services.copilot_engine import CopilotEngine
    assert CopilotEngine is not None


def test_copilot_engine_via_app_runtime():
    from services.copilot_engine import CopilotEngine
    from app_runtime import CopilotEngine as CE
    assert CE is CopilotEngine


def test_service_registry_import():
    from app_runtime.service_registry import service_registry, ServiceRegistry
    assert service_registry is not None


def test_app_context_import_does_not_circular_break():
    from app_runtime.app_context import AppContext
    assert AppContext is not None


def test_copilot_engine_has_required_methods():
    from services.copilot_engine import CopilotEngine
    from core.events import EventBus
    eb = EventBus()
    engine = CopilotEngine(event_bus=eb, ai_client=None, capability_router=None, settings_repo=None)
    assert hasattr(engine, "process_user_input")
    assert hasattr(engine, "process_event")
    assert hasattr(engine, "register_event_handlers")
    assert hasattr(engine, "shutdown")


def test_process_user_input_empty():
    from services.copilot_engine import CopilotEngine
    from core.events import EventBus
    eb = EventBus()
    engine = CopilotEngine(event_bus=eb, ai_client=None, capability_router=None, settings_repo=None)
    result = engine.process_user_input("")
    assert result["response_text"] == ""
    assert result["intent"] == "empty"


def test_register_event_handlers_idempotent():
    from services.copilot_engine import CopilotEngine
    from core.events import EventBus
    eb = EventBus()
    engine = CopilotEngine(event_bus=eb, ai_client=None, capability_router=None, settings_repo=None)
    engine.register_event_handlers()
    count1 = len(engine._handlers)
    engine.register_event_handlers()
    count2 = len(engine._handlers)
    assert count1 == count2
    engine.shutdown()


def test_shutdown_cleans_up():
    from services.copilot_engine import CopilotEngine
    from core.events import EventBus
    eb = EventBus()
    engine = CopilotEngine(event_bus=eb, ai_client=None, capability_router=None, settings_repo=None)
    engine.register_event_handlers()
    engine.shutdown()
    assert len(engine._handlers) == 0
    assert not engine._registered


def test_service_registry_register_and_resolve():
    from services.copilot_engine import CopilotEngine
    from core.events import EventBus
    from app_runtime.service_registry import ServiceRegistry
    eb = EventBus()
    engine = CopilotEngine(event_bus=eb, ai_client=None, capability_router=None, settings_repo=None)
    sr = ServiceRegistry()
    sr.register(CopilotEngine, engine)
    resolved = sr.resolve(CopilotEngine)
    assert resolved is engine


def test_eventbus_routes_expected_events():
    from core.events import EventBus, AppEvent
    events = []

    def on_inbox(event):
        events.append(("inbox", event.payload))

    def on_input(event):
        events.append(("input", event.payload))

    eb = EventBus()
    eb.on("inbox.delivered", on_inbox)
    eb.on("input.captured", on_input)
    eb.emit(AppEvent(event_type="inbox.delivered", payload={"title": "test"}))
    eb.emit(AppEvent(event_type="input.captured", payload={"text": "hello"}))
    assert len(events) == 2
