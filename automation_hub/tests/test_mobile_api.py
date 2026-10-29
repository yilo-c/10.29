"""Mobile capture & dashboard API tests."""

from __future__ import annotations

from api import db
from automation_hub.tests.support import bootstrap_headers, configure_test_env, make_client


def test_mobile_capture_text_note(tmp_path, monkeypatch):
    """POST /mobile/capture with text should create a personal note."""
    configure_test_env(tmp_path, monkeypatch)
    client = make_client()
    headers = bootstrap_headers(client)

    resp = client.post("/mobile/capture", headers=headers, json={
        "capture_type": "text",
        "content": "Quick capture test content",
        "source": "phone",
    })
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["status"] == "ok"
    assert data["note_id"] != ""

    # Verify note was created
    token_payload = client.get("/auth/me", headers=headers).json()
    user_id = token_payload["user_id"]

    with db.get_db_connection() as conn:
        notes = conn.execute(
            "SELECT id, title, content FROM personal_notes WHERE user_id=? ORDER BY created_at DESC LIMIT 1",
            (user_id,),
        ).fetchall()

    assert len(notes) >= 1
    assert "Quick capture" in notes[0]["content"]


def test_mobile_capture_with_media_url(tmp_path, monkeypatch):
    """POST /mobile/capture with media_url should store it."""
    configure_test_env(tmp_path, monkeypatch)
    client = make_client()
    headers = bootstrap_headers(client)

    resp = client.post("/mobile/capture", headers=headers, json={
        "capture_type": "photo",
        "content": "Photo note",
        "media_url": "http://example.com/photo.jpg",
        "source": "phone",
    })
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "ok"


def test_mobile_capture_requires_note_write_scope(tmp_path, monkeypatch):
    """Capture should require note:write scope."""
    configure_test_env(tmp_path, monkeypatch)
    client = make_client()
    admin_headers = bootstrap_headers(client)

    # Create a token with note:read but not note:write
    device_resp = client.post("/auth/devices", headers=admin_headers,
                              json={"name": "test-device", "platform": "windows"})
    assert device_resp.status_code == 200
    device_id = device_resp.json()["id"]

    # Create a custom scoped token
    token_resp = client.post("/auth/tokens", headers=admin_headers,
                             json={"device_id": device_id, "scopes": ["note:read"]})
    assert token_resp.status_code == 200
    limited_headers = {"Authorization": f"Bearer {token_resp.json()['token']}"}

    resp = client.post("/mobile/capture", headers=limited_headers, json={
        "capture_type": "text",
        "content": "Should be blocked",
    })
    assert resp.status_code == 403, resp.text


def test_mobile_dashboard_returns_notes_and_tasks(tmp_path, monkeypatch):
    """GET /mobile/dashboard should return recent notes and active tasks."""
    configure_test_env(tmp_path, monkeypatch)
    client = make_client()
    headers = bootstrap_headers(client)

    token_payload = client.get("/auth/me", headers=headers).json()
    user_id = token_payload["user_id"]
    now = db.now_iso()

    # Insert a test note
    with db.get_db_connection() as conn:
        conn.execute(
            """INSERT INTO personal_notes(id, user_id, title, content, tags_json, folder_path, favorite, version, sync_state, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            ("dash-note-1", user_id, "Dashboard Note", "Dashboard content", '["test"]', "/", 0, 1, "synced", now, now),
        )
        conn.execute(
            """INSERT INTO personal_tasks(id, user_id, name, note, is_completed, version, sync_state, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            ("dash-task-1", user_id, "Dashboard Task", "", 0, 1, "synced", now, now),
        )
        conn.commit()

    resp = client.get("/mobile/dashboard", headers=headers)
    assert resp.status_code == 200, resp.text
    data = resp.json()

    assert len(data["recent_notes"]) >= 1
    assert data["recent_notes"][0]["title"] == "Dashboard Note"
    assert len(data["active_tasks"]) >= 1
    assert data["active_tasks"][0]["title"] == "Dashboard Task"


def test_mobile_dashboard_empty_for_new_user(tmp_path, monkeypatch):
    """GET /mobile/dashboard should return empty lists for a user with no data."""
    configure_test_env(tmp_path, monkeypatch)
    client = make_client()
    headers = bootstrap_headers(client)

    resp = client.get("/mobile/dashboard", headers=headers)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["recent_notes"] == []
    assert data["active_tasks"] == []
