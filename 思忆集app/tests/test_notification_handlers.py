"""P2.9 通知推送通道 — JSON-RPC handlers 单元测试。

2026-10-15 恢复：notification.get / preferences.get / preferences.set 曾被
58e5fa8（09-23 波次①-c）作为「僵尸 RPC」下线并移除行为测试；全族复核确认
前端在调（通知设置面板），注册与测试一并恢复。
"""

from __future__ import annotations

import copy
import sys
from pathlib import Path
from typing import Any

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

import pytest

from backend.handlers._notifications import register_notifications
from core.events import AppEvent
from services.notification_dispatcher import (
    DEFAULT_SUBSCRIBED_EVENTS,
    NotificationDispatcher,
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
    def __init__(self) -> None:
        self.events: list[AppEvent] = []
        self.subscriptions: list[tuple[str, Any]] = []

    def on(self, pattern: str, handler) -> None:
        self.subscriptions.append((pattern, handler))

    def off(self, pattern: str, handler) -> None:
        self.subscriptions = [(p, h) for (p, h) in self.subscriptions if not (p == pattern and h == handler)]

    def emit(self, event: AppEvent) -> bool:
        self.events.append(event)
        return True


class _FakeContext:
    def __init__(self, dispatcher: NotificationDispatcher, store: _FakeDataStore, settings: _FakeSettings) -> None:
        self.notification_dispatcher = dispatcher
        self.local_data_store = store
        self.settings_repository = settings
        self.event_bus = getattr(dispatcher, "_event_bus", None)


class _FakeRegistry:
    def __init__(self) -> None:
        self.methods: dict[str, Any] = {}

    def register(self, name: str, fn) -> None:
        self.methods[name] = fn


@pytest.fixture
def store():
    return _FakeDataStore()


@pytest.fixture
def settings():
    return _FakeSettings()


@pytest.fixture
def bus():
    return _FakeEventBus()


@pytest.fixture
def methods(store, settings, bus):
    dispatcher = NotificationDispatcher(data_store=store, settings_repository=settings, event_bus=bus)
    ctx = _FakeContext(dispatcher, store, settings)
    registry = _FakeRegistry()
    register_notifications(registry, ctx)
    return registry.methods


@pytest.fixture
def ctx_without_dispatcher():
    """context 无 notification_dispatcher 且无数据存储，懒构建失败路径。"""
    ctx = _FakeContext(None, None, None)
    ctx.notification_dispatcher = None
    ctx.local_data_store = None
    ctx.settings_repository = None
    return ctx


async def _seed(store, event_type="inbox.delivered", **extra):
    notification = {
        "id": "seed-1",
        "event_type": event_type,
        "title": "种子通知",
        "body": "body",
        "priority": "normal",
        "read": False,
        "created_at": "2026-08-04T00:00:00+00:00",
    }
    notification.update(extra)
    records = store.load_json("notifications", [])
    records.append(notification)
    store.save_json("notifications", records)
    return notification


# ── 注册 ────────────────────────────────────────────────────


def test_registers_all_methods(methods):
    expected = {
        "notification.list",
        "notification.get",
        "notification.mark_read",
        "notification.mark_all_read",
        "notification.delete",
        "notification.stats",
        "notification.test",
        "notification.preferences.get",
        "notification.preferences.set",
    }
    assert set(methods) >= expected


# ── list / get ──────────────────────────────────────────────


async def test_list_returns_items_desc(methods, store):
    await _seed(store, event_type="vocabulary.due_reminder", id="a")
    await _seed(store, event_type="inbox.delivered", id="b")
    result = await methods["notification.list"](unread_only=False, event_type="", limit=50)
    assert "error" not in result
    assert len(result["items"]) == 2
    assert result["items"][0]["id"] == "b"  # 倒序


async def test_list_unread_filter(methods, store):
    await _seed(store, id="read", read=True)
    await _seed(store, id="unread", read=False)
    result = await methods["notification.list"](unread_only=True, event_type="", limit=50)
    assert [i["id"] for i in result["items"]] == ["unread"]


async def test_list_dispatcher_unavailable(methods, ctx_without_dispatcher):
    registry = _FakeRegistry()
    register_notifications(registry, ctx_without_dispatcher)
    result = await registry.methods["notification.list"]()
    assert result == {"items": [], "error": "dispatcher unavailable"}


async def test_get_requires_id(methods):
    result = await methods["notification.get"]()
    assert result["error"] == "notification_id is required"


async def test_get_not_found(methods):
    result = await methods["notification.get"](notification_id="nope")
    assert result["error"] == "not found"


async def test_get_ok(methods, store):
    await _seed(store)
    result = await methods["notification.get"](notification_id="seed-1")
    assert result["notification"]["id"] == "seed-1"


# ── mark_read / mark_all_read / delete ──────────────────────


async def test_mark_read_requires_id(methods):
    result = await methods["notification.mark_read"]()
    assert result["error"] == "notification_id is required"


async def test_mark_read_ok(methods, store):
    await _seed(store, read=False)
    result = await methods["notification.mark_read"](notification_id="seed-1", read=True)
    assert result["updated"] is True
    assert result["read"] is True
    assert store.load_json("notifications", [])[0]["read"] is True


async def test_mark_all_read(methods, store):
    await _seed(store, read=False, id="a")
    await _seed(store, read=True, id="b")
    result = await methods["notification.mark_all_read"]()
    assert result["updated_count"] == 1


async def test_delete_ok(methods, store):
    await _seed(store)
    result = await methods["notification.delete"](notification_id="seed-1")
    assert result["deleted"] is True
    assert store.load_json("notifications", []) == []


async def test_delete_missing(methods):
    result = await methods["notification.delete"](notification_id="nope")
    assert result["deleted"] is False


# ── stats / test ────────────────────────────────────────────


async def test_stats(methods, store):
    await _seed(store, read=False)
    result = await methods["notification.stats"]()
    assert result["total"] == 1
    assert result["unread"] == 1
    assert result["channels"]


async def test_test_channel_websocket(methods):
    result = await methods["notification.test"](channel="websocket")
    assert result["ok"] is True


async def test_test_channel_unknown(methods):
    result = await methods["notification.test"](channel="nope")
    assert result["ok"] is False


# ── preferences ─────────────────────────────────────────────


async def test_preferences_get(methods):
    result = await methods["notification.preferences.get"]()
    assert result["enabled"] is True
    assert result["preferences"] == {}
    assert set(result["subscribed_events"]) == set(DEFAULT_SUBSCRIBED_EVENTS)
    assert set(result["available_events"]) == set(DEFAULT_SUBSCRIBED_EVENTS)


async def test_preferences_set_enabled(methods, settings):
    result = await methods["notification.preferences.set"](enabled=False)
    assert result["success"] is True
    assert settings.get_setting("notifications_enabled") is False


async def test_preferences_set_rejects_non_dict(methods):
    result = await methods["notification.preferences.set"](preferences=["bad"])
    assert result["error"] == "preferences must be a dict"


async def test_preferences_set_rejects_non_list(methods):
    result = await methods["notification.preferences.set"](subscribed_events="bad")
    assert result["error"] == "subscribed_events must be a list"
