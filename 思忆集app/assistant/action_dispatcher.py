"""Assistant action dispatcher — routes action_key to the correct UI handler.

Construction pattern (mirrors dialog_coordinator.py):
    dispatcher = AssistantActionDispatcher(main_window)
All UI callbacks delegate through self._mw.xxx().
"""
from __future__ import annotations

import logging


class AssistantActionDispatcher:
    """Dispatches action_key strings (news/weather/translate/notes/tasks/search etc.)
    to the corresponding MainWindow dialog-openers and helpers."""

    def __init__(self, main_window):
        """Create the dispatcher and store a reference to the main window.

        Parameters
        ----------
        main_window : MainWindow
            The owning main window (QMainWindow subclass).  All UI-facing calls
            are forwarded through ``self._mw``.
        """
        self._mw = main_window
        self._log = logging.getLogger("siyi.action_dispatcher")

    # ------------------------------------------------------------------
    #  public API required by the decoupling plan
    # ------------------------------------------------------------------

    @property
    def last_runtime_status(self) -> dict:
        """获取最近一次 _handle_runtime_status() 收集的状态快照。"""
        return dict(getattr(self, "_last_runtime_status", {}))

    def execute_action(self, action_key, target_id=None):
        """Execute the action identified by *action_key*.

        Parameters
        ----------
        action_key : str
            One of ``news``, ``tasks``, ``notes``, ``translate``, ``inbox``,
            ``capture``, ``organize_current``, ``floating``, ``self_optimize``,
            ``open_doctor``, ``alarms``, ``settings``, ``wordbook``,
            ``url_learning``.
        target_id : str or None
            Optional record id to focus after opening the dialog.

        Returns
        -------
        bool
            ``True`` when the action was executed successfully.
        """
        mw = self._mw

        if action_key == "tasks":
            dialog = mw.open_tasks_dialog()
            if dialog is None:
                return False
            if target_id is not None and hasattr(dialog, "focus_task_by_id"):
                return bool(dialog.focus_task_by_id(target_id))
            return True

        if action_key == "notes":
            dialog = mw.open_notes_dialog()
            if dialog is None:
                return False
            if target_id is not None and hasattr(dialog, "focus_note_by_id"):
                return bool(dialog.focus_note_by_id(target_id))
            return True

        if action_key == "translate":
            dialog = mw.open_translate_dialog()
            if dialog is None:
                return False
            if target_id is not None and hasattr(dialog, "focus_translation_by_id"):
                return bool(dialog.focus_translation_by_id(target_id))
            return True

        if action_key == "inbox":
            snapshot = mw._collect_dashboard_snapshot()
            if target_id is not None:
                inbox_repo = mw._get_inbox_repository()
                entry = inbox_repo.get_entry(target_id) if inbox_repo is not None else None
                if entry is not None:
                    mw.chat_mode_label.setText("动态收件箱")
                    mw._append_assistant_message(
                        "助手",
                        f"收件详情：{entry.get('title') or '未命名收件'}\n来源：{entry.get('source') or 'manual'}\n{entry.get('content') or ''}",
                    )
                    return True
            mw._append_assistant_message("助手", mw._build_inbox_brief(snapshot))
            mw._focus_inbox_capture()
            return True

        if action_key == "capture":
            return mw._capture_input_to_inbox(skip_action_guard=True)

        if action_key == "local.runtime_status":
            return self._handle_runtime_status()

        if action_key == "organize_current":
            return mw._organize_current_context()

        if action_key == "floating":
            mw.open_floating_assistant_dialog()
            return True

        if action_key == "self_optimize":
            return mw._generate_self_optimization_proposal()

        if action_key == "open_doctor":
            mw.open_desktop_doctor_dialog()
            return True

        if action_key == "workbench":
            mw._show_main_page(0)
            return True

        action_map = {
            "alarms": mw.open_alarm_dialog,
            "settings": mw.open_settings_dialog,
            "settings.shortcuts": lambda: mw.open_settings_dialog() or True,
            "settings.sources": lambda: mw.open_settings_dialog() or True,
            "settings.proactive": lambda: mw.open_settings_dialog() or True,
            "wordbook": mw.open_wordbook_dialog,
            "url_learning": mw.open_url_learning_dialog,
        }
        handler = action_map.get(action_key)
        if handler is not None:
            handler()
            return True

        # Fallback: try routing through capability_router if available
        if action_key in ("cron.add", "digest.create", "notes.organize",
                          "cron.add", "settings.shortcuts", "settings.sources",
                          "settings.proactive", "digest.show", "evolution.show"):
            ctx = getattr(self._mw, "app_context", None)
            if ctx is not None:
                manager = getattr(ctx, "autonomous_assistant_manager", None)
                if manager is not None:
                    router = getattr(manager, "router", None)
                    if router is not None and hasattr(router, "execute"):
                        try:
                            from assistant.models import PlannedStep
                            step = PlannedStep(
                                step_id=f"suggestion_{action_key}",
                                action_id=action_key,
                                payload={},
                                risk_level="low",
                            )
                            result = router.execute(step)
                            if result and result.get("status") in ("completed", "ok", "success"):
                                return True
                        except Exception:
                            self._log.debug("Fallback to capability_router failed for %s", action_key)

        self._log.debug("未处理的动作键: %s", action_key)
        return False

    def _handle_runtime_status(self) -> bool:
        """收集当前运行时状态快照并缓存到 self._last_runtime_status。"""
        from services.ai_service import create_fallback_client

        ctx = getattr(self._mw, "app_context", None)
        status: dict = {"status": "unavailable"}
        if ctx is not None:
            status["status"] = "ok"
            # AI 连接状态
            settings_repo = getattr(ctx, "settings_repository", None)
            if settings_repo is not None:
                try:
                    client = create_fallback_client(settings_repository=settings_repo)
                    probe = client.test_connection()
                    status["ai_online"] = "online" if isinstance(probe, dict) and probe.get("status") == "ok" else "offline"
                except Exception:
                    status["ai_online"] = "offline"
            # 运行时状态（从主窗口缓存读取）
            runtime_state = getattr(self._mw, "_assistant_runtime_state", {})
            status["last_mode"] = runtime_state.get("last_mode", "unknown")
            status["last_state"] = runtime_state.get("last_state", "idle")
            status["degraded"] = runtime_state.get("degraded", False)
        self._last_runtime_status = status
        return True
