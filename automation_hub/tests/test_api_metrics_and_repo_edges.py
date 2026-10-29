from __future__ import annotations

import importlib

from automation_hub.api.approvals.service import create_approval
from automation_hub.api.audit.service import log_event
from automation_hub.api.db import create_run, create_script, create_tool_run, get_db_connection
from automation_hub.api.routes import metrics as metrics_module
from automation_hub.tests.support import bootstrap_headers, configure_test_env, make_client, register_tool

repos_router_module = importlib.import_module("api.repos.router")


def _first_user_id() -> str:
    with get_db_connection() as conn:
        row = conn.execute("SELECT id FROM users ORDER BY created_at ASC LIMIT 1").fetchone()
        assert row is not None
        return row["id"]


def test_metrics_reflect_current_counts_and_queue_failure(tmp_path, monkeypatch):
    configure_test_env(tmp_path, monkeypatch)
    client = make_client()
    headers = bootstrap_headers(client)
    user_id = _first_user_id()

    assert create_script("legacy_script", "legacy metrics test") is True
    assert create_run("legacy-run-1", "legacy_script", "{}") is True
    register_tool(client, headers, "tool_metrics", "read")
    assert create_tool_run("tool-run-1", "tool_metrics", {}, "queued", created_by_user_id=user_id) is True

    create_approval(
        user_id=user_id,
        device_id=None,
        resource_type="run",
        resource_id="tool-run-approval",
        action="execute",
        risk_level="write",
        reason="metrics pending approval",
        payload={"tool_id": "tool_metrics"},
    )
    log_event(
        event_type="metrics.test",
        action="observe",
        status="success",
        actor_user_id=user_id,
        resource_type="test",
        resource_id="metrics-1",
        message="metrics test event",
    )

    def fail_arq(*args, **kwargs):
        raise RuntimeError("redis unavailable")

    monkeypatch.setattr(metrics_module, "arq_queue_length", fail_arq)

    response = client.get("/metrics")
    assert response.status_code == 200, response.text
    text = response.text
    assert "automation_hub_runs_total 1.0" in text
    assert "automation_hub_run_status_total{status=\"queued\"} 1.0" in text
    assert "automation_hub_tool_runs_total 1.0" in text
    assert "automation_hub_tool_run_status_total{status=\"queued\"} 1.0" in text
    assert "automation_hub_approval_pending_total 1.0" in text
    assert "automation_hub_audit_events_24h_total 3.0" in text
    assert "automation_hub_queue_length -1.0" in text


def test_metrics_reports_average_run_durations(tmp_path, monkeypatch):
    configure_test_env(tmp_path, monkeypatch)
    client = make_client()
    headers = bootstrap_headers(client)
    user_id = _first_user_id()

    assert create_script("legacy_duration_script", "legacy duration test") is True
    assert create_run("legacy-duration-run", "legacy_duration_script", "{}") is True
    register_tool(client, headers, "tool_duration", "read")
    assert create_tool_run("tool-duration-run", "tool_duration", {}, "succeeded", created_by_user_id=user_id) is True

    with get_db_connection() as conn:
        conn.execute(
            "UPDATE runs SET status='completed', started_at=?, completed_at=? WHERE run_id=?",
            ("2026-03-10 10:00:00", "2026-03-10 10:00:05", "legacy-duration-run"),
        )
        conn.execute(
            "UPDATE tool_runs SET status='succeeded', started_at=?, finished_at=?, exit_code=0 WHERE id=?",
            ("2026-03-10 10:00:00", "2026-03-10 10:00:03", "tool-duration-run"),
        )
        conn.commit()

    monkeypatch.setattr(metrics_module, "arq_queue_length", lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("redis unavailable")))

    response = client.get("/metrics")
    assert response.status_code == 200, response.text
    text = response.text
    assert "automation_hub_run_duration_seconds_avg 5.0" in text
    assert "automation_hub_tool_run_duration_seconds_avg 3.0" in text


def test_metrics_refresh_does_not_keep_stale_status_labels(tmp_path, monkeypatch):
    configure_test_env(tmp_path, monkeypatch)
    client = make_client()
    headers = bootstrap_headers(client)
    user_id = _first_user_id()

    assert create_script("legacy_status_script", "legacy status test") is True
    assert create_run("legacy-status-run", "legacy_status_script", "{}") is True
    register_tool(client, headers, "tool_status", "read")
    assert create_tool_run("tool-status-run", "tool_status", {}, "queued", created_by_user_id=user_id) is True

    monkeypatch.setattr(metrics_module, "arq_queue_length", lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("redis unavailable")))

    first_response = client.get("/metrics")
    assert first_response.status_code == 200, first_response.text
    first_text = first_response.text
    assert "automation_hub_run_status_total{status=\"queued\"} 1.0" in first_text
    assert "automation_hub_tool_run_status_total{status=\"queued\"} 1.0" in first_text

    with get_db_connection() as conn:
        conn.execute("UPDATE runs SET status='completed' WHERE run_id=?", ("legacy-status-run",))
        conn.execute("UPDATE tool_runs SET status='failed' WHERE id=?", ("tool-status-run",))
        conn.commit()

    second_response = client.get("/metrics")
    assert second_response.status_code == 200, second_response.text
    second_text = second_response.text
    assert "automation_hub_run_status_total{status=\"completed\"} 1.0" in second_text
    assert "automation_hub_tool_run_status_total{status=\"failed\"} 1.0" in second_text
    assert "automation_hub_run_status_total{status=\"queued\"}" not in second_text
    assert "automation_hub_tool_run_status_total{status=\"queued\"}" not in second_text


def test_repo_search_python_fallback_honors_offset_and_limit(tmp_path, monkeypatch):
    env = configure_test_env(tmp_path, monkeypatch)
    client = make_client()
    headers = bootstrap_headers(client)

    repo_root = env["workspace_dir"] / "repo"
    repo_root.mkdir(parents=True, exist_ok=True)
    (repo_root / "notes.txt").write_text(
        "needle one\nneedle two\nneedle three\n",
        encoding="utf-8",
    )

    repo_response = client.post(
        "/repos",
        headers=headers,
        json={"name": "fallback-repo", "root_path": str(repo_root)},
    )
    assert repo_response.status_code == 200, repo_response.text
    repo_id = repo_response.json()["id"]

    def raise_missing_rg(*args, **kwargs):
        raise FileNotFoundError("rg missing")

    monkeypatch.setattr(repos_router_module.subprocess, "run", raise_missing_rg)

    search_response = client.get(f"/repos/{repo_id}/search?q=needle&offset=1&limit=2", headers=headers)
    assert search_response.status_code == 200, search_response.text
    payload = search_response.json()
    assert payload["engine"] == "python"
    assert payload["total"] == 3
    assert payload["offset"] == 1
    assert payload["limit"] == 2
    matches = payload["matches"]
    assert matches == [
        "notes.txt:2:needle two",
        "notes.txt:3:needle three",
    ]
    assert payload["items"] == [
        {"path": "notes.txt", "line": 2, "text": "needle two"},
        {"path": "notes.txt", "line": 3, "text": "needle three"},
    ]