"""App 状态监控 — 窗口焦点、前后台切换等行为日志采集。"""

from __future__ import annotations

import logging
from typing import Any

from core.behavior_logger import BehaviorLogger
from domain.models.behavior import BehaviorEvent, CATEGORY_APP, SOURCE_APP_HOOK

logger = logging.getLogger("siyi.app_state_monitor")

try:
    from PyQt6.QtCore import QObject, QEvent
    from PyQt6.QtGui import QFocusEvent
    from PyQt6.QtWidgets import QApplication
except ImportError:  # pragma: no cover - PyQt6 可选依赖
    QObject = None  # type: ignore[misc, assignment]
    QEvent = None  # type: ignore[misc, assignment]
    QFocusEvent = None  # type: ignore[misc, assignment]
    QApplication = None  # type: ignore[misc, assignment]


class AppStateMonitor:
    """App 状态监控器。

    通过 PyQt6 事件钩子采集窗口焦点变化和应用前后台切换，
    记录为行为日志事件。
    """

    def __init__(self, behavior_logger: BehaviorLogger) -> None:
        self._logger = behavior_logger
        self._installed = False

    def install(self) -> None:
        """安装全局事件过滤器。"""
        if self._installed:
            return
        if QApplication is None:
            logger.warning("PyQt6 not available, app state monitor disabled")
            return

        app = QApplication.instance()
        if app is None:
            logger.warning("no QApplication instance, app state monitor disabled")
            return

        app.applicationStateChanged.connect(self._on_application_state_changed)  # type: ignore[attr-defined]
        self._installed = True
        self._logger.record(
            BehaviorEvent(category=CATEGORY_APP, action="app_state_monitor_installed", source=SOURCE_APP_HOOK)
        )
        logger.info("app state monitor installed")

    def _on_application_state_changed(self, state: Any) -> None:
        """处理应用状态变化（Qt.ApplicationState）。"""
        try:
            from PyQt6.QtCore import Qt

            action_map = {
                Qt.ApplicationState.ApplicationActive: "app_focus",
                Qt.ApplicationState.ApplicationInactive: "app_blur",
                Qt.ApplicationState.ApplicationSuspended: "app_suspended",
                Qt.ApplicationState.ApplicationHidden: "app_hidden",
            }
            action = action_map.get(state, f"app_state_{state}")
            self._logger.record(
                BehaviorEvent(category=CATEGORY_APP, action=action, source=SOURCE_APP_HOOK, detail={"state": str(state)})
            )
        except Exception:
            logger.debug("failed to record app state change", exc_info=True)
