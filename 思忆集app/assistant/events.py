from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from core.observability import SEVERITY_INFO
from core.settings_schema import KEY_ASSISTANT_DAILY_BRIEF_TIME, KEY_ASSISTANT_DAILY_REVIEW_TIME, KEY_RULE_CENTER_ENABLED

logger = logging.getLogger("siyi.assistant.events")


# 鈹?鈹? 统一事件入口常量 鈹?鈹?鈹?鈹?鈹?鈹?鈹?鈹?鈹?鈹?鈹?鈹?鈹?鈹?鈹?鈹?鈹?鈹?鈹?鈹?鈹?鈹?鈹?鈹?鈹?鈹?鈹?鈹?鈹?鈹?鈹?鈹?鈹?鈹?鈹?鈹?鈹?
EVENT_KIND_COMPLETION = "completion"
EVENT_KIND_REMINDER = "reminder"
EVENT_KIND_FAILURE = "failure"
EVENT_KIND_WAITING = "waiting_for_confirmation"
EVENT_KIND_SYSTEM = "system"
EVENT_KIND_CHAT = "chat"


class AssistantEventLayer:
    """事件层：规则一?績驱动的定时触鍙?+ 系统事件触发。"""

    def __init__(self, data_store, settings_repository, observability_service=None, rule_center=None, user_profile_service=None):
        self.data_store = data_store
        self.settings_repository = settings_repository
        self.observability_service = observability_service
        self.rule_center = rule_center
        self.user_profile_service = user_profile_service

    # 鈹?鈹? 统一用户交付入口 鈹?鈹?鈹?鈹?鈹?鈹?鈹?鈹?鈹?鈹?鈹?鈹?鈹?鈹?鈹?鈹?鈹?鈹?鈹?鈹?鈹?鈹?鈹?鈹?鈹?鈹?鈹?鈹?鈹?鈹?鈹?鈹?鈹?鈹?

    def deliver_to_user(
        self,
        *,
        kind: str = EVENT_KIND_SYSTEM,
        title: str = "",
        message: str = "",
        delivery_route: str = "inbox",
        priority: str = "normal",
        action_id: str = "",
        metadata: dict[str, Any] | None = None,
        inbox_service=None,
        character_runtime=None,
    ) -> dict[str, Any]:
        """Unified event delivery entry point."""
        event_entry = {
            "event": f"user_delivery.{kind}",
            "payload": {
                "kind": kind,
                "title": title,
                "message": message,
                "delivery_route": delivery_route,
                "priority": priority,
                "action_id": action_id,
                "metadata": dict(metadata or {}),
            },
            "time": datetime.now(timezone.utc).isoformat(),
            "kind": "user_delivery",
        }
        self.data_store.update_json(
            "assistant_events",
            [],
            lambda history: (list(history) if isinstance(history, list) else [])[-999:] + [event_entry],
        )

        result: dict[str, Any] = {
            "event_kind": kind,
            "title": title,
            "message": message,
            "delivery_results": [],
        }

        # 通过收件箱服务投閫?
        if inbox_service is not None:
            try:
                # 标准化交浠橀?氶亾鍚?
                channel_map = {
                    "subtitle": "subtitle_output",
                    "chat": "chat_output",
                    "floating": "floating_output",
                    "notification": "notification",
                    "inbox": "inbox",
                    "both": "both",
                    "subtitle_output": "subtitle_output",
                    "chat_output": "chat_output",
                    "floating_output": "floating_output",
                }
                channel = channel_map.get(delivery_route, "inbox")

                delivery_result = inbox_service.deliver(
                    title=title,
                    content=message,
                    channel=channel,
                    priority=priority,
                    source="task_result",
                    source_id=action_id,
                    tags=[kind, "user_delivery"],
                    metadata=metadata,
                )
                result["delivery_results"].append(delivery_result)
            except Exception as exc:
                result["delivery_results"].append({"delivered": False, "reason": str(exc)})

        # 通过角色灞傚?理（更新情绪锛?
        if character_runtime is not None:
            try:
                from .character_runtime import EmotionState, EmotionType

                emotion_map = {
                    EVENT_KIND_COMPLETION: EmotionType.SUCCESS,
                    EVENT_KIND_REMINDER: EmotionType.NEUTRAL,
                    EVENT_KIND_FAILURE: EmotionType.ALERT,
                    EVENT_KIND_WAITING: EmotionType.ALERT,
                }
                emotion_type = emotion_map.get(kind, EmotionType.NEUTRAL)
                character_runtime.update_emotion(
                    EmotionState(emotion=emotion_type, message=message[:80])
                )
                result["character_updated"] = True
            except Exception:
                result["character_updated"] = False

        self._track("assistant.event.user_delivery", SEVERITY_INFO, event_entry)
        return result

    def collect_due_events(self, now: datetime | None = None) -> list[dict[str, Any]]:
        now = now or datetime.now()
        events: list[dict[str, Any]] = []
        if not self._is_proactive_allowed("scheduled", now):
            return events

        if self.rule_center is not None and self._get_setting(KEY_RULE_CENTER_ENABLED, True):
            due_rules = self.rule_center.evaluate_cron_rules(now)
            for rule in due_rules:
                events.append(
                    {
                        "event": rule.get("rule_id", "unknown"),
                        "payload": {
                            "trigger": rule.get("trigger_config", {}).get("time", ""),
                            "rule_name": rule.get("name", ""),
                            "goal": rule.get("goal", ""),
                            "rule_id": rule.get("rule_id", ""),
                        },
                    }
                )
                self.rule_center.mark_fired(rule.get("rule_id", ""), now)
            return events

        for event_name, setting_key in (
            ("daily_brief", KEY_ASSISTANT_DAILY_BRIEF_TIME),
            ("daily_review", KEY_ASSISTANT_DAILY_REVIEW_TIME),
        ):
            trigger = str(self.settings_repository.get_setting(setting_key, "") or "").strip()
            if self._is_due(now, event_name, trigger):
                events.append({"event": event_name, "payload": {"trigger": trigger}})
                self._mark_event_fired(event_name, now)

        return events

    def collect_event_triggered_rules(self, event_name: str) -> list[dict[str, Any]]:
        if self.rule_center is None or not self._get_setting(KEY_RULE_CENTER_ENABLED, True):
            return []
        category = self._classify_event_category(event_name)
        if not self._is_proactive_allowed(category, datetime.now()):
            return []
        matched = self.rule_center.evaluate_event_rules(event_name)
        results = []
        for rule in matched:
            results.append(
                {
                    "event": event_name,
                    "payload": {
                        "rule_name": rule.get("name", ""),
                        "goal": rule.get("goal", ""),
                        "rule_id": rule.get("rule_id", ""),
                    },
                }
            )
            self.rule_center.mark_fired(rule.get("rule_id", ""), datetime.now())
        return results

    def emit_system_event(self, event_name: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        entry = {
            "event": str(event_name or "unknown").strip().lower(),
            "payload": dict(payload or {}),
            "time": datetime.now(timezone.utc).isoformat(),
            "kind": "system",
        }
        self.data_store.update_json(
            "assistant_events",
            [],
            lambda history: (list(history) if isinstance(history, list) else [])[-999:] + [entry],
        )
        self._track("assistant.event.emitted", SEVERITY_INFO, entry)
        return entry

    def _is_due(self, now: datetime, event_name: str, trigger_hhmm: str) -> bool:
        """判断事件是否到期。

        P0-2 修复（2026-08-02）：原实现使用 ``hhmm != trigger_hhmm`` 精确匹配，
        若调度循环在该分钟未运行（进程繁忙/重启/系统休眠），则该事件当天永久跳过。
        改为"当前时间 ≥ 到期时间点 且 当天未触发过"判定，支持错过 tick 补跑。

        持久化策略：
        - ``last_fired::{event_name}`` 存 ``YYYY-MM-DD``，每日只触发一次
        - ``last_checked`` 由调用方（``run_due_schedules`` 驱动循环）维护，
          用于诊断循环是否在运行（非触发判定依据）
        """
        if not trigger_hhmm or ":" not in trigger_hhmm:
            return False
        # 当前时间已过到期时间点（零填充 HH:MM 字典序 == 时间序，可直接比较）
        hhmm = now.strftime("%H:%M")
        if hhmm < trigger_hhmm:
            return False

        mark = self.data_store.load_json("assistant_rules", {})
        if not isinstance(mark, dict):
            mark = {}
        key = f"last_fired::{event_name}"
        today = now.strftime("%Y-%m-%d")
        return str(mark.get(key) or "") != today

    def _mark_event_fired(self, event_name: str, now: datetime) -> None:
        key = f"last_fired::{event_name}"
        value = now.strftime("%Y-%m-%d")
        self.data_store.update_json(
            "assistant_rules",
            {},
            lambda mark: {**(dict(mark) if isinstance(mark, dict) else {}), key: value},
        )

    def _track(self, event_name: str, severity: str, payload: dict[str, Any]) -> None:
        if self.observability_service is None:
            return
        try:
            self.observability_service.track_event(event_name, severity=severity, payload=payload)
        except Exception:
            logger.warning("failed to track assistant event: %s", event_name)

    def _get_setting(self, key: str, default: Any) -> Any:
        if self.settings_repository is None:
            return default
        return self.settings_repository.get_setting(key, default)

    def _is_proactive_allowed(self, category: str, now: datetime) -> bool:
        if self.user_profile_service is None:
            return True
        try:
            if not self.user_profile_service.is_proactive_allowed():
                return False
            if self.user_profile_service.is_in_quiet_hours(now):
                return False
            categories = set(self.user_profile_service.get_proactive_categories() or [])
            if categories and category not in categories:
                return False
        except Exception:
            logger.warning("proactive permission check failed for %s, defaulting to disallowed", category, exc_info=True)
            return False
        return True

    @staticmethod
    def _classify_event_category(event_name: str) -> str:
        normalized = str(event_name or "").strip().lower()
        if any(token in normalized for token in ("anomaly", "alert", "warn", "error", "failure", "exception")):
            return "anomaly"
        if any(token in normalized for token in ("match", "focus", "preference", "interest", "favorite")):
            return "high_value_match"
        return "anomaly"
