"""WindowContextMonitor — 前台窗口感知触发器。

Detects foreground window changes and provides context about the active application.
Phase 5.3: 7 trigger types expansion.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any, Callable

logger = logging.getLogger("siyi.triggers.window_context")


class WindowContextMonitor:
    """前台窗口监控器 — 检测当前活跃窗口变化。

    支持 Windows 平台，通过 Win32 API 获取前台窗口信息。
    其他平台返回空信息。
    """

    def __init__(self, callback: Callable[[dict[str, Any]], None] | None = None):
        self._callback = callback
        self._last_window_title: str = ""
        self._last_window_class: str = ""
        self._last_fire: datetime | None = None
        self._cooldown = timedelta(seconds=10)
        self._enabled = True

    @property
    def enabled(self) -> bool:
        return self._enabled

    def set_enabled(self, enabled: bool) -> None:
        self._enabled = enabled

    def set_callback(self, callback: Callable[[dict[str, Any]], None]) -> None:
        self._callback = callback

    def get_foreground_window_info(self) -> dict[str, Any]:
        """获取当前前台窗口信息。"""
        info: dict[str, Any] = {
            "title": "",
            "class_name": "",
            "process_name": "",
            "platform": "unknown",
        }
        try:
            import platform as _platform
            if _platform.system() == "Windows":
                import ctypes
                import ctypes.wintypes
                # Get foreground window handle
                hwnd = ctypes.windll.user32.GetForegroundWindow()
                # Get window title
                length = ctypes.windll.user32.GetWindowTextLengthW(hwnd) + 1
                buffer = ctypes.create_unicode_buffer(length)
                ctypes.windll.user32.GetWindowTextW(hwnd, buffer, length)
                info["title"] = buffer.value or ""
                # Get class name
                class_buffer = ctypes.create_unicode_buffer(256)
                ctypes.windll.user32.GetClassNameW(hwnd, class_buffer, 256)
                info["class_name"] = class_buffer.value or ""
                # Get process ID
                pid = ctypes.wintypes.DWORD()
                ctypes.windll.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
                info["pid"] = pid.value
                info["platform"] = "windows"
        except Exception:
            logger.debug("failed to get foreground window info", exc_info=True)
        return info

    def poll(self) -> dict[str, Any] | None:
        """Poll foreground window. Returns info dict if window changed, None otherwise."""
        if not self._enabled:
            return None

        info = self.get_foreground_window_info()
        title = info.get("title", "")
        class_name = info.get("class_name", "")

        # Skip if title or class is empty
        if not title:
            return None

        # Check if window actually changed
        if title == self._last_window_title and class_name == self._last_window_class:
            return None

        # Cooldown check
        now = datetime.now()
        if self._last_fire is not None and now - self._last_fire < self._cooldown:
            return None

        self._last_window_title = title
        self._last_window_class = class_name
        self._last_fire = now

        if self._callback:
            try:
                self._callback(info)
            except Exception as exc:
                logger.warning("window context callback failed: %s", exc, exc_info=True)

        return info
