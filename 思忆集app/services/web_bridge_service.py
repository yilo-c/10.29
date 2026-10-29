"""Desktop ↔ Web QWebChannel bridge lifecycle management."""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from desktop.web_bridge import AppBridge, BaseService, _default_services
from PyQt6.QtCore import QObject, QUrl
from PyQt6.QtWebChannel import QWebChannel

logger = logging.getLogger("siyi.web_bridge")


class WebBridgeService:
    """Manages QWebChannel lifecycle and bridges desktop services to Web UI.

    Usage:
        service = WebBridgeService()
        service.start()
        # Access the bridge:
        service.bridge.data_changed.emit("inbox:unread_count", {"count": 3})
    """

    def __init__(self, services: dict[str, BaseService] | None = None):
        self._web_engine_view = None
        self._web_channel: QWebChannel | None = None
        self._bridge: AppBridge | None = None
        self._is_started = False
        self._custom_services = services

    # ── Lifecycle ─────────────────────────────────────────────

    def start(self, parent: QObject | None = None) -> dict[str, Any]:
        """Start the QWebChannel bridge."""
        if self._is_started:
            return {"ok": True, "data": {"message": "Bridge already started"}, "error": None}

        try:
            # 惰性导入，避免 Chromium 初始化阻塞启动
            from PyQt6.QtWebEngineWidgets import QWebEngineView

            self._bridge = AppBridge(
                services=self._custom_services or _default_services(),
                parent=parent,
            )
            self._web_engine_view = QWebEngineView(parent)
            html_path = Path(__file__).parent / "web_dist" / "index.html"
            self._web_engine_view.load(QUrl.fromLocalFile(str(html_path)))
            self._web_engine_view.hide()

            self._web_channel = QWebChannel()
            self._web_channel.registerObject("bridge", self._bridge)
            self._web_engine_view.page().setWebChannel(self._web_channel)

            self._is_started = True
            logger.info("Web bridge service started")
            return {"ok": True, "data": {"message": "Bridge started"}, "error": None}

        except Exception as e:
            logger.error("Failed to start web bridge: %s", e)
            return {"ok": False, "data": None, "error": str(e)}

    def stop(self) -> dict[str, Any]:
        """Stop the bridge and release resources."""
        if not self._is_started:
            return {"ok": True, "data": {"message": "Bridge already stopped"}, "error": None}

        try:
            if self._web_engine_view:
                self._web_engine_view.deleteLater()
                self._web_engine_view = None
            self._web_channel = None
            self._bridge = None
            self._is_started = False
            logger.info("Web bridge service stopped")
            return {"ok": True, "data": {"message": "Bridge stopped"}, "error": None}

        except Exception as e:
            logger.error("Failed to stop web bridge: %s", e)
            return {"ok": False, "data": None, "error": str(e)}

    # ── Accessors ─────────────────────────────────────────────

    @property
    def bridge(self) -> AppBridge | None:
        return self._bridge

    @property
    def is_started(self) -> bool:
        return self._is_started

    def get_status(self) -> dict[str, Any]:
        return {
            "ok": True,
            "data": {
                "running": self._is_started,
                "has_view": self._web_engine_view is not None,
                "has_channel": self._web_channel is not None,
                "has_bridge": self._bridge is not None,
                "service_count": len(self._bridge.services) if self._bridge else 0,
            },
            "error": None,
        }

    # ── Convenience signal emitters ───────────────────────────

    def notify_data_changed(self, key: str, payload: object) -> None:
        """Emit data_changed signal if bridge is active."""
        if self._bridge:
            self._bridge.data_changed.emit(key, payload)

    def notify_system_notification(self, title: str, message: str, level: str = "info") -> None:
        """Emit system_notification signal."""
        if self._bridge and level in ("info", "warning", "error"):
            self._bridge.system_notification.emit(title, message, level)

    def notify_pet_mood(self, pet_id: str, mood: str) -> None:
        """Emit pet_mood_changed signal."""
        if self._bridge:
            self._bridge.pet_mood_changed.emit(pet_id, mood)

    def notify_sync_status(self, status: str, message: str, progress: int) -> None:
        """Emit sync_status_changed signal."""
        if self._bridge:
            self._bridge.sync_status_changed.emit(status, message, progress)
