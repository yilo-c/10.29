# -*- coding: utf-8 -*-
"""event.emit RPC + 事件驱动主动性的端到端验证（第二档 #5）。

覆盖：
- event.emit 白名单拒绝 / 接受；
- 前端经 event.emit 注入 selection.detected → ProactiveTriggerEngine 产出
  proactive.suggestion（即"听事件"闭环从前端可达）；
- browser.captured → url.intercepted 桥接。
"""

from __future__ import annotations

import asyncio
from typing import Any


from core.events import AppEvent, event_bus
from services.proactive_triggers import ProactiveTriggerEngine


class _FakeInbox:
    def __init__(self) -> None:
        self.delivered: list[dict[str, Any]] = []
        self.events: list[AppEvent] = []

    def deliver(self, **kwargs: Any) -> dict[str, Any]:
        self.delivered.append(kwargs)
        return {"id": len(self.delivered)}

    def on_event(self, ev: AppEvent) -> None:
        self.events.append(ev)


def _make_engine() -> tuple[ProactiveTriggerEngine, _FakeInbox]:
    inbox = _FakeInbox()
    engine = ProactiveTriggerEngine(inbox_service=inbox, event_bus=event_bus)
    engine.subscribe_events(event_bus)
    # 监听 proactive.suggestion（前端 Orb 实际订阅的事件）
    event_bus.on("proactive.suggestion", inbox.on_event)
    return engine, inbox


def test_event_emit_allowlist():
    from backend.handlers._event_client import register_event_client

    calls: dict[str, Any] = {}

    class _Reg:
        def register(self, name: str, fn):
            calls[name] = fn

    register_event_client(_Reg(), context=None)
    assert "event.emit" in calls
    emit = calls["event.emit"]

    bad = asyncio.run(emit(event_type="evil.hack", payload={"x": 1}))
    assert bad["success"] is False

    ok = asyncio.run(emit(event_type="selection.detected", payload={"text": "选中文本"}))
    assert ok["success"] is True


def test_selection_detected_fires_suggestion():
    event_bus.clear()
    engine, inbox = _make_engine()
    try:
        # 前端经 event.emit 注入划词事件
        event_bus.emit(
            AppEvent(
                event_type="selection.detected",
                payload={"text": "这是一段被选中的长文本，用来测试事件驱动主动性"},
                source="client",
            )
        )
        # 应产出一条 proactive.suggestion
        sugg = [e for e in inbox.events if e.event_type == "proactive.suggestion"]
        assert sugg, "selection.detected 未触发 proactive.suggestion"
        assert sugg[0].payload["trigger"] == "selection"
        assert len(inbox.delivered) >= 1
    finally:
        engine.subscribe_events.__wrapped__ if hasattr(engine.subscribe_events, "__wrapped__") else None


def test_browser_captured_bridges_to_url_intercept():
    event_bus.clear()
    engine, inbox = _make_engine()
    try:
        event_bus.emit(
            AppEvent(
                event_type="browser.captured",
                payload={"title": "某网页", "url": "https://example.com/article"},
                source="browser_bridge",
            )
        )
        sugg = [e for e in inbox.events if e.event_type == "proactive.suggestion"]
        assert sugg, "browser.captured 未桥接为 url 触发"
        assert sugg[0].payload["trigger"] == "url_intercept"
    finally:
        pass
