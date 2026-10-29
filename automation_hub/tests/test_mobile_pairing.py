"""Mobile pairing flow tests: initiate → complete via token/PIN → status."""

from __future__ import annotations

from api import db
from automation_hub.tests.support import bootstrap_headers, configure_test_env, make_client


def test_pairing_initiate_returns_pin_and_token(tmp_path, monkeypatch):
    configure_test_env(tmp_path, monkeypatch)
    client = make_client()
    headers = bootstrap_headers(client)

    resp = client.post("/mobile/pairing/initiate", headers=headers)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert "setup_token" in data
    assert "pin_code" in data
    assert len(data["pin_code"]) == 6
    assert data["pin_code"].isdigit()
    assert data["expires_in_seconds"] == 300


def test_pairing_complete_with_setup_token(tmp_path, monkeypatch):
    configure_test_env(tmp_path, monkeypatch)
    client = make_client()
    headers = bootstrap_headers(client)

    init = client.post("/mobile/pairing/initiate", headers=headers).json()
    setup_token = init["setup_token"]

    resp = client.post("/mobile/pairing/complete", json={
        "setup_token": setup_token,
        "device_name": "Test Phone",
        "platform": "android",
    })
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert "api_token" in data
    assert "device_id" in data
    assert len(data["api_token"]) > 16
    assert "scopes" in data
    assert "note:read" in data["scopes"]
    assert "device:manage" in data["scopes"]
    # Should NOT have admin scopes
    assert "user:admin" not in data["scopes"]
    assert "agent:use" not in data["scopes"]


def test_pairing_complete_with_pin(tmp_path, monkeypatch):
    configure_test_env(tmp_path, monkeypatch)
    client = make_client()
    headers = bootstrap_headers(client)

    init = client.post("/mobile/pairing/initiate", headers=headers).json()
    pin_code = init["pin_code"]

    resp = client.post("/mobile/pairing/complete", json={
        "pin_code": pin_code,
        "device_name": "PIN Phone",
        "platform": "android",
    })
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert "api_token" in data
    assert "device_id" in data


def test_pairing_complete_invalid_token(tmp_path, monkeypatch):
    configure_test_env(tmp_path, monkeypatch)
    client = make_client()

    resp = client.post("/mobile/pairing/complete", json={
        "setup_token": "invalid-token",
    })
    assert resp.status_code == 400


def test_pairing_complete_invalid_pin(tmp_path, monkeypatch):
    configure_test_env(tmp_path, monkeypatch)
    client = make_client()

    resp = client.post("/mobile/pairing/complete", json={
        "pin_code": "000000",
    })
    assert resp.status_code == 400


def test_pairing_status_after_complete(tmp_path, monkeypatch):
    configure_test_env(tmp_path, monkeypatch)
    client = make_client()
    headers = bootstrap_headers(client)

    init = client.post("/mobile/pairing/initiate", headers=headers).json()

    client.post("/mobile/pairing/complete", json={
        "setup_token": init["setup_token"],
        "device_name": "Paired Phone",
        "platform": "ios",
    })

    resp = client.get("/mobile/pairing/status", headers=headers)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["is_paired"] is True
    assert len(data["paired_devices"]) >= 1
    assert data["paired_devices"][0]["name"] == "Paired Phone"


def test_pairing_initiate_requires_device_manage_scope(tmp_path, monkeypatch):
    """A token without device:manage scope should be rejected."""
    configure_test_env(tmp_path, monkeypatch)
    client = make_client()
    admin_headers = bootstrap_headers(client)

    # Create a readonly token (lacks device:manage)
    device_resp = client.post("/auth/devices", headers=admin_headers,
                              json={"name": "ro-device", "platform": "windows"})
    assert device_resp.status_code == 200
    device_id = device_resp.json()["id"]

    token_resp = client.post("/auth/tokens", headers=admin_headers,
                             json={"device_id": device_id, "template": "readonly"})
    assert token_resp.status_code == 200
    readonly_headers = {"Authorization": f"Bearer {token_resp.json()['token']}"}

    resp = client.post("/mobile/pairing/initiate", headers=readonly_headers)
    assert resp.status_code == 403, resp.text


def test_pairing_token_expires(tmp_path, monkeypatch):
    """Setup token expires after TTL."""
    configure_test_env(tmp_path, monkeypatch)
    client = make_client()
    headers = bootstrap_headers(client)

    init = client.post("/mobile/pairing/initiate", headers=headers).json()
    setup_token = init["setup_token"]

    # Manually expire the token by manipulating the in-memory store
    from api.routes.mobile import _setup_tokens
    if setup_token in _setup_tokens:
        import time
        _setup_tokens[setup_token]["expires_at"] = time.time() - 1

    resp = client.post("/mobile/pairing/complete", json={
        "setup_token": setup_token,
    })
    assert resp.status_code == 400
