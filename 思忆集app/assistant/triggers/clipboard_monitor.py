"""ClipboardMonitor — 剪贴板监听触发器。

Periodically polls clipboard content and fires when new text is detected.
Phase 5.3: 7 trigger types expansion.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any, Callable

logger = logging.getLogger("siyi.triggers.clipboard")


class ClipboardMonitor:
    """剪贴板监听器 — 检测新剪贴板内容并触发回调。

    使用定时轮询（~3s 间隔）而非系统剪贴板事件，
    避免跨平台兼容性问题。
    """

    def __init__(self, callback: Callable[[str], None] | None = None):
        self._callback = callback
        self._last_text: str = ""
        self._last_fire: datetime | None = None
        self._cooldown = timedelta(seconds=30)
        self._enabled = True

    @property
    def enabled(self) -> bool:
        return self._enabled

    def set_enabled(self, enabled: bool) -> None:
        self._enabled = enabled

    def set_callback(self, callback: Callable[[str], None]) -> None:
        self._callback = callback

    def poll(self) -> str | None:
        """Poll clipboard for new text. Returns text if new, None otherwise."""
        if not self._enabled:
            return None

        try:
            import pyperclip
            current = pyperclip.paste()
        except Exception:
            return None

        if not current or current == self._last_text:
            return None

        # Ignore very short or whitespace-only content
        stripped = current.strip()
        if len(stripped) < 3:
            self._last_text = current
            return None

        # Cooldown check
        now = datetime.now()
        if self._last_fire is not None and now - self._last_fire < self._cooldown:
            return None

        self._last_text = current
        self._last_fire = now

        if self._callback:
            try:
                self._callback(stripped)
            except Exception as exc:
                logger.warning("clipboard callback failed: %s", exc, exc_info=True)

        return stripped
