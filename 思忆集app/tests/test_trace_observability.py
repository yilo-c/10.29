# -*- coding: utf-8 -*-
"""P1-1/P1-2：TraceRecorder + trace/续跑 RPC + cron 观测接入测试。"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from services.trace_recorder import TraceRecorder
from core.events import AppEvent, event_bus


# ── TraceRecorder 核心 ──


def test_recorder_run_lifecycle_and_steps(tmp_path):
    rec = TraceRecorder(str(tmp_path / "trace.db"))
    try:
        rec.start_run("chat-s1", "chat_tool", title="测试任务", source="butler")
        rec.record_step("chat-s1", "tool_call", tool="web.search", summary="q=ai")
        rec.record_step("chat-s1", "tool_result", tool="web.search", summary="3 results")
        rec.finish_run("chat-s1", "completed")

        run = rec.get_run("chat-s1")
        assert run is not None
        assert run["status"] == "completed"
        assert run["task_type"] == "chat_tool"
        assert [s["step_no"] for s in run["steps"]] == [1, 2]
        assert run["steps"][0]["tool"] == "web.search"

        runs = rec.list_runs()
        assert len(runs) == 1 and runs[0]["task_id"] == "chat-s1"
        assert rec.list_runs(task_type="cron") == []
        assert rec.list_runs(status="failed") == []
    finally:
        rec.close()


def test_recorder_step_auto_registers_run(tmp_path):
    rec = TraceRecorder(str(tmp_path / "trace.db"))
    try:
        rec.record_step("adhoc-1", "step", summary="直接记步骤")
        run = rec.get_run("adhoc-1")
        assert run is not None and run["status"] == "running"
        assert len(run["steps"]) == 1
    finally:
        rec.close()


def test_recorder_stats_success_rate(tmp_path):
    rec = TraceRecorder(str(tmp_path / "trace.db"))
    try:
        for i in range(3):
            rec.start_run(f"cron-ok-{i}", "cron")
            rec.finish_run(f"cron-ok-{i}", "completed")
        rec.start_run("cron-bad", "cron")
        rec.finish_run("cron-bad", "failed", error="boom")

        stats = rec.get_stats(days=7)
        assert stats["total_runs"] == 4
        assert stats["by_type"]["cron"] == 4
        assert stats["by_status"]["completed"] == 3
        assert stats["success_rate"] == 0.75
    finally:
        rec.close()


def test_recorder_payload_convention(tmp_path):
    rec = TraceRecorder(str(tmp_path / "trace.db"))
    try:
        rec.record_from_payload(
            {
                "task_id": "chat-x",
                "task_type": "chat_tool",
                "title": "t",
                "source": "butler",
                "status": "started",
            }
        )
        rec.record_from_payload(
            {
                "task_id": "chat-x",
                "step_type": "tool_call",
                "tool": "notes.create",
                "summary": "ok",
            }
        )
        rec.record_from_payload({"task_id": "chat-x", "status": "failed", "error": "err"})

        run = rec.get_run("chat-x")
        assert run["status"] == "failed"
        assert run["error"] == "err"
        assert len(run["steps"]) == 1
    finally:
        rec.close()


def test_recorder_subscribes_exec_step_event(tmp_path):
    rec = TraceRecorder(str(tmp_path / "trace.db"))
    received_marker = []
    try:
        rec.subscribe(event_bus)
        event_bus.emit(
            AppEvent(
                "exec.step",
                payload={
                    "task_id": "chat-ev",
                    "task_type": "chat_tool",
                    "source": "butler",
                    "status": "started",
                },
                source="test",
            )
        )
        # EventBus emit 为同步分发时立即记录；若异步则给一次让出机会
        import time

        deadline = time.time() + 2
        while time.time() < deadline and rec.get_run("chat-ev") is None:
            time.sleep(0.02)
        run = rec.get_run("chat-ev")
        assert run is not None, "exec.step 事件应被 TraceRecorder 记录"
        received_marker.append(True)
    finally:
        event_bus.off("exec.step", rec.on_exec_step)
        rec.close()
    assert received_marker


# ── trace / 续跑 RPC ──


class FakeRegistry:
    def __init__(self):
        self.handlers = {}

    def register(self, name, fn):
        self.handlers[name] = fn


class FakeAutonomy:
    def __init__(self, unfinished=None, recover_result=None):
        self._unfinished = unfinished or []
        self._recover = recover_result
        self.recovered: list[str] = []
        self.task_manager = None

    def scan_unfinished_tasks(self):
        return list(self._unfinished)

    def recover_task(self, task_id, confirmed=True):
        self.recovered.append(task_id)
        return self._recover


class FakeContext:
    def __init__(self, recorder=None, autonomy=None):
        self.trace_recorder = recorder
        self.autonomous_assistant_manager = autonomy


@pytest.mark.asyncio
async def test_trace_rpc_list_get_stats(tmp_path):
    from backend.handlers._trace import register_trace

    rec = TraceRecorder(str(tmp_path / "trace.db"))
    registry = FakeRegistry()
    register_trace(registry, FakeContext(recorder=rec))
    try:
        rec.start_run("chat-rpc", "chat_tool", title="rpc")
        rec.finish_run("chat-rpc", "completed")

        listed = await registry.handlers["trace.list"]()
        assert listed["success"] and listed["count"] == 1

        got = await registry.handlers["trace.get"](task_id="chat-rpc")
        assert got["success"] and got["run"]["status"] == "completed"

        missing = await registry.handlers["trace.get"](task_id="nope")
        assert missing["success"] is False

        stats = await registry.handlers["trace.stats"](days=7)
        assert stats["success"] and stats["stats"]["total_runs"] == 1
    finally:
        rec.close()


@pytest.mark.asyncio
async def test_unfinished_and_resume_rpc():
    from backend.handlers._trace import register_trace

    autonomy = FakeAutonomy(
        unfinished=[{"task_id": "t9", "state": "interrupted"}],
    )
    registry = FakeRegistry()
    register_trace(registry, FakeContext(autonomy=autonomy))

    listed = await registry.handlers["assistant.unfinished_tasks"]()
    assert listed["success"] and listed["count"] == 1
    assert listed["tasks"][0]["task_id"] == "t9"

    # assistant.resume_task 已于 J 项退役，返回引导改用 chat.resume
    resumed = await registry.handlers["assistant.resume_task"](task_id="t9")
    assert resumed["success"] is False
    assert resumed["reason"] == "assistant_resume_retired"
    assert resumed.get("hint") == "use chat.resume"

    no_task = await registry.handlers["assistant.resume_task"](task_id="")
    assert no_task["success"] is False
    assert no_task["reason"] == "assistant_resume_retired"


# ── cron 观测接入 ──


class _SettingsRepo:
    def get_setting(self, key, default=None):
        return default


def test_cron_direct_execution_recorded_to_trace(tmp_path):
    from services.cron_service import CronService
    from data.local_storage import LocalDataStore

    rec = TraceRecorder(str(tmp_path / "trace.db"))
    try:
        service = CronService(
            data_store=LocalDataStore(base_dir=str(tmp_path)),
            executor=lambda action_id, payload: {"ok": True},
            settings_repository=_SettingsRepo(),
        )
        service.set_trace_recorder(rec)
        service.add_job("直连任务", "test.direct", {}, schedule="* * * * *", repeat="cron", max_retries=0)
        service._tick()

        runs = rec.list_runs(task_type="cron")
        assert len(runs) == 1, "直接执行的 cron 任务应记入统一观测"
        assert runs[0]["status"] == "completed"
        assert runs[0]["title"] == "直连任务"

        # 失败任务记 failed + error
        service2 = CronService(
            data_store=LocalDataStore(base_dir=str(tmp_path / "d2")),
            executor=lambda action_id, payload: {"error": "boom"},
            settings_repository=_SettingsRepo(),
        )
        service2.set_trace_recorder(rec)
        service2.add_job("失败任务", "test.fail", {}, schedule="* * * * *", repeat="cron", max_retries=0)
        with service2._lock:
            for job in service2._jobs:
                past = (datetime.now() - timedelta(minutes=2)).strftime("%Y-%m-%d %H:%M:%S")
                job.last_run = past
                job.next_run_at = past
        service2._tick()
        failed_runs = [r for r in rec.list_runs(task_type="cron") if r["status"] == "failed"]
        assert len(failed_runs) == 1
        assert "boom" in failed_runs[0]["error"]
    finally:
        rec.close()
