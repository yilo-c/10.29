"""ActivityStore — 轻量级用户活动时间线记录。

记录用户的关键操作（创建笔记/任务、完成、同步等），
供工作台活动流组件展示。
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger("siyi.activity")

_MAX_ACTIVITIES = 100


class ActivityStore:
    """环形缓冲区记录用户活动。"""

    def __init__(self, max_size: int = _MAX_ACTIVITIES):
        self._activities: list[dict[str, Any]] = []
        self._max_size = max_size
        self._listeners: list[Callable[[dict[str, Any]], None]] = []

    # ── 公开接口 ────────────────────────────────────────────

    @property
    def activities(self) -> list[dict[str, Any]]:
        return list(self._activities)

    def list(self, limit: int = 20) -> list[dict[str, Any]]:
        return list(self._activities[-limit:])

    def push(self, category: str, action: str, label: str, metadata: dict[str, Any] | None = None) -> None:
        """记录一条活动。

        Args:
            category: 分类 (note/task/sync/system/ai)
            action:  动作描述 (created/updated/completed/synced)
            label:   显示文本
            metadata: 附加数据 (id, url, etc.)
        """
        now = datetime.now(timezone.utc)
        activity = {
            "category": category,
            "action": action,
            "label": label,
            "timestamp": now.isoformat(),
            "timestamp_local": now.astimezone().strftime("%H:%M"),
            "metadata": metadata or {},
        }
        self._activities.append(activity)
        if len(self._activities) > self._max_size:
            self._activities.pop(0)
        self._notify(activity)

    def recent(self, limit: int = 15, category: str | None = None) -> list[dict[str, Any]]:
        """获取最近活动，可选按分类过滤。"""
        if category:
            filtered = [a for a in self._activities if a["category"] == category]
        else:
            filtered = list(self._activities)
        return filtered[-limit:]

    def clear(self) -> None:
        self._activities.clear()

    # ── 观察者 ──────────────────────────────────────────────

    def on_activity(self, callback: Callable[[dict[str, Any]], None]) -> None:
        self._listeners.append(callback)

    def _notify(self, activity: dict[str, Any]) -> None:
        for cb in self._listeners:
            try:
                cb(activity)
            except Exception as exc:
                logger.warning("activity listener failed: %s", exc)
