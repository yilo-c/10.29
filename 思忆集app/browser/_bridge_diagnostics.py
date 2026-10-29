"""诊断/事件/快照相关混入 — 从 bridge_content.py 提取。"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Protocol


class DiagnosticsHost(Protocol):
    """DiagnosticsMixin 依赖的宿主契约 — 由 BrowserAssistantBridgeService 运行时注入。"""
    workbench_service: Any
    note_repository: Any
    task_repository: Any
    news_repository: Any
    inbox_repository: Any
    translation_repository: Any
    proposal_repository: Any
    observability_service: Any
    _lock: Any
    _assistant_events: list[dict[str, Any]]

    def get_status_snapshot(self) -> dict[str, Any]: ...


class DiagnosticsMixin:
    """诊断快照、事件追踪相关方法。"""
    # 宿主契约：由 BrowserAssistantBridgeService 运行时注入
    if TYPE_CHECKING:
        workbench_service: Any
        note_repository: Any
        task_repository: Any
        news_repository: Any
        inbox_repository: Any
        translation_repository: Any
        proposal_repository: Any
        observability_service: Any
        _lock: Any
        _assistant_events: list[dict[str, Any]]

        def get_status_snapshot(self) -> dict[str, Any]: ...

    def get_diagnostics_snapshot(self) -> dict[str, Any]:
        snapshot = self._collect_snapshot()
        bridge = self.get_status_snapshot()
        workspace = {
            "pendingTasks": len(snapshot.get("pending_tasks") or []),
            "notes": len(snapshot.get("notes") or []),
            "inboxEntries": len(snapshot.get("inbox_entries") or []),
            "translations": len(snapshot.get("translations") or []),
            "favoriteNews": len(snapshot.get("favorite_news_keys") or []),
            "improvementProposals": len(snapshot.get("improvement_proposals") or []),
        }
        available_actions = {
            "summarize": True,
            "capture": self.workbench_service is not None,
            "createNote": self.note_repository is not None,
            "createTask": self.task_repository is not None,
        }
        tips = []
        if not bridge.get("enabled", True):
            tips.append("桌面端已禁用浏览器桥接，请先在应用设置中启用。")
        elif not bridge.get("running"):
            last_error = str(bridge.get("last_error") or "").strip()
            if last_error and last_error != "disabled":
                tips.append(f"浏览器桥接当前未运行：{last_error}")
            else:
                tips.append("浏览器桥接未启动，请先确认桌面端正在运行。")
        else:
            tips.append("桌面端桥接已就绪，可以直接总结当前网页并沉淀到收件箱或笔记。")
        if workspace["pendingTasks"] >= 5:
            tips.append("待办较多，适合优先把网页摘要转成任务而不是继续堆信息。")
        if workspace["inboxEntries"] >= 3:
            tips.append("动态收件箱已有积压，建议先整理最近一条收件再继续采集。")
        if not tips:
            tips.append("桌面端状态平稳，可以直接开始使用浏览器助手。")
        return {
            "ok": True,
            "bridge": bridge,
            "workspace": workspace,
            "availableActions": available_actions,
            "tips": tips,
        }

    def _collect_snapshot(self) -> dict[str, Any]:
        pending_tasks = []
        notes = []
        favorite_news_keys = []
        inbox_entries = []
        translations = []
        proposals = []

        if self.task_repository is not None:
            try:
                pending_tasks = [
                    task for task in self.task_repository.list_tasks(include_deleted=False)
                    if not bool(task.get("is_completed", task.get("completed", False)))
                ]
            except Exception as exc:
                import logging
                logging.getLogger("siyi.browser_bridge").debug("snapshot tasks failed: %s", exc)
        if self.note_repository is not None:
            try:
                notes = self.note_repository.list_notes(include_deleted=False)
            except Exception as exc:
                import logging
                logging.getLogger("siyi.browser_bridge").debug("snapshot notes failed: %s", exc)
        if self.news_repository is not None and hasattr(self.news_repository, "get_favorite_news_keys"):
            try:
                favorite_news_keys = self.news_repository.get_favorite_news_keys()
            except Exception as exc:
                import logging
                logging.getLogger("siyi.browser_bridge").debug("snapshot news failed: %s", exc)
        if self.inbox_repository is not None:
            try:
                inbox_entries = self.inbox_repository.list_entries(include_archived=False)
            except Exception as exc:
                import logging
                logging.getLogger("siyi.browser_bridge").debug("snapshot inbox failed: %s", exc)
        if self.translation_repository is not None and hasattr(self.translation_repository, "get_translation_history"):
            try:
                translations = self.translation_repository.get_translation_history()
            except Exception as exc:
                import logging
                logging.getLogger("siyi.browser_bridge").debug("snapshot translations failed: %s", exc)
        if self.proposal_repository is not None and hasattr(self.proposal_repository, "list_proposals"):
            try:
                proposals = self.proposal_repository.list_proposals()
            except Exception as exc:
                import logging
                logging.getLogger("siyi.browser_bridge").debug("snapshot proposals failed: %s", exc)

        return {
            "pending_tasks": pending_tasks,
            "notes": notes,
            "favorite_news_keys": favorite_news_keys,
            "inbox_entries": inbox_entries,
            "translations": translations,
            "improvement_proposals": proposals,
        }

    def _track_event(self, event_name: str, payload: dict[str, Any]):
        if self.observability_service is None:
            return
        try:
            self.observability_service.track_event(event_name, payload=payload or {})
        except Exception as exc:
            import logging
            logging.getLogger("siyi.browser_bridge").debug("browser bridge observability failed (%s): %s", event_name, exc)

    def _queue_assistant_event(self, event_type: str, payload: dict[str, Any] | None = None) -> None:
        event = {
            "event_type": str(event_type or "browser.unknown"),
            "payload": dict(payload or {}),
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        with self._lock:
            self._assistant_events.append(event)
            self._assistant_events = self._assistant_events[-80:]
        try:
            from core.events import AppEvent, event_bus
            event_bus.emit(AppEvent(
                event_type=str(event_type or "browser.unknown"),
                source="browser_bridge",
                payload=dict(payload or {}),
            ))
        except Exception:
            import logging
            logging.getLogger("siyi.browser_bridge").debug("failed to emit browser event to EventBus", exc_info=True)
