import pytest
from api import db
from automation_hub.tests.support import bootstrap_headers, configure_test_env, make_client


def test_personal_wordbooks_round_trip(tmp_path, monkeypatch):
    configure_test_env(tmp_path, monkeypatch)
    client = make_client()
    headers = bootstrap_headers(client)

    create_response = client.post(
        "/personal/wordbooks",
        headers=headers,
        json={
            "id": 801,
            "book_key": "desktop-book-1",
            "name": "Smoke Book",
            "description": "desktop test book",
            "created_at": "2026-03-12T10:00:00+00:00",
            "updated_at": "2026-03-12T10:10:00+00:00",
            "version": 1,
            "device_id": "desktop-a",
            "sync_state": "local_only",
        },
    )
    assert create_response.status_code == 200, create_response.text
    created_wordbook = create_response.json()["wordbooks"]
    assert created_wordbook["remote_id"]
    remote_id = created_wordbook["remote_id"]

    update_response = client.put(
        f"/personal/wordbooks/{remote_id}",
        headers=headers,
        json={
            "remote_id": remote_id,
            "book_key": "desktop-book-1",
            "name": "Smoke Book Updated",
            "description": "updated book",
            "created_at": "2026-03-12T10:00:00+00:00",
            "updated_at": "2026-03-12T10:20:00+00:00",
            "version": 2,
            "device_id": "desktop-a",
            "sync_state": "dirty",
            "last_synced_at": "2026-03-12T10:21:00+00:00",
        },
    )
    assert update_response.status_code == 200, update_response.text
    updated_wordbook = update_response.json()["wordbooks"]
    assert updated_wordbook["name"] == "Smoke Book Updated"

    list_response = client.get("/personal/wordbooks?limit=10", headers=headers)
    assert list_response.status_code == 200, list_response.text
    assert len(list_response.json()["wordbooks"]) == 1

    delete_response = client.delete(f"/personal/wordbooks/{remote_id}", headers=headers)
    assert delete_response.status_code == 200, delete_response.text
    assert delete_response.json()["wordbooks"]["deleted_at"] is not None


def test_personal_words_round_trip(tmp_path, monkeypatch):
    configure_test_env(tmp_path, monkeypatch)
    client = make_client()
    headers = bootstrap_headers(client)

    token_payload = client.get("/auth/me", headers=headers).json()
    user_id = token_payload["user_id"]
    with db.get_db_connection() as conn:
        conn.execute(
            """INSERT INTO personal_wordbooks(
                   id, user_id, remote_id, book_key, name, description, created_at, updated_at,
                   deleted_at, version, device_id, sync_state, last_synced_at
               ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                "book-row-1",
                user_id,
                "remote-book-1",
                "book-key-1",
                "Smoke Book",
                "book desc",
                "2026-03-12T10:00:00+00:00",
                "2026-03-12T10:05:00+00:00",
                None,
                1,
                "desktop-a",
                "synced",
                "2026-03-12T10:06:00+00:00",
            ),
        )
        conn.commit()

    create_response = client.post(
        "/personal/words",
        headers=headers,
        json={
            "id": 901,
            "word_key": "desktop-word-1",
            "word": "smoke",
            "phonetic": "/smoke/",
            "meaning": "冒烟验证",
            "example": "Smoke checks should stay fast.",
            "wordbook_remote_id": "remote-book-1",
            "wordbook_name": "Smoke Book",
            "wordbook_key": "book-key-1",
            "created_at": "2026-03-12T10:10:00+00:00",
            "updated_at": "2026-03-12T10:15:00+00:00",
            "version": 1,
            "device_id": "desktop-a",
            "sync_state": "local_only",
        },
    )
    assert create_response.status_code == 200, create_response.text
    created_word = create_response.json()["words"]
    assert created_word["remote_id"]
    remote_id = created_word["remote_id"]

    update_response = client.put(
        f"/personal/words/{remote_id}",
        headers=headers,
        json={
            "remote_id": remote_id,
            "word_key": "desktop-word-1",
            "word": "smoke",
            "phonetic": "/smoke/",
            "meaning": "冒烟验证 updated",
            "example": "Smoke checks should stay focused.",
            "wordbook_remote_id": "remote-book-1",
            "wordbook_name": "Smoke Book",
            "wordbook_key": "book-key-1",
            "created_at": "2026-03-12T10:10:00+00:00",
            "updated_at": "2026-03-12T10:20:00+00:00",
            "version": 2,
            "device_id": "desktop-a",
            "sync_state": "dirty",
            "last_synced_at": "2026-03-12T10:21:00+00:00",
        },
    )
    assert update_response.status_code == 200, update_response.text
    updated_word = update_response.json()["words"]
    assert updated_word["meaning"] == "冒烟验证 updated"

    list_response = client.get("/personal/words?limit=10", headers=headers)
    assert list_response.status_code == 200, list_response.text
    assert len(list_response.json()["words"]) == 1

    delete_response = client.delete(f"/personal/words/{remote_id}", headers=headers)
    assert delete_response.status_code == 200, delete_response.text
    assert delete_response.json()["words"]["deleted_at"] is not None


@pytest.fixture(autouse=True)
def _unlock_personal_mirror(monkeypatch):
    """V2-01 镜像锁默认 403 拒写；本文件验证的是写流程本身，
    按迁移期回退语义显式关锁（专测锁行为的 test_personal_mirror_lock.py 不受影响）。"""
    monkeypatch.setenv("SIYI_HUB_PERSONAL_MIRROR_LOCK", "0")
