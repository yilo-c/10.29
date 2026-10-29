# -*- coding: utf-8 -*-
"""Tests for scope enforcement on API endpoints (Layer 4).

Covers scope enforcement on /scripts/ and /tasks/ routes that were
previously unprotected, plus existing enforcement on other routes.
"""

from __future__ import annotations

from automation_hub.tests.support import bootstrap_headers, configure_test_env, make_client


def test_scripts_get_requires_tool_read_scope(tmp_path, monkeypatch):
    """/scripts endpoint should require tool:read scope."""
    configure_test_env(tmp_path, monkeypatch)
    client = make_client()
    admin_headers = bootstrap_headers(client)

    # Create a readonly token (has tool:read)
    device_resp = client.post("/auth/devices", headers=admin_headers, json={"name": "ro-device", "platform": "windows"})
    assert device_resp.status_code == 200
    device_id = device_resp.json()["id"]

    token_resp = client.post("/auth/tokens", headers=admin_headers, json={"device_id": device_id, "template": "readonly"})
    assert token_resp.status_code == 200
    readonly_headers = {"Authorization": f"Bearer {token_resp.json()['token']}"}

    # readonly token should access GET /scripts (has tool:read)
    resp = client.get("/scripts", headers=readonly_headers)
    assert resp.status_code == 200, resp.text

    # readonly token should be blocked from POST /scripts (needs tool:write)
    resp = client.post("/scripts", headers=readonly_headers, json={"name": "test.py", "description": "test"})
    assert resp.status_code == 403, resp.text


def test_tasks_get_requires_task_read_scope(tmp_path, monkeypatch):
    """/tasks endpoint should require task:read scope."""
    configure_test_env(tmp_path, monkeypatch)
    client = make_client()
    admin_headers = bootstrap_headers(client)

    # Create an agent token (no task:read scope)
    device_resp = client.post("/auth/devices", headers=admin_headers, json={"name": "agent-device", "platform": "windows"})
    assert device_resp.status_code == 200
    device_id = device_resp.json()["id"]

    token_resp = client.post("/auth/tokens", headers=admin_headers, json={"device_id": device_id, "template": "agent"})
    assert token_resp.status_code == 200
    agent_headers = {"Authorization": f"Bearer {token_resp.json()['token']}"}

    # Agent token lacks task:read — should be blocked
    resp = client.get("/tasks", headers=agent_headers)
    assert resp.status_code == 403, resp.text

    # Admin token should work
    resp = client.get("/tasks", headers=admin_headers)
    assert resp.status_code == 200, resp.text


def test_tasks_post_requires_task_write_scope(tmp_path, monkeypatch):
    """POST /tasks should require task:write scope."""
    configure_test_env(tmp_path, monkeypatch)
    client = make_client()
    admin_headers = bootstrap_headers(client)

    # Create a readonly token (no task:write)
    device_resp = client.post("/auth/devices", headers=admin_headers, json={"name": "ro-device", "platform": "windows"})
    assert device_resp.status_code == 200
    device_id = device_resp.json()["id"]

    token_resp = client.post("/auth/tokens", headers=admin_headers, json={"device_id": device_id, "template": "readonly"})
    assert token_resp.status_code == 200
    readonly_headers = {"Authorization": f"Bearer {token_resp.json()['token']}"}

    # readonly token should be blocked from creating tasks
    resp = client.post("/tasks", headers=readonly_headers, json={
        "name": "test task",
        "script_name": "test.py",
        "parameters": {},
        "status": "pending",
        "scheduled_time": None,
        "completed_at": None,
    })
    assert resp.status_code == 403, resp.text


def test_tasks_delete_requires_task_write_scope(tmp_path, monkeypatch):
    """DELETE /tasks/{id} should require task:write scope."""
    configure_test_env(tmp_path, monkeypatch)
    client = make_client()
    admin_headers = bootstrap_headers(client)

    # Create a task first using admin token (parameters must be a string in Task model)
    create_resp = client.post("/tasks", headers=admin_headers, json={
        "name": "delete-test",
        "script_name": "test.py",
        "parameters": "{}",
        "status": "pending",
    })
    assert create_resp.status_code == 200, create_resp.text
    task_id = create_resp.json()["id"]

    # readonly token should be blocked from deleting
    device_resp = client.post("/auth/devices", headers=admin_headers, json={"name": "ro-device2", "platform": "windows"})
    assert device_resp.status_code == 200
    device_id = device_resp.json()["id"]

    token_resp = client.post("/auth/tokens", headers=admin_headers, json={"device_id": device_id, "template": "readonly"})
    assert token_resp.status_code == 200
    readonly_headers = {"Authorization": f"Bearer {token_resp.json()['token']}"}

    resp = client.delete(f"/tasks/{task_id}", headers=readonly_headers)
    assert resp.status_code == 403, resp.text


def test_all_personal_routes_have_scope_enforcement(tmp_path, monkeypatch):
    """Verify personal data routes have proper scope guards."""
    configure_test_env(tmp_path, monkeypatch)
    client = make_client()
    admin_headers = bootstrap_headers(client)

    # Create agent token (limited scopes)
    device_resp = client.post("/auth/devices", headers=admin_headers, json={"name": "agent-dev", "platform": "windows"})
    assert device_resp.status_code == 200
    device_id = device_resp.json()["id"]

    token_resp = client.post("/auth/tokens", headers=admin_headers, json={"device_id": device_id, "template": "agent"})
    assert token_resp.status_code == 200
    agent_headers = {"Authorization": f"Bearer {token_resp.json()['token']}"}

    # Agent token lacks task:read scope
    agent_has_no_scope_routes = [
        ("GET", "/personal/notes"),
        ("POST", "/personal/notes"),
        ("GET", "/personal/tasks"),
        ("POST", "/personal/tasks"),
    ]
    for method, path in agent_has_no_scope_routes:
        if method == "GET":
            resp = client.get(path, headers=agent_headers)
        elif method == "POST":
            resp = client.post(path, headers=agent_headers, json={"test": "data"})
        else:
            continue
        assert resp.status_code == 403, f"{method} {path} expected 403, got {resp.status_code}"


def test_no_auth_tool_routes_are_unprotected(tmp_path, monkeypatch):
    """Critical routes like /tools, /tool-runs, /approvals should all require auth."""
    configure_test_env(tmp_path, monkeypatch)
    client = make_client()

    # No auth token — should get 401 or 403
    protected_routes = [
        ("GET", "/tools"),
        ("GET", "/tool-runs"),
        ("GET", "/approvals"),
        ("GET", "/audit"),
        ("GET", "/proposals"),
        ("GET", "/repos"),
        ("GET", "/agent-nodes/"),  # trailing slash needed to avoid redirect
    ]
    for method, path in protected_routes:
        resp = client.request(method, path)
        assert resp.status_code in (401, 403), f"{method} {path} expected 401/403, got {resp.status_code}"
