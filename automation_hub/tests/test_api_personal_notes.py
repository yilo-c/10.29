import pytest
from automation_hub.api import db
from automation_hub.tests.support import bootstrap_headers, configure_test_env, make_client


def test_personal_notes_list_returns_user_notes(tmp_path, monkeypatch):
    configure_test_env(tmp_path, monkeypatch)
    client = make_client()
    headers = bootstrap_headers(client)

    token_payload = client.get("/auth/me", headers=headers).json()
    user_id = token_payload["user_id"]

    with db.get_db_connection() as conn:
        conn.execute(
            """INSERT INTO personal_notes(
                   id, user_id, remote_id, title, content, tags_json, category, folder_path,
                   favorite, related_news_url, linked_task_id, created_at, updated_at,
                   deleted_at, version, device_id, sync_state, last_synced_at
               ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                "note-1",
                user_id,
                "remote-note-1",
                "Smoke Remote Note",
                "<p>remote body</p>",
                '["sync","remote"]',
                "笔记",
                "/",
                1,
                None,
                None,
                "2026-03-11T10:00:00+00:00",
                "2026-03-11T12:00:00+00:00",
                None,
                3,
                "desktop-test",
                "synced",
                "2026-03-11T12:05:00+00:00",
            ),
        )
        conn.execute(
            "INSERT INTO users(id,name,created_at) VALUES(?,?,?)",
            ("another-user", "Other", db.now_iso()),
        )
        conn.execute(
            """INSERT INTO personal_notes(
                   id, user_id, remote_id, title, content, tags_json, category, folder_path,
                   favorite, related_news_url, linked_task_id, created_at, updated_at,
                   deleted_at, version, device_id, sync_state, last_synced_at
               ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                "note-2",
                "another-user",
                "remote-note-2",
                "Other Note",
                "<p>ignore</p>",
                '[]',
                "笔记",
                "/",
                0,
                None,
                None,
                "2026-03-11T09:00:00+00:00",
                "2026-03-11T09:30:00+00:00",
                None,
                1,
                "desktop-other",
                "synced",
                "2026-03-11T09:31:00+00:00",
            ),
        )
        conn.commit()

    response = client.get("/personal/notes?limit=20", headers=headers)
    assert response.status_code == 200, response.text
    payload = response.json()

    assert payload["count"] == 1
    assert payload["next_cursor"] is None
    assert len(payload["note"]) == 1
    note = payload["note"][0]
    assert note["id"] == "note-1"
    assert note["remote_id"] == "remote-note-1"
    assert note["tags"] == ["sync", "remote"]
    assert note["favorite"] is True
    assert note["sync_state"] == "synced"


def test_personal_notes_list_supports_since_updated_at_filter(tmp_path, monkeypatch):
    configure_test_env(tmp_path, monkeypatch)
    client = make_client()
    headers = bootstrap_headers(client)

    token_payload = client.get("/auth/me", headers=headers).json()
    user_id = token_payload["user_id"]

    with db.get_db_connection() as conn:
        conn.execute(
            """INSERT INTO personal_notes(
                   id, user_id, title, content, tags_json, folder_path, favorite,
                   created_at, updated_at, version, sync_state
               ) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
            (
                "older-note",
                user_id,
                "Older",
                "old",
                '[]',
                "/",
                0,
                "2026-03-11T08:00:00+00:00",
                "2026-03-11T08:30:00+00:00",
                1,
                "synced",
            ),
        )
        conn.execute(
            """INSERT INTO personal_notes(
                   id, user_id, title, content, tags_json, folder_path, favorite,
                   created_at, updated_at, version, sync_state
               ) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
            (
                "newer-note",
                user_id,
                "Newer",
                "new",
                '[]',
                "/",
                0,
                "2026-03-11T10:00:00+00:00",
                "2026-03-11T10:30:00+00:00",
                2,
                "dirty",
            ),
        )
        conn.commit()

    response = client.get(
        "/personal/notes?since_updated_at=2026-03-11T09:00:00%2B00:00&limit=20",
        headers=headers,
    )
    assert response.status_code == 200, response.text
    payload = response.json()

    assert payload["count"] == 1
    assert payload["note"][0]["id"] == "newer-note"


def test_personal_notes_create_and_update_round_trip(tmp_path, monkeypatch):
    configure_test_env(tmp_path, monkeypatch)
    client = make_client()
    headers = bootstrap_headers(client)

    create_response = client.post(
        "/personal/notes",
        headers=headers,
        json={
            "id": 101,
            "title": "Desktop Draft",
            "content": "<p>draft</p>",
            "tags": ["desktop", "draft"],
            "category": "笔记",
            "folder_path": "/sync",
            "favorite": True,
            "created_at": "2026-03-11T12:00:00+00:00",
            "updated_at": "2026-03-11T12:10:00+00:00",
            "version": 1,
            "device_id": "desktop-a",
            "sync_state": "local_only",
        },
    )
    assert create_response.status_code == 200, create_response.text
    created_note = create_response.json()["note"]
    assert created_note["remote_id"]
    assert created_note["title"] == "Desktop Draft"
    assert created_note["tags"] == ["desktop", "draft"]
    assert created_note["favorite"] is True

    remote_id = created_note["remote_id"]
    update_response = client.put(
        f"/personal/notes/{remote_id}",
        headers=headers,
        json={
            "remote_id": remote_id,
            "title": "Desktop Draft Updated",
            "content": "<p>updated</p>",
            "tags": ["desktop", "synced"],
            "category": "笔记",
            "folder_path": "/sync",
            "favorite": False,
            "created_at": "2026-03-11T12:00:00+00:00",
            "updated_at": "2026-03-11T12:20:00+00:00",
            "version": 2,
            "device_id": "desktop-a",
            "sync_state": "dirty",
            "last_synced_at": "2026-03-11T12:21:00+00:00",
        },
    )
    assert update_response.status_code == 200, update_response.text
    updated_note = update_response.json()["note"]
    assert updated_note["remote_id"] == remote_id
    assert updated_note["title"] == "Desktop Draft Updated"
    assert updated_note["version"] == 2
    assert updated_note["favorite"] is False

    list_response = client.get("/personal/notes?limit=10", headers=headers)
    assert list_response.status_code == 200, list_response.text
    listed_notes = list_response.json()["note"]
    assert len(listed_notes) == 1
    assert listed_notes[0]["remote_id"] == remote_id
    assert listed_notes[0]["title"] == "Desktop Draft Updated"

    delete_response = client.delete(f"/personal/notes/{remote_id}", headers=headers)
    assert delete_response.status_code == 200, delete_response.text
    deleted_note = delete_response.json()["note"]
    assert deleted_note["remote_id"] == remote_id
    assert deleted_note["deleted_at"] is not None

    after_delete = client.get("/personal/notes?limit=10", headers=headers)
    assert after_delete.status_code == 200, after_delete.text
    after_delete_notes = after_delete.json()["note"]
    assert len(after_delete_notes) == 1
    assert after_delete_notes[0]["remote_id"] == remote_id
    assert after_delete_notes[0]["deleted_at"] is not None


@pytest.fixture(autouse=True)
def _unlock_personal_mirror(monkeypatch):
    """V2-01 镜像锁默认 403 拒写；本文件验证的是写流程本身，
    按迁移期回退语义显式关锁（专测锁行为的 test_personal_mirror_lock.py 不受影响）。"""
    monkeypatch.setenv("SIYI_HUB_PERSONAL_MIRROR_LOCK", "0")
