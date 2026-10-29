"""Active proactive agent — context trigger engine.

12 trigger types (Phase 5.3: expanded from 5 to 12):
- idle: user idle > 15 min → memory review card
- morning_brief: weekday 09:00 → daily briefing
- content: note/task created → related suggestions
- system: CPU/memory high load → optimization suggestion
- emotion (Phase 3.2): AI response has ALERT/SUCCESS → follow-up
- selection: user selects text
- blank_hotkey: hotkey wake-up
- system_tray: tray menu action
- clipboard: new clipboard content detected
- foreground_window: foreground window switch detected
- url_intercept: URL detected
- timed: scheduled timer tasks
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from typing import Any, Callable

from core.events import AppEvent, event_bus as _global_event_bus

logger = logging.getLogger("siyi.proactive")

_IDLE_COOLDOWN_MINUTES = 240
_BRIEF_COOLDOWN_HOURS = 23
_CONTENT_COOLDOWN_SECONDS = 300
_SYSTEM_COOLDOWN_MINUTES = 60
_EMOTION_COOLDOWN_SECONDS = 120
_TIMED_TRIGGER_INTERVAL = timedelta(minutes=15)

_IDLE_THRESHOLD_MINUTES = 15

# 第二档 #5：事件驱动触发器冷却（秒）—— 防止高频事件刷屏
_EVENT_COOLDOWNS: dict[str, int] = {
    "selection": 30,
    "clipboard": 60,
    "foreground_window": 120,
    "url_intercept": 30,
    "notification": 20,
    "calendar": 0,
}

# 模块级事件订阅登记表：保证每个事件类型在总线上只有一个 handler（幂等）。
# proactive.tick 每 30 分钟新建一个 ProactiveTriggerEngine 实例，若不在此去重，
# 总线上会堆积 N 个 handler 导致重复投递与 handler 泄漏。登记最新实例的 handler，
# 旧实例的 handler 在重新订阅时被先 off 再 on。
_EVENT_SUBSCRIBERS: dict[str, Any] = {}


class ProactiveTriggerEngine:
    """Evaluate conditions and deliver proactive suggestions."""

    def __init__(
        self,
        inbox_service=None,
        desktop_doctor=None,
        workbench_service=None,
        personal_task_repository=None,
        personal_note_repository=None,
        weather_service=None,
        chat_callback=None,
        event_bus=None,
    ):
        self._inbox = inbox_service
        self._doctor = desktop_doctor
        self._workbench = workbench_service
        self._task_repo = personal_task_repository
        self._note_repo = personal_note_repository
        self._weather = weather_service
        self._chat_callback = chat_callback
        # P2.3: 注入 EventBus 用于发射 proactive.triggered 事件；
        # 默认使用 core.events 全局单例（与 EventBridge 订阅的总线一致）
        self._event_bus = event_bus if event_bus is not None else _global_event_bus

        # cooldown state
        self._last_idle_fire: datetime | None = None
        self._last_brief_date: date | None = None
        self._last_content_fire: datetime | None = None
        self._last_system_fire: datetime | None = None
        self._last_interaction_time: datetime | None = None
        self._last_timed_fire: datetime | None = None

        # 事件驱动触发器冷却时间戳（第二档 #5）
        self._last_event_fire: dict[str, datetime] = {}

        self._enabled_triggers: set[str] = {
            "idle",
            "morning_brief",
            "content",
            "system",
            "emotion",
            "selection",
            "blank_hotkey",
            "system_tray",
            "clipboard",
            "foreground_window",
            "url_intercept",
            "timed",
            # 第二档 #5：把"通知 / 日历临近"也纳入主动性来源（听事件，不只有定时）
            "notification",
            "calendar",
        }

        # emotion trigger buffer
        self._last_emotion_value: str | None = None
        self._last_emotion_fire: datetime | None = None

    def set_enabled_triggers(self, triggers: set[str]):
        self._enabled_triggers = set(triggers)

    def is_trigger_enabled(self, name: str) -> bool:
        return name in self._enabled_triggers

    def notify_interaction(self):
        self._last_interaction_time = datetime.now()

    def notify_emotion_change(self, emotion_value: str) -> None:
        if not self.is_trigger_enabled("emotion"):
            return
        if emotion_value not in ("alert", "success"):
            return
        if emotion_value == self._last_emotion_value:
            return
        self._last_emotion_value = emotion_value
        item = self._try_fire_emotion(emotion_value)
        if item:
            self.deliver(item)

    def get_idle_minutes(self) -> float:
        if self._last_interaction_time is None:
            return 0.0
        return (datetime.now() - self._last_interaction_time).total_seconds() / 60.0

    def notify_content_created(self, content_type: str, content_id: str, title: str = ""):
        if not self.is_trigger_enabled("content"):
            return
        self._try_fire_content(content_type, content_id, title)

    # Phase 5.3: new trigger notification methods
    def _fire_event_trigger(self, trigger: str, build: Callable[[], dict[str, Any] | None]) -> None:
        """事件驱动触发器统一入口：开关检查 + 冷却抑制 + 构建 + 投递。

        所有"听事件"类 notify_* 都经此入口，确保高频事件（复制 / 切窗 / 通知）
        不会刷屏，且与 tick() 共用 deliver() 管道（inbox + proactive.triggered
        + proactive.suggestion + chat 回调），即"info.brief 同款管道"。
        """
        if not self.is_trigger_enabled(trigger):
            return
        cooldown = _EVENT_COOLDOWNS.get(trigger, 30)
        now = datetime.now()
        if cooldown > 0:
            last = self._last_event_fire.get(trigger)
            if last is not None and (now - last).total_seconds() < cooldown:
                return
        item = build()
        if not item:
            return
        self._last_event_fire[trigger] = now
        self.deliver(item)

    @staticmethod
    def _ev_payload(event: Any) -> dict[str, Any]:
        """从 EventBus handler 收到的参数中解出 payload（兼容 AppEvent 与裸 dict）。"""
        if isinstance(event, AppEvent):
            return event.payload if isinstance(event.payload, dict) else {}
        return event if isinstance(event, dict) else {}

    @classmethod
    def _ev_text(cls, event: Any) -> str:
        p = cls._ev_payload(event)
        return p.get("text", "") if isinstance(p, dict) else ""

    @classmethod
    def _ev_field(cls, event: Any, field: str) -> str:
        p = cls._ev_payload(event)
        return p.get(field, "") if isinstance(p, dict) else ""

    def notify_selection(self, selected_text: str) -> None:
        """选中文本触发（事件驱动：selection.detected）。"""

        def _build() -> dict[str, Any] | None:
            if not selected_text or len(selected_text.strip()) < 5:
                return None
            return {
                "title": "选中文本",
                "content": f"是否要对选中内容进行操作？\n\n「{selected_text[:200]}」",
                "channel": "inbox",
                "priority": "low",
                "source": "proactive_selection",
                "source_id": f"sel_{datetime.now().strftime('%Y%m%d_%H%M%S')}",
                "tags": ["proactive", "selection"],
                "metadata": {"trigger": "selection", "text": selected_text[:500]},
            }

        self._fire_event_trigger("selection", _build)

    def notify_blank_hotkey(self) -> None:
        """空白热键唤醒触发。"""
        if not self.is_trigger_enabled("blank_hotkey"):
            return
        item = {
            "title": "思忆集助手",
            "content": "随时待命。输入需求或选择内容后按快捷键。",
            "channel": "inbox",
            "priority": "low",
            "source": "proactive_blank_hotkey",
            "source_id": f"hotkey_{datetime.now().strftime('%Y%m%d_%H%M%S')}",
            "tags": ["proactive", "hotkey"],
            "metadata": {"trigger": "blank_hotkey"},
        }
        self.deliver(item)

    def notify_clipboard(self, text: str) -> None:
        """剪贴板内容变化触发（事件驱动：clipboard.changed）。

        后端剪贴板轮询（services.clipboard_context）监测到新复制内容即 emit 该事件，
        把"复制"从"弹卡片等点击"升级为"主动问一句要不要处理"（第二档 #4 接 #5）。
        """

        def _build() -> dict[str, Any] | None:
            if not text or len(text.strip()) < 3:
                return None
            return {
                "title": "剪贴板更新",
                "content": f"你刚复制了内容，需要我处理吗？\n\n「{text[:200]}」",
                "channel": "inbox",
                "priority": "low",
                "source": "proactive_clipboard",
                "source_id": f"clip_{datetime.now().strftime('%Y%m%d_%H%M%S')}",
                "tags": ["proactive", "clipboard"],
                "metadata": {"trigger": "clipboard", "text": text[:500]},
            }

        self._fire_event_trigger("clipboard", _build)

    def notify_foreground_window(self, title: str) -> None:
        """前台窗口切换触发（事件驱动：foreground.window.changed）。"""

        def _build() -> dict[str, Any] | None:
            if not title or len(title.strip()) < 2:
                return None
            return {
                "title": "切换到了新窗口",
                "content": f"当前窗口：{title}\n需要我针对此应用做点什么吗？",
                "channel": "inbox",
                "priority": "low",
                "source": "proactive_foreground",
                "source_id": f"fg_{datetime.now().strftime('%Y%m%d_%H%M%S')}",
                "tags": ["proactive", "foreground"],
                "metadata": {"trigger": "foreground_window", "title": title[:200]},
            }

        self._fire_event_trigger("foreground_window", _build)

    def notify_calendar_reminder(self, payload: dict[str, Any] | None) -> None:
        """日历临近提醒触发（事件驱动：calendar.reminder）。

        日历源接入后端 emit calendar.reminder 后，这里自动产出一条管家提醒，
        无需改动即可生效（第二档 #5：把"定时"扩成"听事件"）。
        """

        def _build() -> dict[str, Any] | None:
            p = payload if isinstance(payload, dict) else {}
            title = p.get("title") or p.get("summary") or "日程提醒"
            when = p.get("time") or p.get("when") or ""
            return {
                "title": "日程临近",
                "content": (f"提醒：{title}" + (f"（{when}）" if when else "") + "\n需要我帮你准备或设提醒吗？"),
                "channel": "inbox",
                "priority": "normal",
                "source": "proactive_calendar",
                "source_id": f"cal_{datetime.now().strftime('%Y%m%d_%H%M%S')}",
                "tags": ["proactive", "calendar"],
                "metadata": {"trigger": "calendar", "title": title, "when": when},
            }

        self._fire_event_trigger("calendar", _build)

    def notify_notification(self, payload: dict[str, Any] | None) -> None:
        """通知到达触发（事件驱动：notification.delivered）。

        把"通知"转译为一条管家消息，扩展主动性来源（从定时 → 听事件）。
        防回环：proactive.triggered 自身会经 NotificationDispatcher 落库并 emit
        notification.delivered，这里跳过 proactive.* 来源，否则会无限套娃。
        """

        def _build() -> dict[str, Any] | None:
            p = payload if isinstance(payload, dict) else {}
            orig = p.get("event_type", "") or ""
            if orig.startswith("proactive."):
                return None
            title = p.get("title") or orig or "通知"
            body = p.get("body") or ""
            return {
                "title": f"提醒：{title}",
                "content": f"收到一条通知：{title}\n{body}\n需要我帮你处理或归档吗？",
                "channel": "inbox",
                "priority": "low",
                "source": "proactive_notification",
                "source_id": f"notif_{datetime.now().strftime('%Y%m%d_%H%M%S')}",
                "tags": ["proactive", "notification"],
                "metadata": {"trigger": "notification", "event_type": orig},
            }

        self._fire_event_trigger("notification", _build)

    def notify_url_intercept(self, url: str) -> None:
        """URL 拦截触发（事件驱动：url.intercepted）。"""

        def _build() -> dict[str, Any] | None:
            if not url or not str(url).strip():
                return None
            return {
                "title": "检测到链接",
                "content": f"检测到链接：{url}\n是否打开或保存到收件箱？",
                "channel": "inbox",
                "priority": "low",
                "source": "proactive_url_intercept",
                "source_id": f"url_{datetime.now().strftime('%Y%m%d_%H%M%S')}",
                "tags": ["proactive", "url"],
                "metadata": {"trigger": "url_intercept", "url": url},
            }

        self._fire_event_trigger("url_intercept", _build)

    # ── 事件驱动订阅（第二档 #5：把"定时"扩成"听事件"）──────────────
    def subscribe_events(self, event_bus: Any = None) -> None:
        """订阅 EventBus 事件，将事件驱动触发器接入主动投递管道。

        事件源（均为真实已存在或预期存在的总线事件）：
        - selection.detected          → notify_selection
        - clipboard.changed           → notify_clipboard（后端剪贴板轮询 emit）
        - foreground.window.changed    → notify_foreground_window
        - url.intercepted             → notify_url_intercept
        - notification.delivered       → notify_notification（NotificationDispatcher 落库后 emit）
        - calendar.reminder            → notify_calendar_reminder（日历源接入后自动生效）

        幂等：每个事件类型在总线上只保留最新实例的 handler（见 _EVENT_SUBSCRIBERS），
        避免 proactive.tick 每 30 分钟新建引擎导致的 handler 堆积与重复投递。
        """
        bus = event_bus if event_bus is not None else self._event_bus
        if bus is None:
            logger.debug("subscribe_events: 无 EventBus 可用，跳过事件订阅")
            return
        mapping = {
            "selection.detected": lambda e: self.notify_selection(self._ev_text(e)),
            "clipboard.changed": lambda e: self.notify_clipboard(self._ev_text(e)),
            "foreground.window.changed": lambda e: self.notify_foreground_window(self._ev_field(e, "title")),
            "url.intercepted": lambda e: self.notify_url_intercept(self._ev_field(e, "url")),
            "notification.delivered": lambda e: self.notify_notification(self._ev_payload(e)),
            "calendar.reminder": lambda e: self.notify_calendar_reminder(self._ev_payload(e)),
            # 第二档 #5：浏览器桥五事件之一 browser.captured（带 url/title）转译为
            # url.intercepted，使"链接拦截"触发真正有源（此前 url.intercepted 只有前端
            # 经 event.emit 才可达，浏览器扩展/桥截获的链接走 browser.captured）。
            "browser.captured": lambda e: self.notify_url_intercept(self._ev_field(e, "url")),
        }
        for evt, handler in mapping.items():
            prev = _EVENT_SUBSCRIBERS.get(evt)
            if prev is not None:
                try:
                    bus.off(evt, prev)
                except Exception:
                    logger.debug("取消旧事件订阅失败 %s", evt, exc_info=True)
            bus.on(evt, handler)
            _EVENT_SUBSCRIBERS[evt] = handler
        logger.info("ProactiveTriggerEngine 已订阅 %d 个事件类型（听事件模式）", len(mapping))

    def tick(self, context: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        """Run one trigger evaluation pass, returning items delivered."""
        results: list[dict[str, Any]] = []
        ctx = context or {}

        if self.is_trigger_enabled("idle"):
            item = self._try_fire_idle()
            if item:
                results.append(item)

        if self.is_trigger_enabled("morning_brief"):
            item = self._try_fire_morning_brief(ctx)
            if item:
                results.append(item)

        if self.is_trigger_enabled("system"):
            item = self._try_fire_system()
            if item:
                results.append(item)

        if self.is_trigger_enabled("timed"):
            item = self._try_fire_timed()
            if item:
                results.append(item)

        return results

    def _try_fire_idle(self) -> dict[str, Any] | None:
        idle_min = self.get_idle_minutes()
        if idle_min < _IDLE_THRESHOLD_MINUTES:
            return None
        if self._last_idle_fire is not None:
            elapsed = (datetime.now() - self._last_idle_fire).total_seconds() / 60
            if elapsed < _IDLE_COOLDOWN_MINUTES:
                return None
        self._last_idle_fire = datetime.now()
        return self._build_idle_card()

    def _build_idle_card(self) -> dict[str, Any]:
        today = date.today()
        text = (
            f"You've been working quietly for a while. Care to review today's records?\n"
            f"馃搮 {today.strftime('%Y-%m-%d')}  ·  Take a break and look back."
        )
        return {
            "title": "Time Review",
            "content": text,
            "channel": "inbox",
            "priority": "normal",
            "source": "proactive_idle",
            "source_id": f"idle_{today.isoformat()}",
            "tags": ["proactive", "review"],
            "metadata": {"trigger": "idle", "idle_minutes": round(self.get_idle_minutes(), 1)},
        }

    def _try_fire_morning_brief(self, context: dict[str, Any]) -> dict[str, Any] | None:
        now = datetime.now()
        today = now.date()
        if self._last_brief_date == today:
            return None
        if now.weekday() >= 5:
            return None
        if now.hour != 9 or now.minute > 30:
            return None
        self._last_brief_date = today
        return self._build_morning_brief(context)

    def _build_morning_brief(self, context: dict[str, Any]) -> dict[str, Any]:
        lines = [f"☀️ Good morning! Today is {date.today().strftime('%Y-%m-%d')}"]
        pending = self._load_pending_tasks()
        if pending:
            lines.append(f"馃搵 Pending tasks: {len(pending)} incomplete")
            for t in pending[:3]:
                title = t.get("title") or t.get("text", "")
                lines.append(f"  · {title}")
            if len(pending) > 3:
                lines.append(f"  ... and {len(pending) - 3} more")
        recent_notes = self._load_recent_notes(days=1)
        if recent_notes:
            lines.append(f"馃摑 Yesterday's notes: {len(recent_notes)} entries")
            for n in recent_notes[:3]:
                lines.append(f"  · {n.get('title', n.get('text', '(untitled)'))}")
        weather_text = context.get("weather_summary", "")
        if weather_text:
            lines.append(f"馃尋 {weather_text}")
        content = "\n".join(lines)
        return {
            "title": "Daily Briefing",
            "content": content,
            "channel": "both",
            "priority": "normal",
            "source": "proactive_morning_brief",
            "source_id": f"brief_{date.today().isoformat()}",
            "tags": ["proactive", "brief", "daily"],
            "metadata": {"trigger": "morning_brief"},
        }

    def _try_fire_content(self, content_type: str, content_id: str, title: str) -> dict[str, Any] | None:
        now = datetime.now()
        if self._last_content_fire is not None:
            elapsed = (now - self._last_content_fire).total_seconds()
            if elapsed < _CONTENT_COOLDOWN_SECONDS:
                return None
        self._last_content_fire = now
        suggestion = self._build_content_suggestion(content_type, content_id, title)
        if suggestion is None:
            return None
        return {
            "title": f"New {content_type} suggestion",
            "content": suggestion["text"],
            "channel": "inbox",
            "priority": "low",
            "source": "proactive_content",
            "source_id": f"content_{content_type}_{content_id}",
            "tags": ["proactive", "suggestion"],
            "metadata": {"trigger": "content", "content_type": content_type, "content_id": content_id},
        }

    def _build_content_suggestion(self, content_type: str, content_id: str, title: str) -> dict[str, Any] | None:
        content_text = ""
        if content_type == "note" and self._note_repo is not None:
            try:
                note = self._note_repo.find_by_id(content_id)
                if note:
                    content_text = str(getattr(note, "content", note.get("content", "")))
            except Exception:
                logger.debug("Non-critical error (see details)", exc_info=True)

        elif content_type == "task" and self._task_repo is not None:
            try:
                task = self._task_repo.find_by_id(content_id)
                if task:
                    content_text = str(getattr(task, "title", task.get("title", "")))
            except Exception:
                logger.debug("Non-critical error (see details)", exc_info=True)

        if not content_text:
            return None
        return {
            "text": f"Just created 「{title}」. Consider linking to related notes or tasks to establish knowledge connections.",
            "action": "notes",
            "reason": "New content can be linked to existing information",
            "convertible_actions": [{"label": "Open Notes", "action": "notes"}],
        }

    def _try_fire_system(self) -> dict[str, Any] | None:
        if self._doctor is None:
            return None
        now = datetime.now()
        if self._last_system_fire is not None:
            elapsed = (now - self._last_system_fire).total_seconds() / 60
            if elapsed < _SYSTEM_COOLDOWN_MINUTES:
                return None
        try:
            diagnostics = self._doctor.run_diagnostics(deep=False)
        except Exception:
            logger.warning("_try_fire_system diagnostics failed", exc_info=True)
            return None
        suggestion = self._build_system_suggestion(diagnostics)
        if suggestion is None:
            return None
        self._last_system_fire = now
        return {
            "title": "System Status Alert",
            "content": suggestion["text"],
            "channel": "inbox",
            "priority": "normal",
            "source": "proactive_system",
            "source_id": f"system_{now.strftime('%Y%m%d_%H%M')}",
            "tags": ["proactive", "system"],
            "metadata": {"trigger": "system", "diagnostics": diagnostics},
        }

    @staticmethod
    def _build_system_suggestion(diagnostics: dict[str, Any]) -> dict[str, Any] | None:
        issues = []
        sys_info = diagnostics.get("system", {})
        cpu = sys_info.get("cpu_percent", 0)
        mem = sys_info.get("memory_percent", 0)
        if cpu > 80:
            issues.append(f"CPU usage {cpu:.0f}%, sustained high load")
        if mem > 85:
            issues.append(f"Memory usage {mem:.0f}%, near limit")
        if not issues:
            return None
        return {
            "text": "System resource warning:\n"
            + "\n".join(f"  ⚠ {i}" for i in issues)
            + "\nConsider checking running processes or restarting the app to free resources.",
        }

    def _try_fire_emotion(self, emotion_value: str) -> dict[str, Any] | None:
        now = datetime.now()
        if self._last_emotion_fire is not None:
            elapsed = (now - self._last_emotion_fire).total_seconds()
            if elapsed < _EMOTION_COOLDOWN_SECONDS:
                return None
        self._last_emotion_fire = now
        return self._build_emotion_suggestion(emotion_value)

    def _build_emotion_suggestion(self, emotion_value: str) -> dict[str, Any] | None:
        if emotion_value == "alert":
            return {
                "title": "Seems like an issue",
                "content": "It appears the AI ran into trouble. Need me to retry, rephrase, or check help?",
                "channel": "inbox",
                "priority": "normal",
                "source": "proactive_emotion",
                "source_id": f"emotion_alert_{datetime.now().strftime('%Y%m%d_%H%M%S')}",
                "tags": ["proactive", "emotion", "alert"],
                "metadata": {"trigger": "emotion", "emotion": "alert"},
            }
        if emotion_value == "success":
            idle = self.get_idle_minutes()
            if idle < 2:
                return None
            return {
                "title": "Task Complete",
                "content": "The task was executed smoothly. How about organizing notes, checking todos, or browsing news?",
                "channel": "inbox",
                "priority": "low",
                "source": "proactive_emotion",
                "source_id": f"emotion_success_{datetime.now().strftime('%Y%m%d_%H%M%S')}",
                "tags": ["proactive", "emotion", "success"],
                "metadata": {"trigger": "emotion", "emotion": "success"},
            }
        return None

    def _try_fire_timed(self) -> dict[str, Any] | None:
        """Phase 5.3: Timed trigger — fires every TIMED_TRIGGER_INTERVAL if idle."""
        now = datetime.now()
        if self._last_timed_fire is not None:
            if now - self._last_timed_fire < _TIMED_TRIGGER_INTERVAL:
                return None
        idle_min = self.get_idle_minutes()
        if idle_min < 5:
            return None
        self._last_timed_fire = now
        return {
            "title": "Regular Check-in",
            "content": "Just checking in. Anything you need help with? I can review tasks, organize notes, or search for information.",
            "channel": "inbox",
            "priority": "low",
            "source": "proactive_timed",
            "source_id": f"timed_{now.strftime('%Y%m%d_%H%M')}",
            "tags": ["proactive", "timed"],
            "metadata": {"trigger": "timed", "idle_minutes": round(idle_min, 1)},
        }

    def _load_pending_tasks(self) -> list[dict[str, Any]]:
        if self._task_repo is None:
            return []
        try:
            return self._task_repo.list_pending() or []
        except Exception:
            logger.warning("_load_pending_tasks failed", exc_info=True)
            return []

    def _load_recent_notes(self, days: int = 1) -> list[dict[str, Any]]:
        if self._note_repo is None:
            return []
        try:
            return self._note_repo.list_recent(days=days) or []
        except Exception:
            logger.warning("_load_recent_notes failed", exc_info=True)
            return []

    def deliver(self, item: dict[str, Any]) -> dict[str, Any] | None:
        if self._inbox is None:
            return None
        try:
            # P2.3: 在投递前先发射 proactive.triggered 事件，让前端能实时收到
            # 带完整元数据（trigger 类型、idle 分钟数等）的主动建议，
            # 而无需依赖 inbox.delivered 通用事件（其 payload 缺少 trigger 上下文）
            self._emit_proactive_event(item)

            result = self._inbox.deliver(
                title=item.get("title", ""),
                content=item.get("content", ""),
                channel=item.get("channel", "inbox"),
                priority=item.get("priority", "normal"),
                source=item.get("source", "proactive"),
                source_id=item.get("source_id", ""),
                tags=item.get("tags", []),
                metadata=item.get("metadata", {}),
            )
            if self._chat_callback is not None and self.get_idle_minutes() > 2:
                content = item.get("content", "")
                title = item.get("title", "")
                if content:
                    try:
                        self._chat_callback(title, content, item.get("metadata", {}))
                    except Exception:
                        logger.debug("chat callback failed", exc_info=True)
            return result
        except Exception as exc:
            logger.warning("proactive deliver failed: %s", exc)
            return None

    def _emit_proactive_event(self, item: dict[str, Any]) -> None:
        """发射 proactive.* 事件到 EventBus（best-effort，不抛异常）。"""
        if self._event_bus is None:
            return
        try:
            metadata = item.get("metadata") or {}
            trigger_type = str(metadata.get("trigger") or "unknown")
            payload = {
                "trigger": trigger_type,
                "title": item.get("title", ""),
                "content": item.get("content", ""),
                "priority": item.get("priority", "normal"),
                "source": item.get("source", "proactive"),
                "source_id": item.get("source_id", ""),
                "channel": item.get("channel", "inbox"),
                "tags": list(item.get("tags") or []),
                "metadata": dict(metadata),
            }
            self._event_bus.emit(
                AppEvent(
                    event_type="proactive.triggered",
                    source="proactive_triggers",
                    payload=payload,
                )
            )
            # 同步 emit proactive.suggestion：前端 Orb（useBackendEvents）实际监听
            # 该事件以点亮主动建议 + 注入聊天面板；此前引擎只 emit proactive.triggered，
            # 导致 Orb 的主动建议路径长期 dead（建成未启用，第二档 #5 一并修复）。
            self._event_bus.emit(
                AppEvent(
                    event_type="proactive.suggestion",
                    source="proactive_triggers",
                    payload=payload,
                )
            )
        except Exception:
            logger.debug("proactive event emit failed", exc_info=True)

    def deliver_all(self, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        results = []
        for item in items:
            result = self.deliver(item)
            if result:
                results.append(result)
        return results
