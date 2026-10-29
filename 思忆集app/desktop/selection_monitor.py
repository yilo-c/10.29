"""Global text selection monitor — clipboard-change driven, no global hook.

Architecture
------------
Qt fires ``QClipboard.dataChanged`` when the clipboard content changes
(user pressed Ctrl+C / Cut / Copy).  We check whether the foreground
window belongs to our process and, if not, emit ``text_selected``.

Compared to a WH_MOUSE_LL hook this has **zero ongoing CPU cost** —
the callback only fires when the user explicitly copies text.

Usage
-----
    monitor = SelectionMonitor(parent)
    monitor.text_selected.connect(my_popup_show)
    monitor.start()
    ...
    monitor.stop()
"""

from __future__ import annotations

import logging

from PyQt6.QtCore import QObject, QTimer, pyqtSignal
from PyQt6.QtGui import QCursor
from PyQt6.QtWidgets import QApplication

logger = logging.getLogger("siyi.desktop.selection_monitor")

try:
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32

    user32.GetForegroundWindow.restype = wintypes.HWND
    user32.GetWindowThreadProcessId.argtypes = [
        wintypes.HWND, ctypes.POINTER(wintypes.DWORD),
    ]
    user32.GetWindowThreadProcessId.restype = wintypes.DWORD
    kernel32.GetCurrentProcessId.restype = wintypes.DWORD

    _OWN_PID = kernel32.GetCurrentProcessId()
    _HAVE_WIN32 = True
except Exception:
    _HAVE_WIN32 = False
    _OWN_PID = -1
    logger.warning("Win32 API unavailable — PID check disabled")


class SelectionMonitor(QObject):
    """System-wide text selection detector (clipboard-driven).

    Signals
    -------
    text_selected(text: str, x: int, y: int)
        Emitted when text is copied from an external (non-own) window.
        Coordinates are in Qt logical (DPI-aware) pixels.
    """

    text_selected = pyqtSignal(str, int, int)

    MIN_SELECTION_LEN = 1

    def __init__(self, parent: QObject | None = None):
        super().__init__(parent)
        self._last_text: str = ""
        self._running = False
        self._suppress_once = False  # skip next change if we just set clipboard

        # dedup debounce — clipboard can fire multiple rapid signals
        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.timeout.connect(self._on_clipboard_changed)

    # ── public API ──────────────────────────────────────────────

    def start(self):
        """Connect clipboard signal (no hook, zero overhead when idle)."""
        if self._running:
            return
        cb = QApplication.clipboard()
        if cb is None:
            logger.error("no clipboard available")
            return
        cb.dataChanged.connect(self._on_data_changed)
        self._running = True
        logger.info("selection monitor started (clipboard listener)")

    def stop(self):
        """Disconnect clipboard signal."""
        if not self._running:
            return
        cb = QApplication.clipboard()
        if cb is not None:
            try:
                cb.dataChanged.disconnect(self._on_data_changed)
            except (TypeError, RuntimeError):
                pass
        self._running = False
        self._debounce.stop()
        logger.info("selection monitor stopped")

    @property
    def running(self) -> bool:
        return self._running

    def suppress_next(self):
        """Call after we set clipboard ourselves to avoid echo."""
        self._suppress_once = True

    # ── slots ───────────────────────────────────────────────────

    def _on_data_changed(self):
        """Clipboard signal — debounce then process."""
        self._debounce.start(120)

    def _on_clipboard_changed(self):
        """Debounce fired: read clipboard, check source, emit."""
        if self._suppress_once:
            self._suppress_once = False
            return

        if self._is_own_window_foreground():
            return

        cb = QApplication.clipboard()
        if cb is None:
            return
        text = cb.text()
        if not text or len(text) < self.MIN_SELECTION_LEN:
            return
        if text == self._last_text:
            return

        self._last_text = text
        cursor = QCursor.pos()
        logger.debug(
            "external copy detected (%d chars) at %d,%d",
            len(text), cursor.x(), cursor.y(),
        )
        self.text_selected.emit(text, cursor.x(), cursor.y())

    # ── helpers ─────────────────────────────────────────────────

    @staticmethod
    def _is_own_window_foreground() -> bool:
        """Return True if the active window belongs to our process."""
        if not _HAVE_WIN32:
            return False
        fg = user32.GetForegroundWindow()
        if not fg:
            return False
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(fg, ctypes.byref(pid))
        return pid.value == _OWN_PID
