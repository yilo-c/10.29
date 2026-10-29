# -*- coding: utf-8 -*-
"""E2E 收件箱 + 跨模块协作链路：投递 → 主动建议 → 通知 → 事件推送到客户端。

只测不改源码。缺口只登记在本文件（新增测试），不改任何被测代码。

分两层：

Layer A（离线真实类链）——用真实业务类 + 真实 ``core.events.EventBus`` 把跨模块
链路在**单进程内**串起来，不依赖后端进程：

    AssistantInboxService.deliver(source/metadata/tags)
        → emit inbox.delivered (P2.3 透传 metadata/tags)
        → NotificationDispatcher 白名单订阅 (DEFAULT_SUBSCRIBED_EVENTS)
        → dispatch → 持久化 + WebSocketChannel emit notification.delivered
        → (high/urgent 才) DesktopToastChannel emit notification.toast

    ProactiveTriggerEngine (idle / timed / selection 事件驱动)
    SuggestionEngine (无 AI 时的规则兜底，不崩)
    僵尸 RPC：notification.get / notification.preferences.* 未注册；notification.test 保留。

Layer A-shape（形漂移登记，读前端 desk-notes 源码 + 后端生产者，只登记不修）：
    inbox.delivered 前端 TS 缺 metadata/tags/priority；
    proactive.suggestion 三生产者（proactive_triggers=content / suggestion_engine=suggestions /
    前端消费者=description）字段互不对齐。

Layer B（活体 WS 传输腿）——SIYI_INTEGRATION_AUTOSTART 自启隔离后端，验证
EventBridge → WebSocket 真正把服务端 EventBus 事件推到客户端连接：

    notification.test(channel=websocket) → 客户端真实收到 notification.delivered 事件帧
    event.emit(selection.detected)       → 客户端收到 selection.detected 回推事件帧
    僵尸 RPC 过线                       → notification.get / preferences.* 回 -32601

诚实边界：Electron 渲染层（真实浏览器/桌面前端）未跑；Layer B 只覆盖后端进程到 WS
客户端这一传输腿，前端 TS 消费者是否真的渲染出 toast 属渲染层，未测（见 shape 漂移
与最终报告的"实收事件"清单）。
"""

from __future__ import annotations

import asyncio
import copy
import json
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import aiohttp
import pytest

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

REPO_ROOT = APP_ROOT.parent
DESK_NOTES_DIR = REPO_ROOT / "desk-notes"

from assistant.delivery.inbox import AssistantInboxService  # noqa: E402
from backend.handlers._notifications import register_notifications  # noqa: E402
from core.events import AppEvent, EventBus, event_bus as GLOBAL_BUS  # noqa: E402
from services.notification_dispatcher import (  # noqa: E402
    DEFAULT_SUBSCRIBED_EVENTS,
    NotificationDispatcher,
)
from services.proactive_triggers import ProactiveTriggerEngine  # noqa: E402
from services.suggestion_engine import SuggestionEngine  # noqa: E402


# ═══════════════════════════════════════════════════════════════════════════
# 离线 fake 基元（仅数据/设置替身；业务类全部用真实实现）
# ═══════════════════════════════════════════════════════════════════════════


class _FakeDataStore:
    """满足 NotificationDispatcher(load/save) 与 AssistantInboxService(update_json)。"""

    def __init__(self) -> None:
        self._data: dict[str, Any] = {}

    def load_json(self, key: str, default: Any = None):
        return copy.deepcopy(self._data.get(key, default))

    def save_json(self, key: str, data) -> None:
        self._data[key] = copy.deepcopy(data)

    def update_json(self, key: str, default: Any, updater) -> Any:
        current = self._data.get(key, default)
        new_value = updater(copy.deepcopy(current) if isinstance(current, list) else current)
        self._data[key] = new_value
        return new_value


class _FakeSettings:
    def __init__(self) -> None:
        self._data: dict[str, Any] = {"notifications_enabled": True}

    def get_setting(self, key: str, default: Any = None):
        return self._data.get(key, default)

    def set_setting(self, key: str, value: Any) -> None:
        self._data[key] = value


class _FakeRegistry:
    def __init__(self) -> None:
        self.methods: dict[str, Any] = {}

    def register(self, name: str, fn) -> None:
        self.methods[name] = fn


class _FakeInbox:
    """ProactiveTriggerEngine / SuggestionEngine 的收件箱替身，记录 deliver 参数。"""

    def __init__(self) -> None:
        self.delivered: list[dict[str, Any]] = []

    def deliver(
        self,
        title="",
        content="",
        channel="inbox",
        priority="normal",
        source="proactive",
        source_id="",
        tags=None,
        metadata=None,
    ) -> dict[str, Any]:
        self.delivered.append(
            {
                "title": title,
                "content": content,
                "channel": channel,
                "priority": priority,
                "source": source,
                "source_id": source_id,
                "tags": tags or [],
                "metadata": metadata or {},
            }
        )
        return {"id": f"fake-{len(self.delivered)}"}


class _FakeProfileService:
    """SuggestionEngine 规则兜底用的最小画像服务。"""

    def __init__(self, payload: dict[str, Any] | None = None) -> None:
        self._payload = payload or {
            "long_term": {"focus_topics": ["AI 评测", "知识库"], "preferred_tools": ["笔记", "收件箱"]},
            "context": {"habit_hours": ["09:00", "14:00"], "current_focus": "端到端测试"},
            "permission": {"allow_proactive_push": True},
        }

    def load(self) -> dict[str, Any]:
        return copy.deepcopy(self._payload)


def _event_types(bus: EventBus) -> list[str]:
    return [e.event_type for e in bus.recent(limit=500)]


async def _drain_until(bus: EventBus, want_type: str, timeout: float = 3.0) -> bool:
    """等待 dispatcher 的 create_task 调度协程跑完（bus 历史里出现 want_type）。"""
    end = time.time() + timeout
    while time.time() < end:
        if any(e.event_type == want_type for e in bus.recent(limit=500)):
            return True
        await asyncio.sleep(0.02)
    return any(e.event_type == want_type for e in bus.recent(limit=500))


def _find_event(bus: EventBus, want_type: str) -> AppEvent | None:
    for e in bus.recent(limit=500):
        if e.event_type == want_type:
            return e
    return None


# ═══════════════════════════════════════════════════════════════════════════
# Layer A：投递 → 通知管道（真实类 + 真实 EventBus，离线）
# ═══════════════════════════════════════════════════════════════════════════


async def test_inbox_deliver_emits_metadata_and_tags():
    """AssistantInboxService.deliver 的 inbox.delivered 事件 payload 含 P2.3 metadata/tags。

    注：AssistantInboxService 的 _event_bus 走模块级 core.events.event_bus 单例
    （构造签名不注入 event_bus），故本用例订阅/清空全局总线。
    """
    GLOBAL_BUS.clear()
    seen: list[AppEvent] = []
    GLOBAL_BUS.on("inbox.delivered", lambda e: seen.append(e))
    store = _FakeDataStore()
    inbox = AssistantInboxService(data_store=store)
    try:
        inbox.deliver(
            title="任务完成",
            content="周报已生成",
            channel="inbox",
            priority="normal",
            source="task_result",
            tags=["proactive", "selection"],
            metadata={"trigger": "selection", "item": 42},
        )
    finally:
        GLOBAL_BUS.clear()

    assert len(seen) == 1, "deliver 未 emit inbox.delivered"
    payload = seen[0].payload
    assert payload["metadata"] == {"trigger": "selection", "item": 42}, "P2.3 metadata 未透传"
    assert payload["tags"] == ["proactive", "selection"], "P2.3 tags 未透传"
    assert payload["item_id"] and payload["title"] == "任务完成"
    assert payload["source"] == "task_result"


async def test_inbox_delivered_whitelist_to_notification_delivered():
    """inbox.delivered → NotificationDispatcher 白名单订阅 → 落库 + emit notification.delivered。

    投递与分发共享 core.events.event_bus 单例（AssistantInboxService 硬编码该总线），
    证明白名单订阅 handler 真正把 inbox.delivered 拉进通知管道。
    """
    GLOBAL_BUS.clear()
    store = _FakeDataStore()
    settings = _FakeSettings()
    dispatcher = NotificationDispatcher(data_store=store, settings_repository=settings, event_bus=GLOBAL_BUS)
    dispatcher.start()  # 订阅 DEFAULT_SUBSCRIBED_EVENTS
    assert "inbox.delivered" in DEFAULT_SUBSCRIBED_EVENTS

    note_evt = None
    try:
        inbox = AssistantInboxService(data_store=_FakeDataStore())
        inbox.deliver(title="新条目", content="x", priority="normal", metadata={"trigger": "idle"}, tags=["t"])
        assert await _drain_until(GLOBAL_BUS, "notification.delivered"), "白名单事件未产出 notification.delivered"
        note_evt = _find_event(GLOBAL_BUS, "notification.delivered")
    finally:
        dispatcher.stop()
        GLOBAL_BUS.clear()

    assert note_evt is not None
    # 落库 1 条
    assert len(store.load_json("notifications", [])) == 1
    assert note_evt.payload["event_type"] == "inbox.delivered"
    assert note_evt.payload["priority"] == "normal"
    # raw 里保留 P2.3 metadata（链路端到端不丢上下文）
    assert note_evt.payload.get("raw", {}).get("metadata") == {"trigger": "idle"}


async def test_normal_inbox_delivered_no_toast():
    """normal 优先级 inbox.delivered 进管道但不触发 toast（toast 仅 high/urgent）。"""
    bus = EventBus()
    dispatcher = NotificationDispatcher(data_store=_FakeDataStore(), settings_repository=_FakeSettings(), event_bus=bus)
    dispatcher.start()

    result = await dispatcher.dispatch("inbox.delivered", {"title": "普通条目", "priority": "normal"})
    assert result["dispatched"] is True
    assert "notification.delivered" in _event_types(bus)
    await asyncio.sleep(0.05)
    assert "notification.toast" not in _event_types(bus), "normal 不应触发 toast"
    dispatcher.stop()


async def test_urgent_sync_failed_fatal_delivered_and_toast():
    """urgent sync.failed{fatal:true} → delivered + toast（urgent ≥ high）。"""
    bus = EventBus()
    store = _FakeDataStore()
    dispatcher = NotificationDispatcher(data_store=store, settings_repository=_FakeSettings(), event_bus=bus)

    result = await dispatcher.dispatch("sync.failed", {"error": "boom", "fatal": True})
    assert result["dispatched"] is True
    assert result["notification"]["priority"] == "urgent"
    assert "notification.delivered" in _event_types(bus)
    assert "notification.toast" in _event_types(bus), "urgent 应触发 toast"
    toast_evt = _find_event(bus, "notification.toast")
    assert toast_evt.payload["priority"] == "urgent"
    assert len(store.load_json("notifications", [])) == 1


async def test_nonfatal_sync_failed_not_in_pipeline():
    """非 fatal 的 sync.failed 不进通知管道。"""
    bus = EventBus()
    store = _FakeDataStore()
    dispatcher = NotificationDispatcher(data_store=store, settings_repository=_FakeSettings(), event_bus=bus)

    result = await dispatcher.dispatch("sync.failed", {"error": "transient"})
    assert result == {"dispatched": False, "reason": "sync.failed not fatal"}
    assert store.load_json("notifications", []) == []
    assert not bus.recent(limit=500)


async def test_unknown_event_not_subscribed():
    """不在白名单的事件被拒。"""
    dispatcher = NotificationDispatcher(data_store=_FakeDataStore(), settings_repository=_FakeSettings(), event_bus=EventBus())
    result = await dispatcher.dispatch("some.random.event", {"message": "x"})
    assert result == {"dispatched": False, "reason": "event not subscribed"}


async def test_low_priority_inbox_below_min_priority_filtered():
    """显式 low 优先级低于 inbox.delivered 默认 min_priority=normal → 被过滤，不落库不 emit。"""
    bus = EventBus()
    store = _FakeDataStore()
    dispatcher = NotificationDispatcher(data_store=store, settings_repository=_FakeSettings(), event_bus=bus)
    result = await dispatcher.dispatch("inbox.delivered", {"title": "hi", "priority": "low"})
    assert result == {"dispatched": False, "reason": "below min_priority"}
    assert store.load_json("notifications", []) == []
    assert not bus.recent(limit=500)


async def test_delivery_to_notification_end_to_end():
    """链路：投递 → 通知管道 → 查询（list/stats）看到该通知（跨模块闭环，离线）。"""
    GLOBAL_BUS.clear()
    store = _FakeDataStore()
    dispatcher = NotificationDispatcher(data_store=store, settings_repository=_FakeSettings(), event_bus=GLOBAL_BUS)
    dispatcher.start()

    try:
        AssistantInboxService(data_store=_FakeDataStore()).deliver(
            title="闭环条目", content="c", priority="normal", source="task_result", metadata={"k": "v"}
        )
        assert await _drain_until(GLOBAL_BUS, "notification.delivered")
    finally:
        dispatcher.stop()
        GLOBAL_BUS.clear()

    items = dispatcher.list_notifications()
    assert len(items) == 1
    assert items[0]["event_type"] == "inbox.delivered"
    assert items[0]["read"] is False
    stats = dispatcher.get_stats()
    assert stats["total"] == 1 and stats["unread"] == 1
    assert stats["by_event"].get("inbox.delivered") == 1


# ── ProactiveTriggerEngine：idle / timed / selection ──────────────────────


async def test_selection_event_drives_butler_message():
    """selection.detected 事件驱动 → deliver() 管道产出 inbox + proactive.triggered + proactive.suggestion。"""
    bus = EventBus()
    inbox = _FakeInbox()
    engine = ProactiveTriggerEngine(inbox_service=inbox, event_bus=bus)
    engine.subscribe_events(bus)

    bus.emit(AppEvent("selection.detected", payload={"text": "这是一段足够长的被选中文本"}))

    assert any(m["source"] == "proactive_selection" for m in inbox.delivered), "selection 未产管家消息"
    assert "proactive.triggered" in _event_types(bus)
    assert "proactive.suggestion" in _event_types(bus)


async def test_idle_trigger_fires_on_tick():
    """idle 触发器：闲置超阈值时 tick() 返回 proactive_idle 项，deliver 后进入管道。"""
    bus = EventBus()
    inbox = _FakeInbox()
    engine = ProactiveTriggerEngine(inbox_service=inbox, event_bus=bus)
    engine.set_enabled_triggers({"idle"})
    engine._last_interaction_time = datetime.now() - timedelta(minutes=20)  # > 15min 阈值

    results = engine.tick(context={})
    assert any(i.get("source") == "proactive_idle" for i in results), "idle 未在 tick 中触发"

    idle_item = next(i for i in results if i.get("source") == "proactive_idle")
    engine.deliver(idle_item)
    assert any(m["source"] == "proactive_idle" for m in inbox.delivered)
    trig = _find_event(bus, "proactive.triggered")
    assert trig is not None and trig.payload["trigger"] == "idle"


async def test_timed_trigger_fires_on_tick():
    """timed 触发器：闲置 > 5min 且未到冷却时 tick() 返回 proactive_timed 项。"""
    bus = EventBus()
    inbox = _FakeInbox()
    engine = ProactiveTriggerEngine(inbox_service=inbox, event_bus=bus)
    engine.set_enabled_triggers({"timed"})
    engine._last_interaction_time = datetime.now() - timedelta(minutes=10)

    results = engine.tick(context={})
    assert any(i.get("source") == "proactive_timed" for i in results), "timed 未在 tick 中触发"


async def test_deliver_emits_triggered_and_suggestion_with_trigger_metadata():
    """deliver() 双 emit（proactive.triggered + proactive.suggestion），payload 带 trigger 上下文。"""
    bus = EventBus()
    inbox = _FakeInbox()
    engine = ProactiveTriggerEngine(inbox_service=inbox, event_bus=bus)

    engine.deliver(
        {
            "title": "T",
            "content": "C",
            "channel": "inbox",
            "priority": "normal",
            "source": "proactive_clipboard",
            "source_id": "s1",
            "tags": ["proactive"],
            "metadata": {"trigger": "clipboard", "text": "hello"},
        }
    )

    triggered = _find_event(bus, "proactive.triggered")
    suggestion = _find_event(bus, "proactive.suggestion")
    assert triggered is not None and triggered.payload["trigger"] == "clipboard"
    assert suggestion is not None and suggestion.payload["content"] == "C"
    assert suggestion.payload["tags"] == ["proactive"]


# ── SuggestionEngine：无 AI 规则兜底 ──────────────────────────────────────


async def test_suggestion_engine_rule_fallback_offline_no_crash():
    """有画像但 ai_client=None：走规则分析，rank 后 emit proactive.suggestion，不崩。"""
    bus = EventBus()
    engine = SuggestionEngine(user_profile_service=_FakeProfileService(), ai_client=None, event_bus=bus)

    ranked = engine.analyze()  # 不应联网、不应抛异常
    assert isinstance(ranked, list)
    assert len(ranked) >= 1, "规则兜底应至少产出一条建议（画像有习惯/关注话题）"
    # analyze() 排序后 emit proactive.suggestion（payload.suggestions 形态）
    evt = _find_event(bus, "proactive.suggestion")
    assert evt is not None
    assert "suggestions" in evt.payload


async def test_suggestion_engine_bare_no_deps_no_crash():
    """全裸依赖（无画像无 AI）：analyze() 返回空列表，不崩、不 emit。"""
    bus = EventBus()
    engine = SuggestionEngine(ai_client=None, event_bus=bus)
    ranked = engine.analyze()
    assert ranked == []
    assert "proactive.suggestion" not in _event_types(bus)


# ── 通知 RPC 注册面（离线） ──────────────────────────────────────────────
#
# 2026-10-15 反转：本组原断言 notification.get / preferences.* **未注册**（"僵尸"），
# 依据是 58e5fa8（09-23 波次①-c）下线时的结论「前端零调用、无测试耦合」。
# 全族复核证伪该结论（前端通知设置面板在调 notification.preferences.set），
# 注册已于批次 A（c6fb2bc7）恢复 → 断言改为「必须注册」，防止再次误下线。


def test_notification_get_registered():
    registry = _FakeRegistry()
    register_notifications(registry, _FakeCtx(dispatcher=_noop_dispatcher()))
    assert "notification.get" in registry.methods, "notification.get 前端在调，不得下线"


def test_notification_preferences_registered():
    registry = _FakeRegistry()
    register_notifications(registry, _FakeCtx(dispatcher=_noop_dispatcher()))
    assert "notification.preferences.get" in registry.methods
    assert "notification.preferences.set" in registry.methods


def test_notification_test_registered_and_usable():
    """notification.test 保留可用（非僵尸）：注册且在，且 websocket 通道返回 ok。"""
    bus = EventBus()
    dispatcher = NotificationDispatcher(data_store=_FakeDataStore(), settings_repository=_FakeSettings(), event_bus=bus)
    registry = _FakeRegistry()
    register_notifications(registry, _FakeCtx(dispatcher=dispatcher))
    assert "notification.test" in registry.methods
    result = asyncio.run(registry.methods["notification.test"](channel="websocket"))
    assert result["ok"] is True
    assert result["notification"]["test"] is True


# 离线通知 handler 测试所需的最小 context 替身 -------------------------------


def _noop_dispatcher() -> NotificationDispatcher:
    return NotificationDispatcher(data_store=_FakeDataStore(), settings_repository=_FakeSettings(), event_bus=EventBus())


class _FakeCtx:
    def __init__(self, dispatcher: NotificationDispatcher | None) -> None:
        self.notification_dispatcher = dispatcher
        self.local_data_store = getattr(dispatcher, "_store", None)
        self.settings_repository = getattr(dispatcher, "_settings", None)
        self.event_bus = getattr(dispatcher, "_event_bus", None)


# ═══════════════════════════════════════════════════════════════════════════
# Layer A-shape：形漂移登记（读源码，只登记不修；若被修复则本组翻红提醒）
# ═══════════════════════════════════════════════════════════════════════════


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def test_shape_inbox_delivered_frontend_missing_metadata_tags():
    """漂移1（登记）：前端 inbox.delivered TS 类型缺 metadata/tags/priority，且声明了后端不发的字段。"""
    fb = _read(DESK_NOTES_DIR / "packages" / "desktop" / "src" / "FloatingBubble" / "useBackendEvents.ts")
    # 定位 'inbox.delivered' 的类型声明行
    line = next((ln for ln in fb.splitlines() if "'inbox.delivered':" in ln), "")
    assert line, "未找到前端 inbox.delivered 类型声明（文件结构变更，请复核形漂移登记）"
    # 后端真实 payload 有 metadata / tags / priority；前端类型声明缺失
    assert "metadata" not in line, "登记失效：前端已补 metadata，请更新形漂移清单"
    assert "tags" not in line, "登记失效：前端已补 tags，请更新形漂移清单"
    # 前端反而声明了后端从不 emit 的字段
    assert "content_preview" in line and "category" in line, (
        "前端声明的 content_preview/category 与后端 item_id/title/channel/priority/metadata/tags 不对齐"
    )


def test_shape_proactive_suggestion_three_producers_misaligned():
    """漂移2（登记）：proactive.suggestion 三生产者/消费者字段互不对齐。

    - proactive_triggers  emit：payload.content（无 description）
    - suggestion_engine   emit：payload.suggestions[]（无 title / 无 description）
    - 前端消费者          读：payload.title + payload.description
    三者对不齐 → 前端 Orb/桌面通知拿不到 description，suggestion_engine 产出的事件因缺 title 被 useDesktopNotifications 直接丢弃。
    """
    triggers_src = _read(APP_ROOT / "services" / "proactive_triggers.py")
    suggestion_src = _read(APP_ROOT / "services" / "suggestion_engine.py")
    bubble_src = _read(DESK_NOTES_DIR / "packages" / "desktop" / "src" / "FloatingBubble" / "FloatingBubbleApp.tsx")
    desktop_notif_src = _read(DESK_NOTES_DIR / "packages" / "desktop" / "src" / "FloatingBubble" / "useDesktopNotifications.ts")

    # 生产者1：proactive_triggers 用 "content"，不用 "description"
    assert '"content": item.get("content"' in triggers_src
    assert "description" not in triggers_src.split("def _emit_proactive_event")[1][:900], "登记复核：proactive_triggers 已出现 description"
    # 生产者2：suggestion_engine 用 "suggestions"，payload 不含 title/description
    assert '"suggestions": [s.to_dict()' in suggestion_src
    seg = suggestion_src.split("def _emit_suggestion_event")[1][:900]
    assert '"title"' not in seg and '"description"' not in seg, "登记复核：suggestion_engine payload 形态已变"
    # 消费者：前端读 description（两个生产者都不发）
    assert "ev.payload.description" in bubble_src, "登记复核：前端已不再读 description"
    assert "description?" in desktop_notif_src and "if (!payload?.title) return" in desktop_notif_src, (
        "登记复核：useDesktopNotifications 缺 title 即 return → suggestion_engine 事件被丢"
    )


# ═══════════════════════════════════════════════════════════════════════════
# Layer B：活体 WS 传输腿（EventBridge → WebSocket，SIYI_INTEGRATION_AUTOSTART 自启）
# ═══════════════════════════════════════════════════════════════════════════


pytestmark_b = pytest.mark.integration


async def _rpc_and_capture_events(
    ws: "aiohttp.ClientWebSocketResponse",
    req_id: int,
    method: str,
    params: dict[str, Any],
    want_event_types: list[str],
    timeout: float = 12.0,
) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    """在单条 WS 连接上发一次 RPC，并收集随后的事件帧。

    返回 (RPC 响应帧, 事件帧列表)。conftest 的 ``rpc`` fixture 会丢弃事件帧，
    故传输腿验证必须自持连接读取原始帧。提前在收齐 want_event_types 且有响应时结束。
    """
    await ws.send_json({"jsonrpc": "2.0", "id": req_id, "method": method, "params": params})
    response: dict[str, Any] | None = None
    events: list[dict[str, Any]] = []
    end = time.time() + timeout
    while time.time() < end:
        try:
            msg = await asyncio.wait_for(ws.receive(), timeout=max(0.1, end - time.time()))
        except asyncio.TimeoutError:
            break
        if msg.type != aiohttp.WSMsgType.TEXT:
            continue
        try:
            data = json.loads(msg.data)
        except (ValueError, TypeError):
            continue
        if data.get("id") == req_id and ("result" in data or "error" in data):
            response = data
        elif data.get("type") == "event" or "event_type" in data:
            if data.get("event_type"):
                events.append(data)
        have_all = all(any(e.get("event_type") == w for e in events) for w in want_event_types)
        if response is not None and have_all:
            break
    return response, events


@pytestmark_b
async def test_b_notification_test_receives_delivered_event_frame(ws_session):
    """notification.test(channel=websocket) → 客户端真实收到 notification.delivered 事件帧（传输腿）。"""
    resp, events = await _rpc_and_capture_events(ws_session, 9001, "notification.test", {"channel": "websocket"}, ["notification.delivered"])
    assert resp is not None, "notification.test 无 RPC 响应"
    assert resp.get("result", {}).get("ok") is True, f"notification.test 未成功: {resp}"

    delivered = [e for e in events if e.get("event_type") == "notification.delivered"]
    assert delivered, f"客户端未收到 notification.delivered 事件帧；实收={[e.get('event_type') for e in events]}"
    payload = delivered[0].get("payload", {})
    # 关键字段齐全（test_channel 构造的通知）
    assert payload.get("test") is True
    assert payload.get("title") == "测试通知"
    assert payload.get("id")
    assert payload.get("priority") == "normal"
    assert payload.get("event_type") in ("test", "")  # 未传 event_type → 默认 "test"


@pytestmark_b
async def test_b_event_emit_selection_echo_back(ws_session):
    """event.emit(selection.detected) 白名单接受，且 selection.detected 回推到客户端连接。"""
    resp, events = await _rpc_and_capture_events(
        ws_session,
        9002,
        "event.emit",
        {"event_type": "selection.detected", "payload": {"text": "回推验证的选中文本"}},
        ["selection.detected"],
    )
    assert resp is not None, "event.emit 无 RPC 响应"
    assert resp.get("result", {}).get("success") is True, f"event.emit 失败: {resp}"

    echoed = [e for e in events if e.get("event_type") == "selection.detected"]
    assert echoed, f"selection.detected 未回推到客户端；实收={[e.get('event_type') for e in events]}"
    # 事件帧形状：{type:event, event_type, payload}
    assert echoed[0]["payload"].get("text") == "回推验证的选中文本"
    # 若后端 ProactiveTriggerEngine 已在启动/tick 时订阅，则应连带产 proactive.*；
    # 但 subscribe_events 由 cron.tick 惰性触发，不保证本连接窗口内已就绪 → 只记录不硬断言。
    proactive = [e.get("event_type") for e in events if str(e.get("event_type", "")).startswith("proactive.")]
    print(f"[LayerB] selection 回推附带 proactive.* 事件={proactive}")


@pytestmark_b
async def test_b_notification_get_and_preferences_not_32601(rpc):
    """2026-10-15 反转：notification.get / preferences.* 注册已恢复（批次 A c6fb2bc7），
    过线不再返回 -32601。原断言「必须 -32601」锁定的是被证伪的下线结论。"""
    for method in ("notification.get", "notification.preferences.get", "notification.preferences.set"):
        r = await rpc(method, {})
        code = (r.get("error") or {}).get("code")
        assert code != -32601, f"{method} 已注册，不应 -32601，实得: {r}"
