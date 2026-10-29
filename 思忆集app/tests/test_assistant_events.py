from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from assistant.events import AssistantEventLayer
from data.local_storage import LocalDataStore
from core.settings_schema import KEY_ASSISTANT_DAILY_BRIEF_TIME


class _SettingsRepo:
    def __init__(self, values=None):
        self._values = dict(values or {})

    def get_setting(self, key, default=None):
        return self._values.get(key, default)


class _ProfileService:
    def __init__(self, *, allowed=True, quiet=False, categories=None):
        self._allowed = allowed
        self._quiet = quiet
        self._categories = list(categories or ["scheduled", "anomaly", "high_value_match"])

    def is_proactive_allowed(self):
        return self._allowed

    def is_in_quiet_hours(self, now=None):
        return self._quiet

    def get_proactive_categories(self):
        return list(self._categories)


def test_event_layer_skips_scheduled_events_when_proactive_disabled(tmp_path):
    store = LocalDataStore(base_dir=str(tmp_path))
    now = datetime(2026, 4, 18, 9, 0)
    layer = AssistantEventLayer(
        data_store=store,
        settings_repository=_SettingsRepo({KEY_ASSISTANT_DAILY_BRIEF_TIME: "09:00"}),
        user_profile_service=_ProfileService(allowed=False),
    )

    events = layer.collect_due_events(now)

    assert events == []


def test_event_layer_skips_scheduled_events_in_quiet_hours(tmp_path):
    store = LocalDataStore(base_dir=str(tmp_path))
    now = datetime(2026, 4, 18, 9, 0)
    layer = AssistantEventLayer(
        data_store=store,
        settings_repository=_SettingsRepo({KEY_ASSISTANT_DAILY_BRIEF_TIME: "09:00"}),
        user_profile_service=_ProfileService(quiet=True),
    )

    events = layer.collect_due_events(now)

    assert events == []


def test_event_layer_skips_scheduled_events_when_category_not_enabled(tmp_path):
    store = LocalDataStore(base_dir=str(tmp_path))
    now = datetime(2026, 4, 18, 9, 0)
    layer = AssistantEventLayer(
        data_store=store,
        settings_repository=_SettingsRepo({KEY_ASSISTANT_DAILY_BRIEF_TIME: "09:00"}),
        user_profile_service=_ProfileService(categories=["anomaly"]),
    )

    events = layer.collect_due_events(now)

    assert events == []

    assert events == []


def test_event_layer_skips_disallowed_event_rule_category(tmp_path):
    store = LocalDataStore(base_dir=str(tmp_path))
    rule_center = type(
        "_RuleCenter",
        (),
        {
            "evaluate_event_rules": lambda self, event_name: [{"name": "閲嶈提醒", "goal": "给我鎺ㄩ€", "rule_id": "r1"}],
            "mark_fired": lambda self, rule_id, now: None,
        },
    )()
    layer = AssistantEventLayer(
        data_store=store,
        settings_repository=_SettingsRepo(),
        rule_center=rule_center,
        user_profile_service=_ProfileService(categories=["scheduled"]),
    )

    events = layer.collect_event_triggered_rules("important_match")

    assert events == []


# ── P0-2（2026-08-02）：_is_due 错过 tick 补跑测试 ──────────────────────────
# 修复前：使用 hhmm != trigger_hhmm 精确匹配，错过触发分钟则当天永久跳过
# 修复后：使用 hhmm < trigger_hhmm 判定，当前时间 ≥ 到期时间且当天未触发即补跑


def test_is_due_catches_up_after_missed_tick(tmp_path):
    """错过触发分钟后，后续 tick 应补跑（P0-2 核心修复）。"""
    store = LocalDataStore(base_dir=str(tmp_path))
    layer = AssistantEventLayer(
        data_store=store,
        settings_repository=_SettingsRepo({KEY_ASSISTANT_DAILY_BRIEF_TIME: "09:00"}),
        user_profile_service=_ProfileService(allowed=True, quiet=False, categories=["scheduled"]),
    )
    # 触发时间 09:00，当前 09:30（错过 30 分钟）
    now = datetime(2026, 8, 2, 9, 30)
    events = layer.collect_due_events(now)

    assert len(events) == 1
    assert events[0]["event"] == "daily_brief"


def test_is_due_does_not_fire_before_trigger_time(tmp_path):
    """未到触发时间不应触发。"""
    store = LocalDataStore(base_dir=str(tmp_path))
    layer = AssistantEventLayer(
        data_store=store,
        settings_repository=_SettingsRepo({KEY_ASSISTANT_DAILY_BRIEF_TIME: "09:00"}),
        user_profile_service=_ProfileService(allowed=True, quiet=False, categories=["scheduled"]),
    )
    now = datetime(2026, 8, 2, 8, 30)
    events = layer.collect_due_events(now)

    assert events == []


def test_is_due_does_not_fire_twice_same_day(tmp_path):
    """当天已触发后不应重复触发。"""
    store = LocalDataStore(base_dir=str(tmp_path))
    layer = AssistantEventLayer(
        data_store=store,
        settings_repository=_SettingsRepo({KEY_ASSISTANT_DAILY_BRIEF_TIME: "09:00"}),
        user_profile_service=_ProfileService(allowed=True, quiet=False, categories=["scheduled"]),
    )
    now = datetime(2026, 8, 2, 9, 5)
    # 第一次触发
    events1 = layer.collect_due_events(now)
    assert len(events1) == 1
    # 30 分钟后再次扫描，不应重复触发
    now_later = datetime(2026, 8, 2, 9, 35)
    events2 = layer.collect_due_events(now_later)
    assert events2 == []

