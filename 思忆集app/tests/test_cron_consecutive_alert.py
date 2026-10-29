# -*- coding: utf-8 -*-
"""P0-4：cron 连续失败告警 + 通知白名单测试。"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from services.cron_service import CronJob, CronService
from data.local_storage import LocalDataStore


class _SettingsRepo:
    def __init__(self, values=None):
        self._values = dict(values or {})

    def get_setting(self, key, default=None):
        return self._values.get(key, default)


class _FakeEventBus:
    """记录 emit_sync 的事件总线。"""

    def __init__(self):
        self.events: list[tuple[str, dict]] = []

    def emit_sync(self, event_type, payload, source=None):
        self.events.append((event_type, payload))

    def alerts(self) -> list[dict]:
        return [p for t, p in self.events if t == "cron.job_alert"]


def _make_service(tmp_path, executor, bus):
    data_store = LocalDataStore(base_dir=str(tmp_path))
    return CronService(
        data_store=data_store,
        executor=executor,
        settings_repository=_SettingsRepo({"cron_engine": "poll"}),
        event_bus=bus,
    )


def _force_due(service):
    """回拨 last_run 到两分钟前，绕过分钟级去重，使下一轮 tick 必然到期。"""
    with service._lock:
        for job in service._jobs:
            past = (datetime.now() - timedelta(minutes=2)).strftime("%Y-%m-%d %H:%M:%S")
            job.last_run = past
            job.next_run_at = past


def _tick_times(service, n):
    for _ in range(n):
        service._tick()
        _force_due(service)


def test_consecutive_failures_trigger_alert_at_threshold(tmp_path):
    bus = _FakeEventBus()
    service = _make_service(
        tmp_path,
        executor=lambda action_id, payload: {"error": "boom"},
        bus=bus,
    )
    service.add_job("总是失败", "test.always_fail", {}, schedule="* * * * *", repeat="cron", max_retries=0)

    _tick_times(service, 3)

    alerts = bus.alerts()
    assert len(alerts) == 1, "连续失败达阈值（3）应触发一次告警"
    assert alerts[0]["consecutive_failures"] == 3
    assert "boom" in alerts[0]["error"]

    # 冷却期内（默认 1h）继续失败不重复告警
    _tick_times(service, 1)
    assert len(bus.alerts()) == 1, "冷却期内不应重复告警"
    job = service.list_jobs(include_disabled=True)[0]
    assert job["consecutive_failures"] == 4


def test_success_resets_consecutive_failures(tmp_path):
    bus = _FakeEventBus()
    state = {"fail": True}

    def executor(action_id, payload):
        return {"error": "boom"} if state["fail"] else {"ok": True}

    service = _make_service(tmp_path, executor, bus)
    service.add_job("时好时坏", "test.flaky", {}, schedule="* * * * *", repeat="cron", max_retries=0)

    _tick_times(service, 2)  # 连续失败 2 次（未达阈值）
    state["fail"] = False
    _tick_times(service, 1)  # 成功 → 清零
    state["fail"] = True
    _tick_times(service, 2)  # 重新累计 2 次（仍未达阈值）

    assert bus.alerts() == [], "成功应清零连续失败计数，未达阈值不告警"
    job = service.list_jobs(include_disabled=True)[0]
    assert job["consecutive_failures"] == 2


def test_alert_disabled_when_threshold_zero(tmp_path, monkeypatch):
    monkeypatch.setenv("SIYI_CRON_ALERT_THRESHOLD", "0")
    bus = _FakeEventBus()
    service = _make_service(
        tmp_path,
        executor=lambda action_id, payload: {"error": "boom"},
        bus=bus,
    )
    service.add_job("禁用告警", "test.no_alert", {}, schedule="* * * * *", repeat="cron", max_retries=0)

    _tick_times(service, 5)

    assert bus.alerts() == [], "阈值 0 应禁用连续失败告警"


def test_cronjob_roundtrip_preserves_alert_state():
    job = CronJob(
        job_id="j1",
        name="x",
        action_id="a.b",
        payload={},
        consecutive_failures=4,
        last_alert_at="2026-08-09 10:00:00",
    )
    restored = CronJob.from_dict(job.to_dict())
    assert restored.consecutive_failures == 4
    assert restored.last_alert_at == "2026-08-09 10:00:00"


def test_dispatcher_subscribes_cron_job_alert():
    from services.notification_dispatcher import (
        DEFAULT_SUBSCRIBED_EVENTS,
        _EVENT_DEFAULT_PRIORITY,
        _EVENT_TEMPLATES,
    )

    assert "cron.job_alert" in DEFAULT_SUBSCRIBED_EVENTS
    assert _EVENT_TEMPLATES["cron.job_alert"]["title"]
    assert _EVENT_DEFAULT_PRIORITY["cron.job_alert"] == "high"
