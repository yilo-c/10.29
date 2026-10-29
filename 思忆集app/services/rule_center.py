"""规则一?績：把定时鎺ㄩ?併?佸?鐩樸?佷細璁?邯要等做成鍙?厤缃??鍒欍??
鏀?寔的触发类型：
- cron: 每天定时触发（HH:MM锛?- event: 系统事件触发（startup, shutdown, meeting_ended, off_work 等）
- manual: 仅手动触鍙?
每条规则包含鐩?爣（goal锛夈?佸喎却时闂淬?佸惎用开鍏炽??规则持久化到 local_storage，支鎸佸?删改鏌ャ??"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime
from typing import Any

logger = logging.getLogger("siyi.rule_center")

TRIGGER_CRON = "cron"
TRIGGER_EVENT = "event"
TRIGGER_MANUAL = "manual"


DEFAULT_RULES: list[dict[str, Any]] = [
    {
        "rule_id": "daily_brief",
        "name": "每日晨报推送",
        "trigger_type": TRIGGER_CRON,
        "trigger_config": {"time": "08:30"},
        "goal": "新闻资讯检索 + 今日待办摘要",
        "enabled": True,
        "cooldown_minutes": 720,
        "last_fired": None,
        "description": "每天 08:30 自动检索新闻并生成每日简报",
    },
    {
        "rule_id": "daily_review",
        "name": "每日复盘日志",
        "trigger_type": TRIGGER_CRON,
        "trigger_config": {"time": "22:30"},
        "goal": "日志复盘 + 使用情况蹇?収",
        "enabled": True,
        "cooldown_minutes": 720,
        "last_fired": None,
        "description": "每天 22:30 鑷?姩生成复盘日志和软件使用快鐓?",
    },
    {
        "rule_id": "meeting_end_journal",
        "name": "浼氳?后自动纪瑕?",
        "trigger_type": TRIGGER_EVENT,
        "trigger_config": {"event": "meeting_ended"},
        "goal": "日志 浼氳?绾??鑽夌?",
        "enabled": True,
        "cooldown_minutes": 15,
        "last_fired": None,
        "description": "浼氳?结束后自动生成会璁?邯要草绋?",
    },
    {
        "rule_id": "startup_news",
        "name": "鍚?姩时新闂绘?绱?",
        "trigger_type": TRIGGER_EVENT,
        "trigger_config": {"event": "startup"},
        "goal": "新闻璧勮? 每日绠?鎶?",
        "enabled": True,
        "cooldown_minutes": 60,
        "last_fired": None,
        "description": "姣忔?鍚?姩时自鍔ㄦ?索最新新闂?",
    },
    {
        "rule_id": "shutdown_usage",
        "name": "关闭时软件快鐓?",
        "trigger_type": TRIGGER_EVENT,
        "trigger_config": {"event": "shutdown"},
        "goal": "使用情况蹇?収",
        "enabled": True,
        "cooldown_minutes": 30,
        "last_fired": None,
        "description": "关闭时自鍔ㄨ?录软件使用快鐓?",
    },
    {
        "rule_id": "off_work_review",
        "name": "下班复盘",
        "trigger_type": TRIGGER_EVENT,
        "trigger_config": {"event": "off_work"},
        "goal": "日志 下班复盘鑽夌?",
        "enabled": True,
        "cooldown_minutes": 60,
        "last_fired": None,
        "description": "下班/关窗口时鑷?姩生成复盘日志",
    },
    {
        "rule_id": "weekly_notes_sync",
        "name": "每周笔记?同步?",
        "trigger_type": TRIGGER_CRON,
        "trigger_config": {"time": "09:00", "weekday": 1},
        "goal": "笔记?同步? + 笔记?分类整理",
        "enabled": False,
        "cooldown_minutes": 1440,
        "last_fired": None,
        "description": "每周一? 09:00 鑷?姩同步?笔记?并整理分绫?",
    },
    {
        "rule_id": "weekly_tech_digest",
        "name": "每周鎶?鏈?拷韪?",
        "trigger_type": TRIGGER_CRON,
        "trigger_config": {"time": "18:30", "weekday": 5},
        "goal": "生成鎶?鏈?懆鎶?鎶?鏈?懆鎶?鏈?懆鎶?鏈?",
        "enabled": True,
        "cooldown_minutes": 10080,
        "last_fired": None,
        "description": "每周了?18:30 鑷?姩生成关注涓婚?的技鏈?懆鎶?",
    },
]


class RuleCenter:
    """规则一?績服务锛氱?理自动化规则鐨勫?删改查与触发评估。"""

    def __init__(self, data_store, settings_repository=None, observability_service=None):
        self.data_store = data_store
        self.settings_repository = settings_repository
        self.observability_service = observability_service

    # 鈹?鈹? 规则 CRUD 鈹?鈹?

    def list_rules(self) -> list[dict[str, Any]]:
        rules = self.data_store.load_json("rule_center_rules", None)
        if isinstance(rules, list) and rules:
            return rules
        rules = [dict(r) for r in DEFAULT_RULES]
        self.data_store.save_json("rule_center_rules", rules)
        return rules

    def get_rule(self, rule_id: str) -> dict[str, Any] | None:
        for rule in self.list_rules():
            if rule.get("rule_id") == rule_id:
                return rule
        return None

    def add_rule(
        self,
        *,
        name: str,
        trigger_type: str,
        trigger_config: dict[str, Any],
        goal: str,
        enabled: bool = True,
        cooldown_minutes: int = 60,
        description: str = "",
    ) -> dict[str, Any]:
        rule_id = f"custom_{uuid.uuid4().hex[:8]}"
        rule = {
            "rule_id": rule_id,
            "name": str(name or "").strip(),
            "trigger_type": trigger_type if trigger_type in (TRIGGER_CRON, TRIGGER_EVENT, TRIGGER_MANUAL) else TRIGGER_MANUAL,
            "trigger_config": dict(trigger_config or {}),
            "goal": str(goal or "").strip(),
            "enabled": bool(enabled),
            "cooldown_minutes": max(1, int(cooldown_minutes)),
            "last_fired": None,
            "description": str(description or "").strip(),
        }
        rules = self.list_rules()
        rules.append(rule)
        self.data_store.save_json("rule_center_rules", rules)
        return rule

    def update_rule(self, rule_id: str, updates: dict[str, Any]) -> bool:
        rules = self.list_rules()
        for rule in rules:
            if rule.get("rule_id") == rule_id:
                for key in ("name", "trigger_type", "trigger_config", "goal", "enabled",
                            "cooldown_minutes", "description"):
                    if key in updates:
                        rule[key] = updates[key]
                self.data_store.save_json("rule_center_rules", rules)
                return True
        return False

    def remove_rule(self, rule_id: str) -> bool:
        rules = self.list_rules()
        original_len = len(rules)
        rules = [r for r in rules if r.get("rule_id") != rule_id]
        if len(rules) < original_len:
            self.data_store.save_json("rule_center_rules", rules)
            return True
        return False

    def toggle_rule(self, rule_id: str, enabled: bool | None = None) -> bool:
        rules = self.list_rules()
        for rule in rules:
            if rule.get("rule_id") == rule_id:
                if enabled is not None:
                    rule["enabled"] = bool(enabled)
                else:
                    rule["enabled"] = not bool(rule.get("enabled", True))
                self.data_store.save_json("rule_center_rules", rules)
                return True
        return False

    # 鈹?鈹? 触发评估 鈹?鈹?

    def evaluate_cron_rules(self, now: datetime | None = None) -> list[dict[str, Any]]:
        now = now or datetime.now()
        due: list[dict[str, Any]] = []
        for rule in self.list_rules():
            if not rule.get("enabled"):
                continue
            if rule.get("trigger_type") != TRIGGER_CRON:
                continue
            if self._is_cron_due(rule, now):
                due.append(rule)
        return due

    def evaluate_event_rules(self, event_name: str) -> list[dict[str, Any]]:
        now = datetime.now()
        matched: list[dict[str, Any]] = []
        for rule in self.list_rules():
            if not rule.get("enabled"):
                continue
            if rule.get("trigger_type") != TRIGGER_EVENT:
                continue
            config = rule.get("trigger_config") or {}
            if str(config.get("event", "")).strip().lower() == str(event_name or "").strip().lower():
                if self._check_cooldown(rule, now):
                    matched.append(rule)
        return matched

    def mark_fired(self, rule_id: str, now: datetime | None = None) -> None:
        now = now or datetime.now()
        rules = self.list_rules()
        for rule in rules:
            if rule.get("rule_id") == rule_id:
                rule["last_fired"] = now.isoformat()
                break
        self.data_store.save_json("rule_center_rules", rules)

    # 鈹?鈹? 内部方法 鈹?鈹?

    def _is_cron_due(self, rule: dict[str, Any], now: datetime) -> bool:
        config = rule.get("trigger_config") or {}
        target_time = str(config.get("time") or "").strip()
        if not target_time or ":" not in target_time:
            return False

        current_hhmm = now.strftime("%H:%M")
        if current_hhmm != target_time:
            return False

        weekday = config.get("weekday")
        if weekday is not None:
            try:
                if now.isoweekday() != int(weekday):
                    return False
            except (ValueError, TypeError):
                logger.debug("Invalid weekday config value: %s", weekday, exc_info=True)

        return self._check_cooldown(rule, now)

    def _check_cooldown(self, rule: dict[str, Any], now: datetime) -> bool:
        last_fired_raw = rule.get("last_fired")
        if not last_fired_raw:
            return True
        try:
            last_fired = datetime.fromisoformat(str(last_fired_raw))
            cooldown = max(1, int(rule.get("cooldown_minutes") or 60))
            elapsed_minutes = (now - last_fired).total_seconds() / 60
            return elapsed_minutes >= cooldown
        except (ValueError, TypeError):
            return True

    def _track(self, event: str, payload: dict[str, Any]) -> None:
        if self.observability_service is None:
            return
        try:
            from core.observability import SEVERITY_INFO
            self.observability_service.track_event(event, severity=SEVERITY_INFO, payload=payload)
        except Exception:
            logger.warning("failed to track rule center event: %s", event)
