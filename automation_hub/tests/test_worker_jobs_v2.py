from __future__ import annotations

from api import db
from automation_hub.api.approvals.service import create_approval
from automation_hub.api.audit.service import query_events
from automation_hub.api.db import create_tool_run, get_db_connection, get_tool_run_by_id
from automation_hub.tests.support import bootstrap_headers, configure_test_env, make_client, register_tool
from worker import jobs_v2


def _first_user_id() -> str:
    with get_db_connection() as conn:
        row = conn.execute("SELECT id FROM users ORDER BY created_at ASC LIMIT 1").fetchone()
        assert row is not None
        return row["id"]


def _create_run_record(
    run_id: str,
    tool_id: str,
    *,
    user_id: str,
    status: str = "queued",
    approval_request_id: str | None = None,
):
    ok = create_tool_run(
        run_id=run_id,
        tool_id=tool_id,
        args={},
        status=status,
        created_by_user_id=user_id,
        approval_request_id=approval_request_id,
        stdout_path=None,
        stderr_path=None,
    )
    assert ok is True


def test_run_tool_job_transitions_running_to_succeeded(tmp_path, monkeypatch):
    configure_test_env(tmp_path, monkeypatch)
    client = make_client()
    headers = bootstrap_headers(client)
    user_id = _first_user_id()
    register_tool(client, headers, "worker_ok", "read")

    run_id = "run-success"
    _create_run_record(run_id, "worker_ok", user_id=user_id)

    def fake_run(self, command, cwd, env, timeout, stdout_path, stderr_path):
        row = get_tool_run_by_id(run_id)
        assert row is not None
        assert row["status"] == "running"
        return 0

    monkeypatch.setattr(jobs_v2.HostExecutor, "run", fake_run)

    jobs_v2.run_tool_job(run_id=run_id, tool_id="worker_ok", args={}, user_id=user_id)

    row = get_tool_run_by_id(run_id)
    assert row is not None
    assert row["status"] == "succeeded"
    assert row["started_at"] is not None
    assert row["finished_at"] is not None
    assert row["exit_code"] == 0


def test_run_tool_job_marks_failed_on_nonzero_exit(tmp_path, monkeypatch):
    configure_test_env(tmp_path, monkeypatch)
    client = make_client()
    headers = bootstrap_headers(client)
    user_id = _first_user_id()
    register_tool(client, headers, "worker_fail", "read")

    run_id = "run-failed"
    _create_run_record(run_id, "worker_fail", user_id=user_id)

    def fake_run(self, command, cwd, env, timeout, stdout_path, stderr_path):
        return 7

    monkeypatch.setattr(jobs_v2.HostExecutor, "run", fake_run)

    jobs_v2.run_tool_job(run_id=run_id, tool_id="worker_fail", args={}, user_id=user_id)

    row = get_tool_run_by_id(run_id)
    assert row is not None
    assert row["status"] == "failed"
    assert row["started_at"] is not None
    assert row["finished_at"] is not None
    assert row["exit_code"] == 7

    events = query_events(limit=20, event_type="run.executed")
    target = next((event for event in events if event["resource_id"] == run_id and event["status"] == "fail"), None)
    assert target is not None
    assert float((target.get("meta") or {}).get("duration_sec") or 0) >= 0


def test_run_tool_job_marks_failed_on_executor_exception(tmp_path, monkeypatch):
    configure_test_env(tmp_path, monkeypatch)
    client = make_client()
    headers = bootstrap_headers(client)
    user_id = _first_user_id()
    register_tool(client, headers, "worker_error", "read")

    run_id = "run-exception"
    _create_run_record(run_id, "worker_error", user_id=user_id)

    def fake_run(self, command, cwd, env, timeout, stdout_path, stderr_path):
        raise RuntimeError("boom")

    monkeypatch.setattr(jobs_v2.HostExecutor, "run", fake_run)

    jobs_v2.run_tool_job(run_id=run_id, tool_id="worker_error", args={}, user_id=user_id)

    row = get_tool_run_by_id(run_id)
    assert row is not None
    assert row["status"] == "failed"
    assert row["finished_at"] is not None

    events = query_events(limit=20, event_type="run.failed")
    assert any(event["resource_id"] == run_id for event in events)


def test_run_tool_job_keeps_denied_runs_blocked(tmp_path, monkeypatch):
    configure_test_env(tmp_path, monkeypatch)
    client = make_client()
    headers = bootstrap_headers(client)
    user_id = _first_user_id()
    register_tool(client, headers, "worker_denied", "exec_high")

    approval_id = create_approval(
        user_id=user_id,
        device_id=None,
        resource_type="run",
        resource_id="run-denied",
        action="execute",
        risk_level="exec_high",
        reason="needs approval",
        payload={"tool_id": "worker_denied"},
    )
    with get_db_connection() as conn:
        conn.execute(
            "UPDATE approval_requests SET status='denied' WHERE id=?",
            (approval_id,),
        )
        conn.commit()

    _create_run_record("run-denied", "worker_denied", user_id=user_id, status="pending_approval", approval_request_id=approval_id)

    def fail_if_called(self, command, cwd, env, timeout, stdout_path, stderr_path):
        raise AssertionError("executor should not run for denied jobs")

    monkeypatch.setattr(jobs_v2.HostExecutor, "run", fail_if_called)

    jobs_v2.run_tool_job(run_id="run-denied", tool_id="worker_denied", args={}, user_id=user_id)

    row = get_tool_run_by_id("run-denied")
    assert row is not None
    assert row["status"] == "denied"
    assert row["started_at"] is None
    assert row["finished_at"] is None

    events = query_events(limit=20, event_type="run.blocked")
    assert any(event["resource_id"] == "run-denied" for event in events)


def test_arq_worker_settings_create(monkeypatch):
    """验证 ARQ Worker 参数能正确构建。"""
    from worker.arq_settings import build_worker_kwargs
    kwargs = build_worker_kwargs()
    assert len(kwargs) > 0
    assert len(kwargs["functions"]) > 0
    # 验证注册的函数名
    func_names = [f.__name__ for f in kwargs["functions"]]
    assert "arq_run_tool_job" in func_names
    assert kwargs["max_jobs"] == 10


def test_arq_worker_settings_custom_overrides(monkeypatch):
    """验证 ARQ Worker 参数支持覆盖默认值。"""
    from worker.arq_settings import build_worker_kwargs
    kwargs = build_worker_kwargs(max_jobs=5, poll_delay=1.0)
    assert kwargs["max_jobs"] == 5
    assert kwargs["poll_delay"] == 1.0