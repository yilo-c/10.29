"""P2.9 通知推送通道 JSON-RPC handlers。

注册 9 个 RPC 方法：
- ``notification.list``：查询通知列表，支持 unread_only / event_type / limit 过滤
- ``notification.get``：按 ID 查询单条通知
- ``notification.mark_read``：标记单条为已读/未读
- ``notification.mark_all_read``：全部标记为已读，返回更新条数
- ``notification.delete``：按 ID 删除单条通知
- ``notification.stats``：聚合统计
- ``notification.test``：向指定通道发送一条测试通知
- ``notification.preferences.get``：读取通道偏好配置
- ``notification.preferences.set``：写入通道偏好配置
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from core.settings_keys import (
    KEY_NOTIFICATION_PREFERENCES,
    KEY_NOTIFICATION_SUBSCRIBED_EVENTS,
    KEY_NOTIFICATIONS_ENABLED,
)
from services.notification_dispatcher import DEFAULT_SUBSCRIBED_EVENTS, NotificationDispatcher

logger = logging.getLogger("siyi.backend.handlers.notifications")


def _get_dispatcher(context) -> NotificationDispatcher | None:
    """优先使用 context.notification_dispatcher，缺失时懒构建。

    headless_main 启动时已显式注入单例；这里兜底用于 handler 独立被调用
    或依赖未注入的场景。
    """
    dispatcher = getattr(context, "notification_dispatcher", None)
    if dispatcher is not None:
        return dispatcher
    try:
        data_store = getattr(context, "local_data_store", None)
        settings_repo = getattr(context, "settings_repository", None)
        if data_store is None or settings_repo is None:
            return None
        dispatcher = NotificationDispatcher(
            data_store=data_store,
            settings_repository=settings_repo,
            event_bus=getattr(context, "event_bus", None),
        )
        dispatcher.start()
        setattr(context, "notification_dispatcher", dispatcher)
        return dispatcher
    except Exception as exc:
        logger.warning("notification dispatcher lazy init failed: %s", exc)
        return None


def register_notifications(registry, context) -> None:
    """注册 notification.* JSON-RPC 方法。"""

    async def notification_list(
        unread_only: bool = False,
        event_type: str = "",
        limit: int = 50,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """查询通知列表（按时间倒序）。"""
        disp = _get_dispatcher(context)
        if disp is None:
            return {"items": [], "error": "dispatcher unavailable"}
        et = (event_type or "").strip() or None
        try:
            items = await asyncio.to_thread(disp.list_notifications, bool(unread_only), et, limit)
            return {"items": items}
        except Exception as exc:
            logger.warning("notification.list failed: %s", exc, exc_info=True)
            return {"items": [], "error": f"list failed: {exc}"}

    registry.register("notification.list", notification_list)

    async def notification_get(notification_id: str = "", **kwargs: Any) -> dict[str, Any]:
        """按 ID 查询单条通知。"""
        notification_id = str(notification_id or "").strip()
        if not notification_id:
            return {"error": "notification_id is required"}
        disp = _get_dispatcher(context)
        if disp is None:
            return {"error": "dispatcher unavailable"}
        try:
            notification = await asyncio.to_thread(disp.get_notification, notification_id)
            if notification is None:
                return {"error": "not found"}
            return {"notification": notification}
        except Exception as exc:
            logger.warning("notification.get failed: %s", exc, exc_info=True)
            return {"error": f"get failed: {exc}"}

    registry.register("notification.get", notification_get)

    async def notification_mark_read(
        notification_id: str = "",
        read: bool = True,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """标记单条通知为已读/未读。"""
        notification_id = str(notification_id or "").strip()
        if not notification_id:
            return {"error": "notification_id is required"}
        disp = _get_dispatcher(context)
        if disp is None:
            return {"error": "dispatcher unavailable"}
        try:
            ok = await asyncio.to_thread(disp.mark_read, notification_id, bool(read))
            return {"updated": ok, "notification_id": notification_id, "read": bool(read)}
        except Exception as exc:
            logger.warning("notification.mark_read failed: %s", exc, exc_info=True)
            return {"error": f"mark_read failed: {exc}"}

    registry.register("notification.mark_read", notification_mark_read)

    async def notification_mark_all_read(**kwargs: Any) -> dict[str, Any]:
        """全部标记为已读，返回更新条数。"""
        disp = _get_dispatcher(context)
        if disp is None:
            return {"updated_count": 0, "error": "dispatcher unavailable"}
        try:
            count = await asyncio.to_thread(disp.mark_all_read)
            return {"updated_count": count}
        except Exception as exc:
            logger.warning("notification.mark_all_read failed: %s", exc, exc_info=True)
            return {"updated_count": 0, "error": f"mark_all_read failed: {exc}"}

    registry.register("notification.mark_all_read", notification_mark_all_read)

    async def notification_delete(notification_id: str = "", **kwargs: Any) -> dict[str, Any]:
        """按 ID 删除单条通知。"""
        notification_id = str(notification_id or "").strip()
        if not notification_id:
            return {"error": "notification_id is required"}
        disp = _get_dispatcher(context)
        if disp is None:
            return {"deleted": False, "error": "dispatcher unavailable"}
        try:
            ok = await asyncio.to_thread(disp.delete_notification, notification_id)
            return {"deleted": ok}
        except Exception as exc:
            logger.warning("notification.delete failed: %s", exc, exc_info=True)
            return {"deleted": False, "error": f"delete failed: {exc}"}

    registry.register("notification.delete", notification_delete)

    async def notification_stats(**kwargs: Any) -> dict[str, Any]:
        """聚合统计：总/未读/按事件类型/按优先级 + 通道健康 + 订阅事件列表 + 总开关。"""
        disp = _get_dispatcher(context)
        if disp is None:
            return {"error": "dispatcher unavailable"}
        try:
            return await asyncio.to_thread(disp.get_stats)
        except Exception as exc:
            logger.warning("notification.stats failed: %s", exc, exc_info=True)
            return {"error": f"stats failed: {exc}"}

    registry.register("notification.stats", notification_stats)

    async def notification_test(
        channel: str = "websocket",
        event_type: str = "",
        **kwargs: Any,
    ) -> dict[str, Any]:
        """向指定通道发送一条测试通知（诊断用）。

        参数：
        - ``channel``: 通道名，默认 "websocket"，可选 "toast"。
        - ``event_type``: 自定义事件类型标签，用于测试展示，不触发真实数据处理。
        """
        disp = _get_dispatcher(context)
        if disp is None:
            return {"ok": False, "error": "dispatcher unavailable"}
        try:
            channel_name = (channel or "websocket").strip().lower() or "websocket"
            result = await disp.test_channel(channel_name, event_type)
            return result
        except Exception as exc:
            logger.warning("notification.test failed: %s", exc, exc_info=True)
            return {"ok": False, "error": str(exc)}

    # 注：notification.get / preferences.get / preferences.set 已于审计波次①-c 下线。
    # notification.test 保留：test_r2_verify 的 TestV6NotificationTestChannel 以其为
    # 参考方法验证 test-then-list/mark_read/delete 链路与未知通道拒绝，本波不下线，
    # 遗留 lead 决策（详见审计波次①-c 报告）。
    registry.register("notification.test", notification_test)

    async def notification_preferences_get(**kwargs: Any) -> dict[str, Any]:
        """读取通道偏好配置。

        返回结构：
        - ``enabled``: 总开关
        - ``preferences``: dict[event_type, {ws, toast, min_priority}]
        - ``subscribed_events``: 已订阅事件列表
        - ``available_events``: 支持的事件类型列表（即 DEFAULT_SUBSCRIBED_EVENTS）
        """
        repo = getattr(context, "settings_repository", None)
        if repo is None:
            return {"error": "settings repository unavailable"}
        try:
            enabled = await asyncio.to_thread(repo.get_setting, KEY_NOTIFICATIONS_ENABLED, True)
            preferences = await asyncio.to_thread(repo.get_setting, KEY_NOTIFICATION_PREFERENCES, {})
            subscribed = await asyncio.to_thread(repo.get_setting, KEY_NOTIFICATION_SUBSCRIBED_EVENTS, DEFAULT_SUBSCRIBED_EVENTS)
            return {
                "enabled": bool(enabled),
                "preferences": preferences if isinstance(preferences, dict) else {},
                "subscribed_events": (list(subscribed) if isinstance(subscribed, list) else list(DEFAULT_SUBSCRIBED_EVENTS)),
                "available_events": list(DEFAULT_SUBSCRIBED_EVENTS),
            }
        except Exception as exc:
            logger.warning("notification.preferences.get failed: %s", exc, exc_info=True)
            return {"error": f"preferences get failed: {exc}"}

    registry.register("notification.preferences.get", notification_preferences_get)

    async def notification_preferences_set(
        enabled: Any = None,
        preferences: Any = None,
        subscribed_events: Any = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """写入通道偏好配置。所有参数可选，仅更新提供的字段。

        参数：
        - ``enabled``: 总开关（True/False）
        - ``preferences``: dict[event_type, {ws, toast, min_priority}]
        - ``subscribed_events``: list[str]，订阅事件类型
        """
        repo = getattr(context, "settings_repository", None)
        if repo is None:
            return {"error": "settings repository unavailable"}
        try:
            if enabled is not None:
                await asyncio.to_thread(repo.set_setting, KEY_NOTIFICATIONS_ENABLED, bool(enabled))
            if preferences is not None:
                if not isinstance(preferences, dict):
                    return {"error": "preferences must be a dict"}
                await asyncio.to_thread(repo.set_setting, KEY_NOTIFICATION_PREFERENCES, preferences)
            if subscribed_events is not None:
                if not isinstance(subscribed_events, list):
                    return {"error": "subscribed_events must be a list"}
                events = list(subscribed_events)
                for ev in DEFAULT_SUBSCRIBED_EVENTS:
                    if ev not in events:
                        events.append(ev)
                await asyncio.to_thread(repo.set_setting, KEY_NOTIFICATION_SUBSCRIBED_EVENTS, events)

            # 偏好变更后重新加载 dispatcher 订阅
            disp = _get_dispatcher(context)
            if disp is not None:
                try:
                    disp.stop()
                    disp.start()
                except Exception:
                    logger.warning("notification dispatcher reload failed", exc_info=True)
            return {"success": True}
        except Exception as exc:
            logger.warning("notification.preferences.set failed: %s", exc, exc_info=True)
            return {"error": f"preferences set failed: {exc}"}

    registry.register("notification.preferences.set", notification_preferences_set)
