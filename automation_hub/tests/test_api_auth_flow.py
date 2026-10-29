from __future__ import annotations

import json

from automation_hub.api.config import settings

from automation_hub.tests.support import bootstrap_headers, configure_test_env, make_client


def test_bootstrap_token_templates_and_token_rotation(tmp_path, monkeypatch):
    configure_test_env(tmp_path, monkeypatch)
    monkeypatch.setattr(settings, "TOKEN_DEFAULT_TTL_DAYS", 30)

    client = make_client()
    headers = bootstrap_headers(client)

    response = client.get("/auth/token-templates", headers=headers)
    assert response.status_code == 200, response.text
    template_names = {item["name"] for item in response.json()["templates"]}
    assert {"admin_ops", "agent", "desktop", "cronjob", "readonly"}.issubset(template_names)

    device_response = client.post(
        "/auth/devices",
        headers=headers,
        json={"name": "cron device", "platform": "windows"},
    )
    assert device_response.status_code == 200, device_response.text
    device_id = device_response.json()["id"]

    token_response = client.post(
        "/auth/tokens",
        headers=headers,
        json={"device_id": device_id, "template": "cronjob"},
    )
    assert token_response.status_code == 200, token_response.text
    created_token_id = token_response.json()["token_id"]

    list_response = client.get("/auth/tokens", headers=headers)
    assert list_response.status_code == 200, list_response.text
    tokens = list_response.json()["tokens"]
    created = next(item for item in tokens if item["id"] == created_token_id)
    created_scopes = set(created["scopes"])
    assert {"tool:execute", "tool:read"}.issubset(created_scopes)
    assert created["expires_at"] is not None

    rotate_response = client.post(
        f"/auth/tokens/{created_token_id}/rotate",
        headers=headers,
        json={"template": "readonly", "ttl_days": 7},
    )
    assert rotate_response.status_code == 200, rotate_response.text
    rotated_token_id = rotate_response.json()["token_id"]
    assert rotated_token_id != created_token_id

    list_response = client.get("/auth/tokens", headers=headers)
    rotated_tokens = list_response.json()["tokens"]
    old_token = next(item for item in rotated_tokens if item["id"] == created_token_id)
    new_token = next(item for item in rotated_tokens if item["id"] == rotated_token_id)
    assert old_token["revoked_at"] is not None
    readonly_required = {"tool:read", "approval:read", "audit:read", "repo:read", "proposal:read"}
    readonly_scopes = set(new_token["scopes"])
    assert readonly_required.issubset(readonly_scopes)
    assert "tool:execute" not in readonly_scopes


def test_readonly_token_blocked_from_write_endpoints(tmp_path, monkeypatch):
    configure_test_env(tmp_path, monkeypatch)
    client = make_client()
    admin_headers = bootstrap_headers(client)

    # Create a readonly token
    device_response = client.post("/auth/devices", headers=admin_headers, json={"name": "ro-device", "platform": "windows"})
    assert device_response.status_code == 200
    device_id = device_response.json()["id"]

    token_response = client.post("/auth/tokens", headers=admin_headers, json={"device_id": device_id, "template": "readonly"})
    assert token_response.status_code == 200
    raw_token = token_response.json()["token"]
    readonly_headers = {"Authorization": f"Bearer {raw_token}"}

    # GET on a read endpoint should work
    get_resp = client.get("/auth/tokens", headers=readonly_headers)
    assert get_resp.status_code == 200, get_resp.text

    # POST on a write endpoint should be blocked by scope middleware
    post_resp = client.post("/auth/devices", headers=readonly_headers, json={"name": "blocked", "platform": "windows"})
    assert post_resp.status_code == 403, post_resp.text
    assert "ReadOnly" in post_resp.text


def test_cronjob_token_blocked_from_non_tool_endpoints(tmp_path, monkeypatch):
    configure_test_env(tmp_path, monkeypatch)
    client = make_client()
    admin_headers = bootstrap_headers(client)

    # Create a cronjob token
    device_response = client.post("/auth/devices", headers=admin_headers, json={"name": "cron-device", "platform": "windows"})
    assert device_response.status_code == 200
    device_id = device_response.json()["id"]

    token_response = client.post("/auth/tokens", headers=admin_headers, json={"device_id": device_id, "template": "cronjob"})
    assert token_response.status_code == 200
    raw_token = token_response.json()["token"]
    cron_headers = {"Authorization": f"Bearer {raw_token}"}

    # Accessing a non-tool endpoint should be blocked
    resp = client.get("/auth/tokens", headers=cron_headers)
    assert resp.status_code == 403, resp.text
    assert "CronJob" in resp.text

    # Note: actual /tools/ endpoints require additional setup; testing
    # that the middleware correctly blocks non-tool paths is sufficient


def test_insufficient_scope_returns_403(tmp_path, monkeypatch):
    """Verify require_scope dependency correctly rejects insufficient scopes."""
    configure_test_env(tmp_path, monkeypatch)
    client = make_client()
    admin_headers = bootstrap_headers(client)

    # Create an agent token (limited scopes)
    device_response = client.post("/auth/devices", headers=admin_headers, json={"name": "agent-device", "platform": "windows"})
    assert device_response.status_code == 200
    device_id = device_response.json()["id"]

    token_response = client.post("/auth/tokens", headers=admin_headers, json={"device_id": device_id, "template": "agent"})
    assert token_response.status_code == 200
    raw_token = token_response.json()["token"]
    agent_headers = {"Authorization": f"Bearer {raw_token}"}

    # Agent token lacks user:admin scope, so creating tokens should fail
    resp = client.post("/auth/tokens", headers=agent_headers, json={"device_id": device_id, "template": "readonly"})
    assert resp.status_code == 403, resp.text
    assert "Insufficient scope" in resp.text

    # Agent token lacks audit:read scope, reading audit should fail
    resp = client.get("/audit", headers=agent_headers)
    assert resp.status_code == 403, resp.text
    assert "Insufficient scope" in resp.text


def test_revoked_token_returns_401(tmp_path, monkeypatch):
    """Verify revoked tokens are rejected."""
    configure_test_env(tmp_path, monkeypatch)
    client = make_client()
    admin_headers = bootstrap_headers(client)

    # Create then revoke a token
    device_response = client.post("/auth/devices", headers=admin_headers, json={"name": "rev-device", "platform": "windows"})
    assert device_response.status_code == 200
    device_id = device_response.json()["id"]

    token_response = client.post("/auth/tokens", headers=admin_headers, json={"device_id": device_id, "template": "readonly"})
    assert token_response.status_code == 200
    token_id = token_response.json()["token_id"]
    raw_token = token_response.json()["token"]
    revoked_headers = {"Authorization": f"Bearer {raw_token}"}

    # Revoke the token
    revoke_resp = client.delete(f"/auth/tokens/{token_id}", headers=admin_headers)
    assert revoke_resp.status_code == 200

    # Using revoked token should return 401
    resp = client.get("/auth/me", headers=revoked_headers)
    assert resp.status_code == 401, resp.text


def test_desktop_and_worker_use_same_scope_rules(tmp_path, monkeypatch):
    """M0-WP2: Verify desktop and worker (agent) templates use identical scope sets."""
    from api.auth.router import TOKEN_TEMPLATES

    desktop_scopes = set(TOKEN_TEMPLATES["desktop"])
    agent_scopes = set(TOKEN_TEMPLATES["agent"])
    assert desktop_scopes == agent_scopes, (
        f"desktop scopes {desktop_scopes} must match agent scopes {agent_scopes}"
    )

    # Verify both can execute tools but cannot write personal data
    assert "tool:execute" in desktop_scopes
    assert "tool:read" in desktop_scopes
    assert "agent:use" in desktop_scopes
    assert "note:write" not in desktop_scopes
    assert "task:write" not in desktop_scopes
    assert "user:admin" not in desktop_scopes


def test_desktop_token_created_with_agent_scopes(tmp_path, monkeypatch):
    """Verify creating a token with the 'desktop' template works end-to-end."""
    configure_test_env(tmp_path, monkeypatch)
    client = make_client()
    admin_headers = bootstrap_headers(client)

    device_response = client.post("/auth/devices", headers=admin_headers, json={"name": "desktop-device", "platform": "windows"})
    assert device_response.status_code == 200
    device_id = device_response.json()["id"]

    token_response = client.post("/auth/tokens", headers=admin_headers, json={"device_id": device_id, "template": "desktop"})
    assert token_response.status_code == 200, token_response.text
    raw_token = token_response.json()["token"]
    desktop_headers = {"Authorization": f"Bearer {raw_token}"}

    # Verify /auth/me works with desktop token
    me_resp = client.get("/auth/me", headers=desktop_headers)
    assert me_resp.status_code == 200, me_resp.text
    token_scopes = set(me_resp.json().get("scopes", []))
    assert "agent:use" in token_scopes
    assert "tool:execute" in token_scopes
    assert "tool:read" in token_scopes