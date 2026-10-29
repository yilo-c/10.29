# -*- coding: utf-8 -*-
"""第二档 #5：事件驱动主动性（把"定时"扩成"听事件"）闭环测试。

验证 ProactiveTriggerEngine 经 subscribe_events 接入 EventBus 后，真实事件
（selection / clipboard / foreground / url / notification / calendar）能驱动出一条
管家消息（统一走 deliver() 管道：inbox + proactive.triggered + proactive.suggestion），
且：
- 冷却生效（高频事件不刷屏）
- 防回环（notification.delivered 来源为 proactive.* 时不二次触发）
"""

from __future__ import annotations

from core.events import AppEvent, EventBus

from services.proactive_triggers import ProactiveTriggerEngine


class _FakeInbox:
    def __init__(self) -> None:
        self.delivered: list[dict] = []

    def deliver(self, title="", content="", channel="inbox", priority="normal", source="proactive", source_id="", tags=None, metadata=None) -> dict:
        self.delivered.append(
            {
                "title": title,
                "content": content,
                "source": source,
                "metadata": metadata or {},
            }
        )
        return {"id": "fake"}


def _make_engine() -> tuple[ProactiveTriggerEngine, _FakeInbox, EventBus, list[str]]:
    inbox = _FakeInbox()
    bus = EventBus()
    engine = ProactiveTriggerEngine(inbox_service=inbox, event_bus=bus)
    engine.subscribe_events(bus)
    emitted: list[str] = []
    bus.on("proactive.triggered", lambda e: emitted.append(e.event_type))
    bus.on("proactive.suggestion", lambda e: emitted.append(e.event_type))
    return engine, inbox, bus, emitted


def test_selection_event_produces_butler_message():
    _engine, inbox, bus, emitted = _make_engine()
    bus.emit(AppEvent("selection.detected", payload={"text": "这是一段被选中的长文本"}))
    msgs = [m for m in inbox.delivered if m["source"] == "proactive_selection"]
    assert msgs, "selection 事件未产出管家消息"
    assert "proactive.triggered" in emitted
    assert "proactive.suggestion" in emitted  # Orb 死路径修复验证


def test_clipboard_event_produces_butler_message():
    _engine, inbox, bus, _ = _make_engine()
    bus.emit(AppEvent("clipboard.changed", payload={"text": "刚复制的代码片段"}))
    msgs = [m for m in inbox.delivered if m["source"] == "proactive_clipboard"]
    assert msgs, "clipboard 事件未产出管家消息"


def test_url_and_foreground_and_calendar_events():
    _engine, inbox, bus, _ = _make_engine()
    bus.emit(AppEvent("url.intercepted", payload={"url": "https://example.com/doc"}))
    bus.emit(AppEvent("foreground.window.changed", payload={"title": "VSCode"}))
    bus.emit(AppEvent("calendar.reminder", payload={"title": "周会", "time": "15:00"}))
    sources = {m["source"] for m in inbox.delivered}
    assert "proactive_url_intercept" in sources
    assert "proactive_foreground" in sources
    assert "proactive_calendar" in sources


def test_notification_event_produces_butler_but_skips_proactive_source():
    _engine, inbox, bus, _ = _make_engine()
    # 真实通知（如 task.due）应转译为管家消息
    bus.emit(
        AppEvent(
            "notification.delivered",
            payload={
                "event_type": "task.due",
                "title": "任务到期",
                "body": "写周报",
            },
        )
    )
    real = [m for m in inbox.delivered if m["source"] == "proactive_notification"]
    assert real, "非 proactive 来源的通知未产出管家消息"

    before = len(inbox.delivered)
    # 回环防护：proactive.triggered 自身经 NotificationDispatcher 落库后会 emit
    # notification.delivered（event_type=proactive.triggered），必须被跳过
    bus.emit(
        AppEvent(
            "notification.delivered",
            payload={
                "event_type": "proactive.triggered",
                "title": "主动建议",
                "body": "x",
            },
        )
    )
    after = len(inbox.delivered)
    assert after == before, "proactive.* 来源的通知不应二次触发管家消息（回环）"


def test_cooldown_suppresses_rapid_repeats():
    _engine, inbox, bus, _ = _make_engine()
    bus.emit(AppEvent("selection.detected", payload={"text": "重复触发的选中文本"}))
    bus.emit(AppEvent("selection.detected", payload={"text": "重复触发的选中文本"}))
    msgs = [m for m in inbox.delivered if m["source"] == "proactive_selection"]
    assert len(msgs) == 1, "冷却未生效：30s 内重复 selection 触发了多次"


def test_disabled_trigger_is_ignored():
    _engine, inbox, bus, _ = _make_engine()
    _engine.set_enabled_triggers({"idle"})  # 关闭所有事件触发器
    bus.emit(AppEvent("clipboard.changed", payload={"text": "应被忽略的复制"}))
    msgs = [m for m in inbox.delivered if m["source"] == "proactive_clipboard"]
    assert not msgs, "已禁用的触发器不应响应事件"


def test_subscribe_is_idempotent_on_same_bus():
    inbox = _FakeInbox()
    bus = EventBus()
    e1 = ProactiveTriggerEngine(inbox_service=inbox, event_bus=bus)
    e2 = ProactiveTriggerEngine(inbox_service=inbox, event_bus=bus)
    e1.subscribe_events(bus)
    e2.subscribe_events(bus)  # 应替换而非叠加 handler
    bus.emit(AppEvent("clipboard.changed", payload={"text": "幂等验证"}))
    # 只有最新引擎（e2）的 handler 生效 → 仅 1 条消息，无重复投递
    msgs = [m for m in inbox.delivered if m["source"] == "proactive_clipboard"]
    assert len(msgs) == 1, "subscribe_events 非幂等：同一总线出现重复 handler"
