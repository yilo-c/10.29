"""P2.9 通知推送通道 — NotificationDispatcher / Channel 单元测试。"""

from __future__ import annotations

import copy
import sys
from pathlib import Path
from typing import Any

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

import pytest

from core.events import AppEvent
from services.notification_dispatcher import (
    DEFAULT_SUBSCRIBED_EVENTS,
    DesktopToastChannel,
    NotificationDispatcher,
    WebSocketChannel,
)


class _FakeDataStore:
    def __init__(self) -> None:
        self._data: dict[str, Any] = {}

    def load_json(self, key: str, default: Any = None):
        return copy.deepcopy(self._data.get(key, default))

    def save_json(self, key: str, data) -> None:
        self._data[key] = copy.deepcopy(data)


class _FakeSettings:
    def __init__(self) -> None:
        self._data: dict[str, Any] = {"notifications_enabled": True}

    def get_setting(self, key: str, default: Any = None):
        return self._data.get(key, default)

    def set_setting(self, key: str, value: Any) -> None:
        self._data[key] = value


class _FakeEventBus:
    """记录 emit 的事件；on/off 记录订阅，供 start/stop 使用。"""

    def __init__(self) -> None:
        self.events: list[AppEvent] = []
        self.subscriptions: list[tuple[str, Any]] = []

    def on(self, pattern: str, handler) -> None:
        self.subscriptions.append((pattern, handler))

    def off(self, pattern: str, handler) -> None:
        self.subscriptions = [
            (p, h) for (p, h) in self.subscriptions if not (p == pattern and h == handler)
        ]

    def emit(self, event: AppEvent) -> bool:
        self.events.append(event)
        return True


@pytest.fixture
def bus():
    return _FakeEventBus()


@pytest.fixture
def store():
    return _FakeDataStore()


@pytest.fixture
def settings():
    return _FakeSettings()


@pytest.fixture
def dispatcher(store, settings, bus):
    d = NotificationDispatcher(data_store=store, settings_repository=settings, event_bus=bus)
    return d


def _event_types(bus: _FakeEventBus) -> list[str]:
    return [e.event_type for e in bus.events]


# ── 开关 / 订阅白名单 ───────────────────────────────────────

def test_is_enabled_default_true(dispatcher):
    assert dispatcher.is_enabled() is True


def test_is_enabled_false_when_disabled(dispatcher, settings):
    settings.set_setting("notifications_enabled", False)
    assert dispatcher.is_enabled() is False


def test_subscribed_events_default(dispatcher):
    assert dispatcher._subscribed_events() == DEFAULT_SUBSCRIBED_EVENTS


def test_subscribed_events_uses_setting(dispatcher, settings):
    settings.set_setting("notification_subscribed_events", ["inbox.delivered"])
    assert dispatcher._subscribed_events() == ["inbox.delivered"]


# ── dispatch 过滤分支 ───────────────────────────────────────

async def test_dispatch_disabled(dispatcher, settings):
    settings.set_setting("notifications_enabled", False)
    result = await dispatcher.dispatch("inbox.delivered", {"title": "x"})
    assert result == {"dispatched": False, "reason": "disabled"}


async def test_dispatch_not_subscribed(dispatcher):
    result = await dispatcher.dispatch("unknown.event", {"message": "x"})
    assert result == {"dispatched": False, "reason": "event not subscribed"}


async def test_dispatch_sync_failed_non_fatal_filtered(dispatcher):
    result = await dispatcher.dispatch("sync.failed", {"error": "boom"})
    assert result == {"dispatched": False, "reason": "sync.failed not fatal"}


async def test_dispatch_sync_failed_fatal_passes(dispatcher, store, bus):
    result = await dispatcher.dispatch("sync.failed", {"error": "boom", "fatal": True})
    assert result["dispatched"] is True
    assert result["notification"]["priority"] == "urgent"
    assert "notification.delivered" in _event_types(bus)
    assert len(store.load_json("notifications", [])) == 1


async def test_dispatch_below_min_priority(dispatcher, store, bus):
    # inbox.delivered 默认 min_priority=normal；显式 low 应被过滤
    result = await dispatcher.dispatch("inbox.delivered", {"title": "hi", "priority": "low"})
    assert result == {"dispatched": False, "reason": "below min_priority"}
    assert store.load_json("notifications", []) == []
    assert not bus.events


# ── dispatch 成功路径 ───────────────────────────────────────

async def test_dispatch_success_persists_and_emits(dispatcher, store, bus):
    result = await dispatcher.dispatch(
        "vocabulary.due_reminder", {"message": "10 个单词到期了", "count": 10}
    )
    assert result["dispatched"] is True
    notification = result["notification"]
    assert notification["event_type"] == "vocabulary.due_reminder"
    assert notification["title"] == "单词到期复习"
    assert notification["body"] == "10 个单词到期了"
    assert notification["priority"] == "normal"
    assert notification["read"] is False
    assert notification["id"]
    assert notification["created_at"]
    assert "notification.delivered" in _event_types(bus)
    assert len(store.load_json("notifications", [])) == 1


async def test_dispatch_payload_title_override(dispatcher):
    result = await dispatcher.dispatch(
        "inbox.delivered", {"title": "正文", "notification_title": "自定义标题"}
    )
    notification = result["notification"]
    assert notification["title"] == "自定义标题"
    assert notification["body"] == "正文"


# ── 持久化 LRU ──────────────────────────────────────────────

def test_persist_lru_caps_at_500(dispatcher, store):
    for i in range(520):
        dispatcher._persist({"id": str(i), "event_type": "task.due", "priority": "normal"})
    records = store.load_json("notifications", [])
    assert len(records) == 500
    assert records[0]["id"] == "20"  # 最旧的 20 条被丢弃


# ── CRUD ────────────────────────────────────────────────────

async def test_crud_lifecycle(dispatcher, store):
    await dispatcher.dispatch("inbox.delivered", {"title": "hello"})
    items = dispatcher.list_notifications()
    assert len(items) == 1
    nid = items[0]["id"]

    # list 过滤
    assert dispatcher.list_notifications(unread_only=True) == items
    assert dispatcher.list_notifications(unread_only=False) == items
    assert dispatcher.list_notifications(event_type="inbox.delivered") == items
    assert dispatcher.list_notifications(event_type="other") == []
    # limit 下限为 1（契约：max(1, min(500, int(limit)))）
    assert len(dispatcher.list_notifications(limit=0)) == 1
    assert len(dispatcher.list_notifications(limit=10)) == 1

    # get / mark_read / mark_all_read / delete
    assert dispatcher.get_notification(nid)["id"] == nid
    assert dispatcher.get_notification("missing") is None
    assert dispatcher.mark_read(nid, read=False) is True
    assert dispatcher.get_notification(nid)["read"] is False
    assert dispatcher.mark_read("missing") is False
    assert dispatcher.mark_all_read() == 1
    assert dispatcher.mark_all_read() == 0
    assert dispatcher.get_notification(nid)["read"] is True
    assert dispatcher.delete_notification(nid) is True
    assert dispatcher.delete_notification("missing") is False
    assert store.load_json("notifications", []) == []


# ── 统计 ────────────────────────────────────────────────────

async def test_get_stats(dispatcher, store):
    await dispatcher.dispatch("vocabulary.due_reminder", {"message": "a"})
    await dispatcher.dispatch("inbox.delivered", {"title": "b"})
    stats = dispatcher.get_stats()
    assert stats["total"] == 2
    assert stats["unread"] == 2
    assert stats["by_event"] == {"vocabulary.due_reminder": 1, "inbox.delivered": 1}
    assert stats["by_priority"] == {"low": 0, "normal": 2, "high": 0, "urgent": 0}
    assert stats["channels"] == [
        {"name": "websocket", "healthy": True},
        {"name": "toast", "healthy": True},
    ]
    assert stats["enabled"] is True
    assert set(stats["subscribed_events"]) == set(DEFAULT_SUBSCRIBED_EVENTS)


# ── 通道 ────────────────────────────────────────────────────

async def test_websocket_channel_emits_delivered(bus):
    ch = WebSocketChannel(bus)
    ok = await ch.send({"id": "1", "event_type": "task.due", "priority": "normal"})
    assert ok is True
    assert "notification.delivered" in _event_types(bus)
    assert bus.events[-1].payload["event_type"] == "task.due"


async def test_desktop_toast_channel_low_priority_skipped(bus):
    ch = DesktopToastChannel(bus)
    ok = await ch.send({"id": "1", "event_type": "task.due", "priority": "normal"})
    assert ok is False
    assert "notification.toast" not in _event_types(bus)


async def test_desktop_toast_channel_high_priority_emits(bus):
    ch = DesktopToastChannel(bus)
    ok = await ch.send({"id": "1", "event_type": "sync.failed", "priority": "urgent", "title": "t", "body": "b"})
    assert ok is True
    assert "notification.toast" in _event_types(bus)
    payload = bus.events[-1].payload
    assert payload["title"] == "t"
    assert payload["body"] == "b"
    assert payload["priority"] == "urgent"


# ── 订阅管理 / 测试通道 ─────────────────────────────────────

async def test_start_subscribes_whitelist(dispatcher, bus):
    dispatcher.start()
    subscribed = {pattern for pattern, _ in bus.subscriptions}
    assert subscribed == set(DEFAULT_SUBSCRIBED_EVENTS)
    dispatcher.stop()
    assert bus.subscriptions == []
    dispatcher.start()  # 重复 start 安全
    dispatcher.start()
    assert {p for p, _ in bus.subscriptions} == set(DEFAULT_SUBSCRIBED_EVENTS)


async def test_test_channel_ok_and_unknown(dispatcher):
    ok_result = await dispatcher.test_channel("websocket")
    assert ok_result["ok"] is True
    assert ok_result["channel"] == "websocket"
    assert ok_result["notification"]["title"] == "测试通知"

    bad_result = await dispatcher.test_channel("nope")
    assert bad_result["ok"] is False
    assert "unknown channel" in bad_result["error"]


# ── 偏好配置 ────────────────────────────────────────────────

def test_get_event_config_defaults(dispatcher):
    cfg = dispatcher._get_event_config("inbox.delivered")
    assert cfg == {"ws": True, "toast": True, "min_priority": "normal"}


def test_get_event_config_from_preferences(dispatcher, settings):
    settings.set_setting("notification_preferences", {
        "inbox.delivered": {"ws": False, "toast": True, "min_priority": "high"},
    })
    cfg = dispatcher._get_event_config("inbox.delivered")
    assert cfg == {"ws": False, "toast": True, "min_priority": "high"}


async def test_dispatch_respects_ws_preference(dispatcher, settings, store, bus):
    # 关闭 ws 通道后，delivered 不应发出，但仍持久化
    settings.set_setting("notification_preferences", {
        "inbox.delivered": {"ws": False, "toast": False, "min_priority": "normal"},
    })
    result = await dispatcher.dispatch("inbox.delivered", {"title": "x"})
    assert result["dispatched"] is True
    assert "notification.delivered" not in _event_types(bus)
    assert len(store.load_json("notifications", [])) == 1
