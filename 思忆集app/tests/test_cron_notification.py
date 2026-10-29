"""CronService 任务执行状态通知测试（S11 补齐）。

验证 CronService 在任务执行失败/成功/重试耗尽时通过 EventBus 发布通知事件，
使 NotificationDispatcher 能 fan-out 到 WebSocket/Toast 通道。
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from services.cron_service import CronService
from data.local_storage import LocalDataStore


class _SettingsRepo:
    def __init__(self, values=None):
        self._values = dict(values or {})

    def get_setting(self, key, default=None):
        return self._values.get(key, default)


class _FakeEventBus:
    """记录所有 emit 调用的假 EventBus。"""

    def __init__(self):
        self.events: list[tuple[str, dict, str]] = []

    def emit_sync(self, event_type: str, payload: dict | None = None, source: str = "") -> int:
        self.events.append((event_type, payload or {}, source))
        return 1

    async def emit(self, event_type: str, payload: dict | None = None) -> int:
        self.events.append((event_type, payload or {}, "async"))
        return 1


def _make_service(tmp_path, executor, event_bus=None, max_retries=0):
    """构造测试用 CronService。"""
    data_store = LocalDataStore(base_dir=str(tmp_path))
    service = CronService(
        data_store=data_store,
        executor=executor,
        settings_repository=_SettingsRepo({"cron_engine": "poll"}),
        event_bus=event_bus,
    )
    run_at = (datetime.now() - timedelta(minutes=1)).strftime("%Y-%m-%d %H:%M")
    service.add_job(
        "测试任务", "test.action", {"q": 1},
        run_at=run_at, repeat="none", max_retries=max_retries,
    )
    return service


def test_no_notification_without_event_bus(tmp_path):
    """无 EventBus 时不崩溃，正常执行。"""
    service = _make_service(tmp_path, lambda a, p: {"success": True})
    service._tick()  # 不应抛异常
    jobs = service.list_jobs(include_disabled=True)
    assert jobs[0]["enabled"] is False


def test_success_notification_for_retryable_job(tmp_path):
    """有重试配置的任务成功时发 cron.job_succeeded 通知。"""
    bus = _FakeEventBus()
    service = _make_service(
        tmp_path,
        lambda a, p: {"success": True},
        event_bus=bus,
        max_retries=3,
    )
    service._tick()
    types = [e[0] for e in bus.events]
    assert "cron.job_succeeded" in types
    payload = next(p for t, p, _ in bus.events if t == "cron.job_succeeded")
    assert payload["job_name"] == "测试任务"
    assert payload["action_id"] == "test.action"


def test_success_no_notification_for_non_retryable_job(tmp_path):
    """无重试配置(max_retries=0)的普通成功任务不发通知（避免噪音）。"""
    bus = _FakeEventBus()
    service = _make_service(
        tmp_path,
        lambda a, p: {"success": True},
        event_bus=bus,
        max_retries=0,
    )
    service._tick()
    types = [e[0] for e in bus.events]
    assert "cron.job_succeeded" not in types


def test_failure_notification_triggers_retry(tmp_path):
    """任务失败且未耗尽重试时发 cron.job_failed 通知。"""
    bus = _FakeEventBus()
    service = _make_service(
        tmp_path,
        lambda a, p: {"success": False, "error": "network timeout"},
        event_bus=bus,
        max_retries=3,
    )
    service._tick()
    types = [e[0] for e in bus.events]
    assert "cron.job_failed" in types
    payload = next(p for t, p, _ in bus.events if t == "cron.job_failed")
    assert payload["error"] == "network timeout"
    assert payload["retry_count"] == 1
    assert payload["max_retries"] == 3
    assert payload["next_run_at"]  # 有下次重试时间


def test_exhausted_notification_when_retries_depleted(tmp_path):
    """重试耗尽时发 cron.job_exhausted 通知（高优先级）。"""
    bus = _FakeEventBus()
    service = _make_service(
        tmp_path,
        lambda a, p: {"success": False, "error": "persistent failure"},
        event_bus=bus,
        max_retries=2,
    )
    # 第一次执行 → 失败 → 重试
    service._tick()
    assert any(t == "cron.job_failed" for t, _, _ in bus.events)

    # 模拟重试执行（手动推进 retry_count）
    with service._lock:
        job = service._jobs[0]
        job.retry_count = 2  # 已重试2次，下一次应耗尽
        job.next_run_at = (datetime.now() - timedelta(minutes=1)).strftime("%Y-%m-%d %H:%M:%S")
    service._tick()

    types = [e[0] for e in bus.events]
    assert "cron.job_exhausted" in types
    payload = next(p for t, p, _ in bus.events if t == "cron.job_exhausted")
    assert payload["error"] == "persistent failure"
    assert payload["max_retries"] == 2


def test_dispatch_failure_notification(tmp_path):
    """TaskQueue 入队失败时也发通知。"""
    bus = _FakeEventBus()

    class _BrokenQueue:
        def enqueue(self, **kwargs):
            raise RuntimeError("queue connection refused")

    data_store = LocalDataStore(base_dir=str(tmp_path))
    service = CronService(
        data_store=data_store,
        executor=lambda a, p: {"success": True},
        settings_repository=_SettingsRepo({"cron_engine": "poll"}),
        event_bus=bus,
        task_queue=_BrokenQueue(),
    )
    run_at = (datetime.now() - timedelta(minutes=1)).strftime("%Y-%m-%d %H:%M")
    service.add_job(
        "队列任务", "test.queue", {},
        run_at=run_at, repeat="none", max_retries=2,
    )
    service._tick()

    types = [e[0] for e in bus.events]
    # 入队失败应触发 job_failed（首次）或 job_exhausted（耗尽时）
    assert "cron.job_failed" in types or "cron.job_exhausted" in types
