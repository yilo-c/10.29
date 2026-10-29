# -*- coding: utf-8 -*-
"""剪贴板上下文采集（第二档 #4：复制即候选上下文）。

把 ClipboardMonitor 的轮询结果沉淀为「最近复制上下文」存储，供划词 / 悬浮
在用户复制后自动获得候选上下文（比现在「弹卡片等点击」进一格）。

机制：后端启动时由 backend/handlers/_clipboard.register_clipboard 拉起一个
守护线程轮询剪贴板，新文本推入 ClipboardContextStore（内存，最近 N 条）。
前端可通过 `clipboard.context` / `clipboard.latest` 拉取并自动注入聊天。
"""

from __future__ import annotations

import logging
import threading
import time
from collections import deque
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger("siyi.services.clipboard_context")

_MAX_ITEMS = 10


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class ClipboardContextStore:
    """最近复制片段的轻量内存存储（候选上下文源）。"""

    def __init__(self, max_items: int = _MAX_ITEMS) -> None:
        self._max = max(1, int(max_items))
        self._items: deque[dict[str, Any]] = deque()
        self._lock = threading.Lock()

    def push(self, text: str) -> None:
        text = (text or "").strip()
        if len(text) < 3:
            return
        with self._lock:
            # 去重：与最新条目相同则仅刷新时间戳
            if self._items and self._items[0].get("text") == text:
                self._items[0]["ts"] = _now_iso()
                return
            self._items.appendleft({"text": text, "ts": _now_iso()})
            while len(self._items) > self._max:
                self._items.pop()

    def latest(self) -> dict[str, Any] | None:
        with self._lock:
            return dict(self._items[0]) if self._items else None

    def recent(self, limit: int = 5) -> list[dict[str, Any]]:
        limit = max(1, min(int(limit or 5), self._max))
        with self._lock:
            return [dict(x) for x in list(self._items)[:limit]]


# 进程内单例：cron job / RPC handler / 后台轮询线程共享同一份存储
store = ClipboardContextStore()

_monitor = None
_monitor_lock = threading.Lock()
_polling_started = False
_polling_lock = threading.Lock()


def _get_monitor():
    """懒初始化单例 ClipboardMonitor（导入失败则返回 None，功能降级）。"""
    global _monitor
    with _monitor_lock:
        if _monitor is None:
            try:
                from assistant.triggers.clipboard_monitor import ClipboardMonitor

                _monitor = ClipboardMonitor()
                _monitor.set_callback(store.push)  # 新文本直接沉淀到 store
            except Exception as exc:  # noqa: BLE001
                logger.warning("ClipboardMonitor 初始化失败（剪贴板监听不可用）: %s", exc)
                return None
        return _monitor


def poll_once() -> str | None:
    """轮询一次剪贴板；有新文本则已自动推入 store，返回该文本或 None。

    第二档 #5：监测到新复制内容时额外 emit ``clipboard.changed`` 事件，
    把"复制"接入 ProactiveTriggerEngine 的事件驱动管道（→ 一条管家消息），
    从而与 tick 定时模式并列，实现"听事件"的主动性。Monitor 已对相同内容去重，
    故该事件仅在真正有新复制时触发。
    """
    mon = _get_monitor()
    if mon is None:
        return None
    try:
        text = mon.poll()
    except Exception as exc:  # noqa: BLE001
        logger.debug("clipboard poll error: %s", exc)
        return None
    if text:
        try:
            from core.events import AppEvent, event_bus

            event_bus.emit(AppEvent(event_type="clipboard.changed", payload={"text": text}))
        except Exception:  # noqa: BLE001
            logger.debug("clipboard.changed emit failed", exc_info=True)
    return text


def start_background_polling(interval: float = 3.0) -> bool:
    """启动守护线程持续轮询剪贴板（幂等）。成功返回 True。"""
    global _polling_started
    with _polling_lock:
        if _polling_started:
            return True
        # 先探一次，确认 monitor 可用再起线程
        if _get_monitor() is None:
            logger.info("剪贴板监听不可用，跳过后台轮询")
            return False

        def _loop() -> None:
            while True:
                try:
                    poll_once()
                except Exception as exc:  # noqa: BLE001
                    logger.debug("clipboard poll loop error: %s", exc)
                time.sleep(max(0.5, float(interval)))

        t = threading.Thread(target=_loop, name="clipboard-context-poll", daemon=True)
        t.start()
        _polling_started = True
        logger.info("剪贴板上下文轮询已启动（间隔 %.1fs）", interval)
        return True
