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


def test_cron_service_executes_one_shot_job_and_persists_state(tmp_path):
    data_store = LocalDataStore(base_dir=str(tmp_path))
    executed = []

    service = CronService(
        data_store=data_store,
        executor=lambda action_id, payload: executed.append((action_id, payload)) or {"ok": True},
        settings_repository=_SettingsRepo({"cron_engine": "poll"}),
    )

    run_at = (datetime.now() - timedelta(minutes=1)).strftime("%Y-%m-%d %H:%M")
    job = service.add_job("一次性测试", "news.lookup", {"query": "AI"}, run_at=run_at, repeat="none")

    service._tick()

    jobs = service.list_jobs(include_disabled=True)
    assert executed == [("news.lookup", {"query": "AI"})]
    assert jobs[0]["enabled"] is False
    assert jobs[0]["last_run"]
    persisted = data_store.load_json("cron_jobs", [])
    assert persisted and persisted[0]["job_id"] == job["job_id"]


def test_cron_service_daily_digest_default_job_uses_settings_time_and_email_delivery(tmp_path):
    data_store = LocalDataStore(base_dir=str(tmp_path))
    service = CronService(
        data_store=data_store,
        executor=lambda action_id, payload: {"ok": True},
        settings_repository=_SettingsRepo({
            "cron_engine": "poll",
            "assistant_daily_brief_time": "09:15",
        }),
    )

    service.ensure_default_jobs()

    jobs = service.list_jobs(include_disabled=True)
    daily_digest = next(job for job in jobs if job["name"] == "每日早报")

    assert daily_digest["action_id"] == "subagent.spawn"
    assert daily_digest["repeat"] == "daily"
    assert daily_digest["run_at"].endswith("09:15")
    assert daily_digest["payload"]["workflow"] == "weekly_tech_digest"
    assert daily_digest["payload"]["delivery_target"] == "email"
    assert daily_digest["payload"]["trigger"] == "cron_daily_brief"


def test_cron_service_updates_existing_daily_digest_default_job(tmp_path):
    data_store = LocalDataStore(base_dir=str(tmp_path))
    stale_job = {
        "job_id": "stale123",
        "name": "每日早报",
        "action_id": "news.briefing",
        "payload": {"topic": "AI OR 科技", "max_items": 10},
        "schedule": "0 8 * * *",
        "run_at": "",
        "repeat": "cron",
        "enabled": True,
        "last_run": "",
        "created_at": "2026-04-18 08:00:00",
        "next_run_at": "",
    }
    data_store.save_json("cron_jobs", [stale_job])

    service = CronService(
        data_store=data_store,
        executor=lambda action_id, payload: {"ok": True},
        settings_repository=_SettingsRepo({
            "cron_engine": "poll",
            "assistant_daily_brief_time": "07:45",
        }),
    )

    service.ensure_default_jobs()

    jobs = service.list_jobs(include_disabled=True)
    daily_digest = next(job for job in jobs if job["name"] == "每日早报")

    assert daily_digest["job_id"] == "stale123"
    assert daily_digest["action_id"] == "subagent.spawn"
    assert daily_digest["repeat"] == "daily"
    assert daily_digest["run_at"].endswith("07:45")
    assert daily_digest["payload"]["delivery_target"] == "email"


def test_cron_service_records_execution_history(tmp_path):
    data_store = LocalDataStore(base_dir=str(tmp_path))
    service = CronService(
        data_store=data_store,
        executor=lambda action_id, payload: {"output": "ok"},
        settings_repository=_SettingsRepo({"cron_engine": "poll"}),
    )
    run_at = (datetime.now() - timedelta(minutes=1)).strftime("%Y-%m-%d %H:%M")
    job = service.add_job("历史记录测试", "news.lookup", {}, run_at=run_at, repeat="none")

    service._tick()

    history = service.get_job_history(job["job_id"])
    assert len(history) == 1
    assert history[0]["success"] is True
    assert history[0]["output"] == "ok"
    assert history[0]["error"] is None


def test_cron_service_treats_success_with_warning_as_success(tmp_path):
    data_store = LocalDataStore(base_dir=str(tmp_path))
    service = CronService(
        data_store=data_store,
        executor=lambda action_id, payload: {"success": True, "error": "warning"},
        settings_repository=_SettingsRepo({"cron_engine": "poll"}),
    )
    run_at = (datetime.now() - timedelta(minutes=1)).strftime("%Y-%m-%d %H:%M")
    job = service.add_job("成功告警测试", "warning.action", {}, run_at=run_at, repeat="none")

    service._tick()

    history = service.get_job_history(job["job_id"])
    assert history[0]["success"] is True


def test_cron_service_does_not_bypass_queue_when_enqueue_fails(tmp_path):
    data_store = LocalDataStore(base_dir=str(tmp_path))
    executed = []

    class FailingQueue:
        def enqueue(self, **kwargs):
            raise RuntimeError("queue unavailable")

    service = CronService(
        data_store=data_store,
        executor=lambda action_id, payload: executed.append(action_id) or {"success": True},
        settings_repository=_SettingsRepo({"cron_engine": "poll"}),
    )
    service.set_task_queue(FailingQueue())
    job = service.add_job(
        "入队失败测试", "queue.action", {},
        run_at=(datetime.now() - timedelta(minutes=1)).strftime("%Y-%m-%d %H:%M"),
        repeat="none",
        max_retries=1,
        retry_backoff=1,
    )

    service._execute_job(next(item for item in service._jobs if item.job_id == job["job_id"]))

    history = service.get_job_history(job["job_id"])
    state = next(item for item in service.list_jobs(include_disabled=True) if item["job_id"] == job["job_id"])
    assert executed == []
    assert history[0]["success"] is False
    assert history[0]["error"] == "queue unavailable"
    assert state["retry_count"] == 1
    assert state["enabled"] is True


def test_cron_service_retries_failed_job_with_exponential_backoff(tmp_path):
    data_store = LocalDataStore(base_dir=str(tmp_path))
    attempts = []

    def failing_executor(action_id, payload):
        attempts.append(action_id)
        if len(attempts) < 3:
            return {"error": f"fail #{len(attempts)}"}
        return {"output": "recovered"}

    service = CronService(
        data_store=data_store,
        executor=failing_executor,
        settings_repository=_SettingsRepo({"cron_engine": "poll"}),
    )
    run_at = (datetime.now() - timedelta(minutes=1)).strftime("%Y-%m-%d %H:%M")
    job = service.add_job(
        "重试测试",
        "flaky.action",
        {},
        run_at=run_at,
        repeat="none",
        max_retries=3,
        retry_backoff=1,
    )

    # 第一次执行失败，安排重试
    service._tick()
    assert len(attempts) == 1
    job_state = next(j for j in service.list_jobs(include_disabled=True) if j["job_id"] == job["job_id"])
    assert job_state["retry_count"] == 1
    assert job_state["next_run_at"]

    # 模拟重试时间已到
    service._jobs[0].next_run_at = (datetime.now() - timedelta(seconds=1)).strftime("%Y-%m-%d %H:%M:%S")
    service._tick()
    assert len(attempts) == 2

    service._jobs[0].next_run_at = (datetime.now() - timedelta(seconds=1)).strftime("%Y-%m-%d %H:%M:%S")
    service._tick()
    assert len(attempts) == 3

    # 成功后状态恢复
    job_state = next(j for j in service.list_jobs(include_disabled=True) if j["job_id"] == job["job_id"])
    assert job_state["retry_count"] == 0
    assert job_state["enabled"] is False  # 一次性任务成功后禁用
    assert len(job_state["history"]) == 3
    assert job_state["history"][-1]["success"] is True


def test_cron_service_disables_job_when_retries_exhausted(tmp_path):
    data_store = LocalDataStore(base_dir=str(tmp_path))
    service = CronService(
        data_store=data_store,
        executor=lambda action_id, payload: {"error": "permanent failure"},
        settings_repository=_SettingsRepo({"cron_engine": "poll"}),
    )
    run_at = (datetime.now() - timedelta(minutes=1)).strftime("%Y-%m-%d %H:%M")
    job = service.add_job(
        "重试耗尽测试",
        "failing.action",
        {},
        run_at=run_at,
        repeat="none",
        max_retries=1,
        retry_backoff=1,
    )

    service._tick()
    service._jobs[0].next_run_at = (datetime.now() - timedelta(seconds=1)).strftime("%Y-%m-%d %H:%M:%S")
    service._tick()

    job_state = next(j for j in service.list_jobs(include_disabled=True) if j["job_id"] == job["job_id"])
    assert job_state["enabled"] is False
    assert job_state["retry_count"] == 0
    assert len(job_state["history"]) == 2
    assert all(not h["success"] for h in job_state["history"])


def test_cron_service_persists_retry_and_history_fields(tmp_path):
    data_store = LocalDataStore(base_dir=str(tmp_path))
    service = CronService(
        data_store=data_store,
        executor=lambda action_id, payload: {"error": "boom"},
        settings_repository=_SettingsRepo({"cron_engine": "poll"}),
    )
    run_at = (datetime.now() - timedelta(minutes=1)).strftime("%Y-%m-%d %H:%M")
    job = service.add_job(
        "持久化测试",
        "persist.action",
        {},
        run_at=run_at,
        repeat="none",
        max_retries=2,
        retry_backoff=30,
    )
    service._tick()

    service2 = CronService(
        data_store=data_store,
        executor=lambda action_id, payload: {"output": "ok"},
        settings_repository=_SettingsRepo({"cron_engine": "poll"}),
    )
    loaded = next(j for j in service2.list_jobs(include_disabled=True) if j["job_id"] == job["job_id"])
    assert loaded["max_retries"] == 2
    assert loaded["retry_backoff"] == 30
    assert loaded["retry_count"] == 1
    assert len(loaded["history"]) == 1
    assert loaded["history"][0]["success"] is False
