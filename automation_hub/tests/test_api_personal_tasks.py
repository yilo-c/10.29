import pytest
from api import db
from automation_hub.tests.support import bootstrap_headers, configure_test_env, make_client


def test_personal_tasks_list_returns_user_tasks(tmp_path, monkeypatch):
    configure_test_env(tmp_path, monkeypatch)
    client = make_client()
    headers = bootstrap_headers(client)

    token_payload = client.get("/auth/me", headers=headers).json()
    user_id = token_payload["user_id"]

    with db.get_db_connection() as conn:
        conn.execute(
            """INSERT INTO personal_tasks(
                   id, user_id, remote_id, name, note, is_completed, reminder_time,
                   linked_note_id, created_at, updated_at, deleted_at, version,
                   device_id, sync_state, last_synced_at
               ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                "task-1",
                user_id,
                "remote-task-1",
                "Smoke Remote Task",
                "remote task body",
                1,
                "2026-03-11T13:00:00+00:00",
                12,
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
            """INSERT INTO personal_tasks(
                   id, user_id, remote_id, name, note, is_completed, reminder_time,
                   linked_note_id, created_at, updated_at, deleted_at, version,
                   device_id, sync_state, last_synced_at
               ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                "task-2",
                "another-user",
                "remote-task-2",
                "Other Task",
                "ignore",
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

    response = client.get("/personal/tasks?limit=20", headers=headers)
    assert response.status_code == 200, response.text
    payload = response.json()

    assert payload["count"] == 1
    assert payload["next_cursor"] is None
    assert len(payload["task"]) == 1
    task = payload["task"][0]
    assert task["id"] == "task-1"
    assert task["remote_id"] == "remote-task-1"
    assert task["is_completed"] is True
    assert task["completed"] is True
    assert task["linked_note_id"] == 12


def test_personal_tasks_list_supports_since_updated_at_filter(tmp_path, monkeypatch):
    configure_test_env(tmp_path, monkeypatch)
    client = make_client()
    headers = bootstrap_headers(client)

    token_payload = client.get("/auth/me", headers=headers).json()
    user_id = token_payload["user_id"]

    with db.get_db_connection() as conn:
        conn.execute(
            """INSERT INTO personal_tasks(
                   id, user_id, name, note, is_completed, created_at, updated_at, version, sync_state
               ) VALUES(?,?,?,?,?,?,?,?,?)""",
            (
                "older-task",
                user_id,
                "Older Task",
                "old",
                0,
                "2026-03-11T08:00:00+00:00",
                "2026-03-11T08:30:00+00:00",
                1,
                "synced",
            ),
        )
        conn.execute(
            """INSERT INTO personal_tasks(
                   id, user_id, name, note, is_completed, created_at, updated_at, version, sync_state
               ) VALUES(?,?,?,?,?,?,?,?,?)""",
            (
                "newer-task",
                user_id,
                "Newer Task",
                "new",
                1,
                "2026-03-11T10:00:00+00:00",
                "2026-03-11T10:30:00+00:00",
                2,
                "dirty",
            ),
        )
        conn.commit()

    response = client.get(
        "/personal/tasks?since_updated_at=2026-03-11T09:00:00%2B00:00&limit=20",
        headers=headers,
    )
    assert response.status_code == 200, response.text
    payload = response.json()

    assert payload["count"] == 1
    assert payload["task"][0]["id"] == "newer-task"


def test_personal_tasks_create_and_update_round_trip(tmp_path, monkeypatch):
    configure_test_env(tmp_path, monkeypatch)
    client = make_client()
    headers = bootstrap_headers(client)

    create_response = client.post(
        "/personal/tasks",
        headers=headers,
        json={
            "id": 201,
            "name": "Desktop Task",
            "note": "draft task",
            "is_completed": False,
            "completed": False,
            "reminder_time": "2026-03-11T13:00:00+00:00",
            "linked_note_id": 7,
            "created_at": "2026-03-11T12:00:00+00:00",
            "updated_at": "2026-03-11T12:10:00+00:00",
            "version": 1,
            "device_id": "desktop-a",
            "sync_state": "local_only",
        },
    )
    assert create_response.status_code == 200, create_response.text
    created_task = create_response.json()["task"]
    assert created_task["remote_id"]
    assert created_task["name"] == "Desktop Task"
    assert created_task["is_completed"] is False
    assert created_task["linked_note_id"] == 7

    remote_id = created_task["remote_id"]
    update_response = client.put(
        f"/personal/tasks/{remote_id}",
        headers=headers,
        json={
            "remote_id": remote_id,
            "name": "Desktop Task Updated",
            "note": "updated task",
            "is_completed": True,
            "completed": True,
            "reminder_time": "2026-03-11T13:30:00+00:00",
            "linked_note_id": 8,
            "created_at": "2026-03-11T12:00:00+00:00",
            "updated_at": "2026-03-11T12:20:00+00:00",
            "version": 2,
            "device_id": "desktop-a",
            "sync_state": "dirty",
            "last_synced_at": "2026-03-11T12:21:00+00:00",
        },
    )
    assert update_response.status_code == 200, update_response.text
    updated_task = update_response.json()["task"]
    assert updated_task["remote_id"] == remote_id
    assert updated_task["name"] == "Desktop Task Updated"
    assert updated_task["version"] == 2
    assert updated_task["is_completed"] is True
    assert updated_task["completed"] is True
    assert updated_task["linked_note_id"] == 8

    list_response = client.get("/personal/tasks?limit=10", headers=headers)
    assert list_response.status_code == 200, list_response.text
    listed_tasks = list_response.json()["task"]
    assert len(listed_tasks) == 1
    assert listed_tasks[0]["remote_id"] == remote_id
    assert listed_tasks[0]["name"] == "Desktop Task Updated"

    delete_response = client.delete(f"/personal/tasks/{remote_id}", headers=headers)
    assert delete_response.status_code == 200, delete_response.text
    deleted_task = delete_response.json()["task"]
    assert deleted_task["remote_id"] == remote_id
    assert deleted_task["deleted_at"] is not None

    after_delete = client.get("/personal/tasks?limit=10", headers=headers)
    assert after_delete.status_code == 200, after_delete.text
    after_delete_tasks = after_delete.json()["task"]
    assert len(after_delete_tasks) == 1
    assert after_delete_tasks[0]["remote_id"] == remote_id
    assert after_delete_tasks[0]["deleted_at"] is not None


@pytest.fixture(autouse=True)
def _unlock_personal_mirror(monkeypatch):
    """V2-01 镜像锁默认 403 拒写；本文件验证的是写流程本身，
    按迁移期回退语义显式关锁（专测锁行为的 test_personal_mirror_lock.py 不受影响）。"""
    monkeypatch.setenv("SIYI_HUB_PERSONAL_MIRROR_LOCK", "0")
