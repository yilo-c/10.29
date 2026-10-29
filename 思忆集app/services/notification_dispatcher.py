"""P2.9 统一通知推送通道。

设计要点：
- ``NotificationChannel`` 抽象接口：``send`` / ``health`` / ``name``
- ``NotificationDispatcher`` 管理通道、订阅 EventBus 事件、按用户偏好过滤并 fan-out
- 内置通道：
  - ``WebSocketChannel``：通过 EventBus 发布 ``notification.delivered`` 事件（EventBridge 自动转发给前端）
  - ``DesktopToastChannel``：判断"是否应触发 toast"，emit ``notification.toast`` 事件供前端
    ``useDesktopNotifications`` 用 Web Notification API 实现系统级 toast
- 通知持久化：``local_storage`` 的 ``notifications`` 键（list，LRU 上限 500 条）
- 用户偏好存储在 settings：``notification_preferences`` / ``notification_subscribed_events`` / ``notifications_enabled``
"""

from __future__ import annotations

import asyncio
import logging
import secrets
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable

from core.events import AppEvent
from core.settings_keys import (
    KEY_NOTIFICATION_PREFERENCES,
    KEY_NOTIFICATION_SUBSCRIBED_EVENTS,
    KEY_NOTIFICATIONS_ENABLED,
    KEY_NOTIFICATIONS_STORAGE,
)

logger = logging.getLogger("siyi.services.notification")

# ── 模块级常量 ──────────────────────────────────────────────

#: 哪些事件自动进入通知管道（白名单）
DEFAULT_SUBSCRIBED_EVENTS: list[str] = [
    "inbox.delivered",
    "proactive.triggered",
    "vocabulary.due_reminder",
    "task.due",
    "sync.failed",
    "job_hunter.new_jobs",
    "ai.usage_alert",
    "cron.job_failed",
    "cron.job_exhausted",
    "cron.job_alert",
    "sandbox.timeout",
]

#: 各事件的标题模板与 payload 中提取正文的字段名
_EVENT_TEMPLATES: dict[str, dict[str, str]] = {
    "inbox.delivered": {"title": "新收件箱条目", "body_field": "title"},
    "proactive.triggered": {"title": "主动建议", "body_field": "content"},
    "vocabulary.due_reminder": {"title": "单词到期复习", "body_field": "message"},
    "task.due": {"title": "任务到期", "body_field": "title"},
    "sync.failed": {"title": "同步失败", "body_field": "error"},
    "job_hunter.new_jobs": {"title": "招聘新岗位", "body_field": "message"},
    "ai.usage_alert": {"title": "AI 用量告警", "body_field": "message"},
    "cron.job_failed": {"title": "定时任务失败", "body_field": "error"},
    "cron.job_exhausted": {"title": "定时任务重试耗尽", "body_field": "error"},
    "cron.job_alert": {"title": "定时任务连续失败告警", "body_field": "error"},
    "sandbox.timeout": {"title": "高危审批超时已自动处置", "body_field": "action_id"},
}

#: 各事件的默认优先级（payload 可覆盖）
_EVENT_DEFAULT_PRIORITY: dict[str, str] = {
    "sync.failed": "urgent",
    "ai.usage_alert": "high",
    "task.due": "high",
    "cron.job_exhausted": "high",
    "cron.job_alert": "high",
    "sandbox.timeout": "high",
    "proactive.triggered": "normal",
    "inbox.delivered": "normal",
    "vocabulary.due_reminder": "normal",
    "job_hunter.new_jobs": "normal",
    "cron.job_failed": "normal",
}

#: 优先级排序：数值越大越紧急
_PRIORITY_RANK: dict[str, int] = {"low": 0, "normal": 1, "high": 2, "urgent": 3}

#: 持久化上限（LRU）
_MAX_NOTIFICATIONS = 500


def _priority_rank(p: str) -> int:
    """返回优先级排序值，未知优先级按 'normal' 处理。"""
    return _PRIORITY_RANK.get(p, _PRIORITY_RANK["normal"])


def _emit_event(event_bus: Any, event_type: str, payload: dict[str, Any]) -> bool:
    """统一 emit 逻辑，兼容 ``core.events.EventBus.emit(event: AppEvent)``。

    若 event_bus 缺失或 emit 抛异常，返回 False（由调用方记录失败日志）。
    """
    if event_bus is None:
        return False
    try:
        event_bus.emit(AppEvent(event_type=event_type, payload=payload))
        return True
    except Exception:
        # 不提供 emit(event_type, payload) 旧式双参回退——接缝契约禁该写法。
        # （2026-09-13 守卫通电普查：旧回退是契约禁用模式的存活实例；
        # 全部 fake 总线均已升级单参签名，双参分支无消费者。）
        return False


def _unpack_payload(arg: Any) -> Any:
    """从 EventBus handler 收到的参数中解出 payload。

    ``core.events.EventBus`` 的 handler 收到 ``AppEvent``，payload 在 ``event.payload``；
    fake 事件总线可能直接传 dict。
    """
    if isinstance(arg, AppEvent):
        return arg.payload
    return arg


class NotificationChannel:
    """通知通道抽象基类。"""

    name: str = "base"

    async def send(self, notification: dict[str, Any]) -> bool:
        """发送通知，返回是否成功。子类必须实现。

        与所有内置子类一致采用 ``async`` 签名（dispatch 通过 ``await`` 调用），
        避免第三方通道按同步签名实现后与调用方不匹配。
        """
        raise NotImplementedError

    def health(self) -> dict[str, Any]:
        """返回通道健康状态。"""
        return {"name": self.name, "healthy": True}


class WebSocketChannel(NotificationChannel):
    """通过 EventBus 向 WebSocket 客户端广播 ``notification.delivered`` 事件。

    事件经 EventBridge 转发给所有 WS 客户端，前端可订阅以实时刷新通知列表。
    """

    name = "websocket"

    def __init__(self, event_bus: Any) -> None:
        self._event_bus = event_bus

    async def send(self, notification: dict[str, Any]) -> bool:
        if self._event_bus is None:
            return False
        ok = _emit_event(self._event_bus, "notification.delivered", notification)
        if not ok:
            logger.warning("WebSocketChannel send failed: emit returned False")
        return ok


class DesktopToastChannel(NotificationChannel):
    """桌面 toast 通道：仅对 high/urgent 优先级通知触发。

    与 WebSocketChannel 互补：WebSocketChannel 负责持久化通知的列表数据，
    DesktopToastChannel 只 emit ``notification.toast`` 事件，由前端 hook
    （``useDesktopNotifications``）决定是否弹出系统级 toast。
    """

    name = "toast"

    def __init__(self, event_bus: Any) -> None:
        self._event_bus = event_bus

    async def send(self, notification: dict[str, Any]) -> bool:
        if self._event_bus is None:
            return False
        if _priority_rank(notification.get("priority", "normal")) < _priority_rank("high"):
            return False
        payload = {
            "id": notification.get("id"),
            "title": notification.get("title", ""),
            "body": notification.get("body", ""),
            "priority": notification.get("priority", "normal"),
            "event_type": notification.get("event_type", ""),
            "created_at": notification.get("created_at", ""),
        }
        ok = _emit_event(self._event_bus, "notification.toast", payload)
        if not ok:
            logger.warning("DesktopToastChannel send failed: emit returned False")
        return ok


@dataclass
class _Subscription:
    """EventBus 订阅记录，用于取消订阅。"""

    event_type: str
    handler: Callable[[Any], None]


class NotificationDispatcher:
    """统一通知分发器。

    订阅 EventBus 白名单事件，按总开关 / 订阅白名单 / 偏好过滤后构造并持久化
    通知 dict，再按通道偏好 fan-out 到各通道。
    """

    def __init__(
        self,
        data_store: Any,
        settings_repository: Any,
        event_bus: Any,
    ) -> None:
        self._store = data_store
        self._settings = settings_repository
        self._event_bus = event_bus
        self._channels: dict[str, NotificationChannel] = {}
        self._subscriptions: list[_Subscription] = []
        self._started = False
        # C5 修复：用线程锁保护 load-modify-save 竞态（持久化在 to_thread 中执行）
        self._store_lock = threading.Lock()
        # M9（2026-08-29）：必须持有 dispatch 任务的强引用至其结束。
        # asyncio 文档明确要求保存 task 引用，否则事件循环只持有弱引用，
        # 任务可能在 await 中途被 GC 回收 → 通知静默丢失且极难复现。
        self._dispatch_tasks: set[asyncio.Task] = set()

        if event_bus is not None:
            self.register_channel(WebSocketChannel(event_bus))
            self.register_channel(DesktopToastChannel(event_bus))

    # ── 通道管理 ────────────────────────────────────────────

    def register_channel(self, channel: NotificationChannel) -> None:
        """注册通道，之后按 channel.name 索引。"""
        self._channels[channel.name] = channel
        logger.info("notification channel registered: %s", channel.name)

    def list_channels(self) -> list[dict[str, Any]]:
        """列出所有通道的健康状态。"""
        return [ch.health() for ch in self._channels.values()]

    # ── 配置读取 ────────────────────────────────────────────

    def is_enabled(self) -> bool:
        """总开关：settings 未配置或缺省时默认启用。"""
        if self._settings is None:
            return True
        try:
            return bool(self._settings.get_setting(KEY_NOTIFICATIONS_ENABLED, True))
        except Exception:
            return True

    def _subscribed_events(self) -> list[str]:
        """返回已订阅的事件白名单。"""
        if self._settings is None:
            return list(DEFAULT_SUBSCRIBED_EVENTS)
        try:
            events = self._settings.get_setting(KEY_NOTIFICATION_SUBSCRIBED_EVENTS, DEFAULT_SUBSCRIBED_EVENTS)
            if not isinstance(events, list):
                return list(DEFAULT_SUBSCRIBED_EVENTS)
            return events
        except Exception:
            return list(DEFAULT_SUBSCRIBED_EVENTS)

    def _preferences(self) -> dict[str, Any]:
        """返回通道偏好配置 dict[event_type, {ws, toast, min_priority}]。"""
        if self._settings is None:
            return {}
        try:
            prefs = self._settings.get_setting(KEY_NOTIFICATION_PREFERENCES, {})
            return prefs if isinstance(prefs, dict) else {}
        except Exception:
            return {}

    def _get_event_config(self, event_type: str) -> dict[str, Any]:
        """获取某事件类型的通道偏好，缺失字段返回默认值。"""
        prefs = self._preferences()
        cfg = prefs.get(event_type, {})
        if not isinstance(cfg, dict):
            cfg = {}
        return {
            "ws": bool(cfg.get("ws", True)),
            "toast": bool(cfg.get("toast", True)),
            "min_priority": cfg.get("min_priority", "normal"),
        }

    # ── EventBus 订阅 ───────────────────────────────────────

    def start(self) -> None:
        """订阅 EventBus 白名单事件；重复调用安全。"""
        if self._started or self._event_bus is None:
            return
        for event_type in self._subscribed_events():
            handler = self._make_handler(event_type)
            self._event_bus.on(event_type, handler)
            self._subscriptions.append(_Subscription(event_type=event_type, handler=handler))
        self._started = True
        logger.info("NotificationDispatcher subscribed %d events", len(self._subscriptions))

    def stop(self) -> None:
        """取消所有订阅。"""
        if self._event_bus is None:
            return
        for sub in self._subscriptions:
            try:
                self._event_bus.off(sub.event_type, sub.handler)
            except Exception:
                logger.debug("failed to unsubscribe %s", sub.event_type, exc_info=True)
        self._subscriptions = []
        self._started = False

    def _make_handler(self, event_type: str) -> Callable[[Any], None]:
        """为指定事件类型创建 EventBus handler。

        ``core.events.EventBus`` 同步调用 handler；``dispatch`` 是 async 方法，
        必须用 ``asyncio.create_task`` 调度协程到事件循环，否则协程从未被
        await 会被 GC 回收并触发 ``RuntimeWarning: coroutine was never awaited``，
        导致所有 EventBus 触发的通知静默失败。
        """

        def _handler(event: Any) -> None:
            coro = self.dispatch(event_type, _unpack_payload(event.payload))
            try:
                # 优先用运行中的事件循环调度
                loop = asyncio.get_running_loop()
                task = loop.create_task(coro)
            except RuntimeError:
                # 无运行中的事件循环（罕见：测试环境或线程外调用）
                # ensure_future 会创建新 task，但需调用方驱动 loop
                task = asyncio.ensure_future(coro)
            # 必须保存强引用并在完成后释放，否则任务可能在 await 中途被 GC
            # 回收 → 通知静默丢失（asyncio 文档要求，M9）。
            self._dispatch_tasks.add(task)
            task.add_done_callback(self._dispatch_tasks.discard)

        return _handler

    # ── 分发核心 ────────────────────────────────────────────

    async def dispatch(self, event_type: str, payload: Any) -> dict[str, Any]:
        """分发一个事件到通知管道，返回处理结果。

        - 检查总开关 / 订阅白名单 / 偏好过滤
        - 构造通知 dict 并持久化（``asyncio.to_thread``，不阻塞事件循环）
        - 按通道偏好 fan-out
        """
        if not self.is_enabled():
            return {"dispatched": False, "reason": "disabled"}
        if event_type not in self._subscribed_events():
            return {"dispatched": False, "reason": "event not subscribed"}
        if event_type == "sync.failed" and isinstance(payload, dict) and not payload.get("fatal"):
            return {"dispatched": False, "reason": "sync.failed not fatal"}

        notification = self._build_notification(event_type, payload)
        cfg = self._get_event_config(event_type)
        if _priority_rank(notification["priority"]) < _priority_rank(cfg["min_priority"]):
            return {"dispatched": False, "reason": "below min_priority"}

        await asyncio.to_thread(self._persist, notification)

        results: dict[str, Any] = {}
        if cfg["ws"]:
            ws_ch = self._channels.get("websocket")
            if ws_ch is not None:
                results["websocket"] = await ws_ch.send(notification)
        if cfg["toast"]:
            toast_ch = self._channels.get("toast")
            if toast_ch is not None:
                results["toast"] = await toast_ch.send(notification)

        results["delivery"] = notification
        return {"dispatched": True, "notification": notification}

    def _build_notification(self, event_type: str, payload: Any) -> dict[str, Any]:
        """从事件 payload 构造通知 dict。"""
        template = _EVENT_TEMPLATES.get(event_type, {"title": event_type, "body_field": "message"})
        priority = _EVENT_DEFAULT_PRIORITY.get(event_type, "normal")
        title = template["title"]
        body = ""
        if isinstance(payload, dict):
            body_field = template["body_field"]
            body = str(payload.get(body_field) or payload.get("message") or payload.get("title") or "")
            priority = payload.get("priority", priority)
            if payload.get("notification_title"):
                title = str(payload["notification_title"])
        return {
            "id": secrets.token_hex(8),
            "event_type": event_type,
            "title": title[:200],
            "body": body[:500],
            "priority": priority,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "read": False,
            "raw": payload if isinstance(payload, dict) else str(payload)[:500],
        }

    # ── 持久化（load-modify-save，线程锁保护）────────────────

    def _persist(self, notification: dict[str, Any]) -> None:
        """持久化到 local_storage，LRU 上限 500 条。"""
        try:
            with self._store_lock:
                records = self._store.load_json(KEY_NOTIFICATIONS_STORAGE, [])
                if not isinstance(records, list):
                    records = []
                records.append(notification)
                if len(records) > _MAX_NOTIFICATIONS:
                    records = records[-_MAX_NOTIFICATIONS:]
                self._store.save_json(KEY_NOTIFICATIONS_STORAGE, records)
        except Exception as exc:
            logger.warning("persist notification failed: %s", exc)

    # ── 查询与操作 ──────────────────────────────────────────

    def list_notifications(
        self,
        unread_only: bool = False,
        event_type: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """查询通知列表（按写入时间倒序）。"""
        limit = max(1, min(500, int(limit)))
        with self._store_lock:
            records = self._store.load_json(KEY_NOTIFICATIONS_STORAGE, [])
        if not isinstance(records, list):
            records = []
        result = list(reversed(records))
        if unread_only:
            result = [n for n in result if not n.get("read")]
        if event_type:
            result = [n for n in result if n.get("event_type") == event_type]
        return result[:limit]

    def get_notification(self, notification_id: str) -> dict[str, Any] | None:
        """按 ID 查询单条通知；未找到返回 None。"""
        if not notification_id:
            return None
        with self._store_lock:
            records = self._store.load_json(KEY_NOTIFICATIONS_STORAGE, [])
        if not isinstance(records, list):
            return None
        for r in records:
            if r.get("id") == notification_id:
                return r
        return None

    def mark_read(self, notification_id: str, read: bool = True) -> bool:
        """标记单条通知已读/未读；返回是否找到并更新。"""
        if not notification_id:
            return False
        with self._store_lock:
            records = self._store.load_json(KEY_NOTIFICATIONS_STORAGE, [])
            if not isinstance(records, list):
                return False
            for r in records:
                if r.get("id") == notification_id:
                    r["read"] = bool(read)
                    self._store.save_json(KEY_NOTIFICATIONS_STORAGE, records)
                    return True
        return False

    def mark_all_read(self) -> int:
        """全部标记已读，返回更新条数。"""
        with self._store_lock:
            records = self._store.load_json(KEY_NOTIFICATIONS_STORAGE, [])
            if not isinstance(records, list):
                return 0
            count = 0
            for r in records:
                if not r.get("read"):
                    r["read"] = True
                    count += 1
            if count > 0:
                self._store.save_json(KEY_NOTIFICATIONS_STORAGE, records)
            return count

    def delete_notification(self, notification_id: str) -> bool:
        """按 ID 删除单条通知；返回是否删除成功。"""
        if not notification_id:
            return False
        with self._store_lock:
            records = self._store.load_json(KEY_NOTIFICATIONS_STORAGE, [])
            if not isinstance(records, list):
                return False
            new_records = [r for r in records if r.get("id") != notification_id]
            if len(new_records) != len(records):
                self._store.save_json(KEY_NOTIFICATIONS_STORAGE, new_records)
                return True
        return False

    def get_stats(self) -> dict[str, Any]:
        """聚合统计：总数/未读/按事件类型/按优先级 + 通道健康 + 订阅事件列表 + 总开关。"""
        with self._store_lock:
            records = self._store.load_json(KEY_NOTIFICATIONS_STORAGE, [])
        if not isinstance(records, list):
            records = []
        total = len(records)
        unread = sum(1 for r in records if not r.get("read"))
        by_event: dict[str, int] = {}
        by_priority: dict[str, int] = {"low": 0, "normal": 0, "high": 0, "urgent": 0}
        for r in records:
            event_type = r.get("event_type", "unknown")
            by_event[event_type] = by_event.get(event_type, 0) + 1
            priority = r.get("priority", "normal")
            by_priority[priority] = by_priority.get(priority, 0) + 1
        return {
            "total": total,
            "unread": unread,
            "by_event": by_event,
            "by_priority": by_priority,
            "channels": self.list_channels(),
            "subscribed_events": self._subscribed_events(),
            "enabled": self.is_enabled(),
        }

    async def test_channel(self, channel_name: str, event_type: str = "") -> dict[str, Any]:
        """向指定通道发送一条测试通知（诊断用）。

        D4（2026-09-17）：原实现只调 ``ch.send`` 后即返回，通知不落库——用户
        看到 ok:true，但 notification.get/list/mark_read/delete 全部指向空。
        现改为发送成功后真实持久化，并带 ``test: True`` 标记，list 返回体
        （list_notifications 原样回传记录）保留该标记供前端区分。
        """
        if channel_name not in self._channels:
            return {"ok": False, "error": f"unknown channel: {channel_name}"}
        ch = self._channels[channel_name]
        notification = {
            "id": secrets.token_hex(8),
            "event_type": event_type or "test",
            "title": "测试通知",
            "body": f"这是一条 {channel_name} 通道的测试通知",
            "priority": "normal",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "read": False,
            "test": True,
        }
        ok = await ch.send(notification)
        if ok:
            # 仅在真实送达时落库：发送失败却留下"已送达"记录会制造新的假成功
            await asyncio.to_thread(self._persist, notification)
        return {"ok": ok, "channel": channel_name, "notification": notification}
