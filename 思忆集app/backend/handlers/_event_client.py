# -*- coding: utf-8 -*-
"""前端 → 后端事件上报 RPC（第二档 #5：把"听事件"的来源从后端扩展到前端）。

前端悬浮球 / 聊天窗监听到本地信号（划词、切窗、截获链接）后，经此 RPC 把
事件注入后端 EventBus，触发 ProactiveTriggerEngine 的"听事件"触发器，产出管家
消息（与 info.brief 同款管道）。

安全：仅允许白名单内的事件类型，避免任意客户端在总线上伪造/刷屏事件。
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("siyi.backend.handlers.event_client")

# 前端可经 event.emit 注入的事件类型（与 ProactiveTriggerEngine.subscribe_events
# 的订阅键对齐）。越权事件类型一律拒绝。
_ALLOWED_CLIENT_EVENTS = frozenset(
    {
        "selection.detected",
        "foreground.window.changed",
        "url.intercepted",
        "user.action",
    }
)

_SOURCE = "client"


def register_event_client(registry, context) -> None:
    """注册前端事件上报 RPC。"""

    async def event_emit(
        event_type: str = "",
        payload: dict[str, Any] | None = None,
        source: str = _SOURCE,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """前端把本地信号作为事件注入后端 EventBus。

        Args:
            event_type: 事件名，必须是白名单内（selection.detected /
                foreground.window.changed / url.intercepted / user.action）。
            payload: 事件负载（dict）。selection.detected→{text}；
                foreground.window.changed→{title, app?}；url.intercepted→{url, title?}。
            source: 来源标记（默认 "client"）。

        Returns:
            {"success": true, "event_type": ...} 或 {"success": false, "error": ...}
        """
        if not event_type or event_type not in _ALLOWED_CLIENT_EVENTS:
            return {
                "success": False,
                "error": f"不允许的事件类型：{event_type or '(空)'}（仅允许 {sorted(_ALLOWED_CLIENT_EVENTS)}）",
            }
        if not isinstance(payload, dict):
            payload = {}

        try:
            from core.events import AppEvent, event_bus

            event_bus.emit(
                AppEvent(
                    event_type=event_type,
                    payload=payload,
                    source=source or _SOURCE,
                )
            )
            logger.debug("event.emit 已注入 %s (payload keys=%s)", event_type, list(payload.keys()))
            return {"success": True, "event_type": event_type}
        except Exception as exc:  # noqa: BLE001
            logger.warning("event.emit 注入失败 %s: %s", event_type, exc)
            return {"success": False, "error": str(exc)}

    registry.register("event.emit", event_emit)
