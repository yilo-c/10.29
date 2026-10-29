"""EventBus — 思忆集统一事件发布/订阅总线。

设计参考：
- openclaw 的 channel event 模型
- syncthing 的 event subscription loop

使用方式：
    from core.events import event_bus, AppEvent

    # 发布
    event_bus.emit(AppEvent("browser.summarized", payload={"url": "..."}))

    # 订阅
    def on_summary(event: AppEvent) -> None:
        print(event.payload)

    event_bus.on("browser.summarized", on_summary)
    event_bus.on("browser.*", on_summary)       # 通配符订阅
"""

from __future__ import annotations

import fnmatch
import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger("siyi.events")


@dataclass(frozen=True)
class AppEvent:
    """不可变事件对象。"""

    event_type: str
    payload: dict[str, Any] = field(default_factory=dict)
    source: str = ""
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


# 回调类型
EventHandler = Callable[[AppEvent], None]


class EventBus:
    """线程安全的 pub-sub 事件总线，支持精确匹配和 fnmatch 通配符。"""

    def __init__(self) -> None:
        self._handlers: dict[str, list[EventHandler]] = {}
        self._lock = threading.Lock()
        self._history: list[AppEvent] = []
        self._history_limit = 200
        # 复C3（2026-08-29）：订阅方异常累计计数（并入 stats() 可读接口）
        self._handler_errors = 0

    def on(self, pattern: str, handler: EventHandler) -> None:
        """订阅事件。pattern 支持 fnmatch 通配符（如 'browser.*'）。"""
        with self._lock:
            self._handlers.setdefault(pattern, []).append(handler)

    def off(self, pattern: str, handler: EventHandler) -> None:
        """取消订阅。"""
        with self._lock:
            handlers = self._handlers.get(pattern)
            if handlers and handler in handlers:
                handlers.remove(handler)

    def emit(self, event: AppEvent) -> None:
        """发布事件，同步调用所有匹配的处理器。"""
        with self._lock:
            self._history.append(event)
            if len(self._history) > self._history_limit:
                self._history = self._history[-self._history_limit :]
            matched_handlers: list[EventHandler] = []
            for pattern, handlers in self._handlers.items():
                if pattern == event.event_type or fnmatch.fnmatch(event.event_type, pattern):
                    matched_handlers.extend(handlers)

        for handler in matched_handlers:
            try:
                handler(event)
            except Exception:
                # 复C3：订阅方异常计数（此前仅有日志无计数）
                with self._lock:
                    self._handler_errors += 1
                logger.exception("event handler failed for %s", event.event_type)

    def recent(self, limit: int = 20) -> list[AppEvent]:
        """返回最近的事件（调试用）。"""
        with self._lock:
            return list(self._history[-limit:])

    def stats(self) -> dict[str, Any]:
        """复C3：可读统计接口（订阅方异常计数等）。"""
        with self._lock:
            return {"handler_errors": self._handler_errors}

    def clear(self) -> None:
        """清空所有订阅、历史和统计计数（用于测试）。"""
        with self._lock:
            self._handlers.clear()
            self._history.clear()
            self._handler_errors = 0


# 全局单例
event_bus = EventBus()
