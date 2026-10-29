import pytest
from api import db
from automation_hub.tests.support import bootstrap_headers, configure_test_env, make_client


def test_personal_translations_list_returns_user_records(tmp_path, monkeypatch):
    configure_test_env(tmp_path, monkeypatch)
    client = make_client()
    headers = bootstrap_headers(client)

    token_payload = client.get("/auth/me", headers=headers).json()
    user_id = token_payload["user_id"]

    with db.get_db_connection() as conn:
        conn.execute(
            """INSERT INTO personal_translation_history(
                   id, user_id, remote_id, history_key, source_text, translated_text,
                   from_lang, to_lang, raw_result_json, created_at, updated_at,
                   deleted_at, version, device_id, sync_state, last_synced_at
               ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                "translation-1",
                user_id,
                "remote-translation-1",
                "history-key-1",
                "hello",
                "你好",
                "en",
                "zh-CHS",
                '"你好"',
                "2026-03-11T12:00:00+00:00",
                "2026-03-11T12:30:00+00:00",
                None,
                2,
                "desktop-test",
                "synced",
                "2026-03-11T12:31:00+00:00",
            ),
        )
        conn.execute(
            "INSERT INTO users(id,name,created_at) VALUES(?,?,?)",
            ("another-user", "Other", db.now_iso()),
        )
        conn.execute(
            """INSERT INTO personal_translation_history(
                   id, user_id, source_text, translated_text, from_lang, to_lang,
                   created_at, updated_at, version, sync_state
               ) VALUES(?,?,?,?,?,?,?,?,?,?)""",
            (
                "translation-2",
                "another-user",
                "world",
                "世界",
                "en",
                "zh-CHS",
                "2026-03-11T10:00:00+00:00",
                "2026-03-11T10:30:00+00:00",
                1,
                "synced",
            ),
        )
        conn.commit()

    response = client.get("/personal/translations?limit=20", headers=headers)
    assert response.status_code == 200, response.text
    payload = response.json()

    assert payload["count"] == 1
    assert len(payload["records"]) == 1
    record = payload["records"][0]
    assert record["id"] == "translation-1"
    assert record["remote_id"] == "remote-translation-1"
    assert record["source_text"] == "hello"
    assert record["translated_text"] == "你好"


def test_personal_translations_create_update_delete_round_trip(tmp_path, monkeypatch):
    configure_test_env(tmp_path, monkeypatch)
    client = make_client()
    headers = bootstrap_headers(client)

    create_response = client.post(
        "/personal/translations",
        headers=headers,
        json={
            "id": 501,
            "history_key": "desktop-history-1",
            "source_text": "smoke hello",
            "translated_text": "烟雾你好",
            "from_lang": "en",
            "to_lang": "zh-CHS",
            "raw_result": "烟雾你好",
            "created_at": "2026-03-11T12:00:00+00:00",
            "updated_at": "2026-03-11T12:10:00+00:00",
            "version": 1,
            "device_id": "desktop-a",
            "sync_state": "local_only",
        },
    )
    assert create_response.status_code == 200, create_response.text
    created_record = create_response.json()["records"]
    assert created_record["remote_id"]
    assert created_record["source_text"] == "smoke hello"

    remote_id = created_record["remote_id"]
    update_response = client.put(
        f"/personal/translations/{remote_id}",
        headers=headers,
        json={
            "remote_id": remote_id,
            "history_key": "desktop-history-1",
            "source_text": "smoke hello",
            "translated_text": "烟雾你好 updated",
            "from_lang": "en",
            "to_lang": "zh-CHS",
            "raw_result": {"translation": ["烟雾你好 updated"]},
            "created_at": "2026-03-11T12:00:00+00:00",
            "updated_at": "2026-03-11T12:20:00+00:00",
            "version": 2,
            "device_id": "desktop-a",
            "sync_state": "dirty",
            "last_synced_at": "2026-03-11T12:21:00+00:00",
        },
    )
    assert update_response.status_code == 200, update_response.text
    updated_record = update_response.json()["records"]
    assert updated_record["remote_id"] == remote_id
    assert updated_record["translated_text"] == "烟雾你好 updated"
    assert updated_record["version"] == 2
    assert updated_record["raw_result"]["translation"] == ["烟雾你好 updated"]

    list_response = client.get("/personal/translations?limit=10", headers=headers)
    assert list_response.status_code == 200, list_response.text
    listed_records = list_response.json()["records"]
    assert len(listed_records) == 1
    assert listed_records[0]["remote_id"] == remote_id

    delete_response = client.delete(f"/personal/translations/{remote_id}", headers=headers)
    assert delete_response.status_code == 200, delete_response.text
    deleted_record = delete_response.json()["records"]
    assert deleted_record["remote_id"] == remote_id
    assert deleted_record["deleted_at"] is not None

    after_delete = client.get("/personal/translations?limit=10", headers=headers)
    assert after_delete.status_code == 200, after_delete.text
    after_delete_records = after_delete.json()["records"]
    assert len(after_delete_records) == 1
    assert after_delete_records[0]["remote_id"] == remote_id
    assert after_delete_records[0]["deleted_at"] is not None


@pytest.fixture(autouse=True)
def _unlock_personal_mirror(monkeypatch):
    """V2-01 镜像锁默认 403 拒写；本文件验证的是写流程本身，
    按迁移期回退语义显式关锁（专测锁行为的 test_personal_mirror_lock.py 不受影响）。"""
    monkeypatch.setenv("SIYI_HUB_PERSONAL_MIRROR_LOCK", "0")
