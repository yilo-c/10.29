import pytest
from api import db
from automation_hub.tests.support import bootstrap_headers, configure_test_env, make_client


def test_personal_news_list_returns_user_items(tmp_path, monkeypatch):
    configure_test_env(tmp_path, monkeypatch)
    client = make_client()
    headers = bootstrap_headers(client)

    token_payload = client.get("/auth/me", headers=headers).json()
    user_id = token_payload["user_id"]

    with db.get_db_connection() as conn:
        conn.execute(
            """INSERT INTO personal_news_items(
                   id, user_id, remote_id, item_key, title, url, summary, content,
                   source, category, published_at, image_url, is_favorite, created_at,
                   updated_at, deleted_at, version, device_id, sync_state, last_synced_at
               ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                "news-1",
                user_id,
                "remote-news-1",
                "https://example.com/news-1",
                "Smoke News",
                "https://example.com/news-1",
                "summary",
                "content",
                "Smoke Source",
                "科技",
                "2026-03-11T12:00:00+00:00",
                "https://example.com/news-1.png",
                1,
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
            """INSERT INTO personal_news_items(
                   id, user_id, title, is_favorite, created_at, updated_at, version, sync_state
               ) VALUES(?,?,?,?,?,?,?,?)""",
            (
                "news-2",
                "another-user",
                "Other News",
                1,
                "2026-03-11T10:00:00+00:00",
                "2026-03-11T10:30:00+00:00",
                1,
                "synced",
            ),
        )
        conn.commit()

    response = client.get("/personal/news?limit=20", headers=headers)
    assert response.status_code == 200, response.text
    payload = response.json()

    assert payload["count"] == 1
    assert payload["next_cursor"] is None
    assert len(payload["items"]) == 1
    item = payload["items"][0]
    assert item["id"] == "news-1"
    assert item["remote_id"] == "remote-news-1"
    assert item["key"] == "https://example.com/news-1"
    assert item["is_favorite"] is True


def test_personal_news_list_supports_since_updated_at_filter(tmp_path, monkeypatch):
    configure_test_env(tmp_path, monkeypatch)
    client = make_client()
    headers = bootstrap_headers(client)

    token_payload = client.get("/auth/me", headers=headers).json()
    user_id = token_payload["user_id"]

    with db.get_db_connection() as conn:
        conn.execute(
            """INSERT INTO personal_news_items(
                   id, user_id, title, is_favorite, created_at, updated_at, version, sync_state
               ) VALUES(?,?,?,?,?,?,?,?)""",
            (
                "older-news",
                user_id,
                "Older News",
                1,
                "2026-03-11T08:00:00+00:00",
                "2026-03-11T08:30:00+00:00",
                1,
                "synced",
            ),
        )
        conn.execute(
            """INSERT INTO personal_news_items(
                   id, user_id, title, is_favorite, created_at, updated_at, version, sync_state
               ) VALUES(?,?,?,?,?,?,?,?)""",
            (
                "newer-news",
                user_id,
                "Newer News",
                1,
                "2026-03-11T10:00:00+00:00",
                "2026-03-11T10:30:00+00:00",
                2,
                "dirty",
            ),
        )
        conn.commit()

    response = client.get(
        "/personal/news?since_updated_at=2026-03-11T09:00:00%2B00:00&limit=20",
        headers=headers,
    )
    assert response.status_code == 200, response.text
    payload = response.json()

    assert payload["count"] == 1
    assert payload["items"][0]["id"] == "newer-news"


def test_personal_news_create_update_delete_round_trip(tmp_path, monkeypatch):
    configure_test_env(tmp_path, monkeypatch)
    client = make_client()
    headers = bootstrap_headers(client)

    create_response = client.post(
        "/personal/news",
        headers=headers,
        json={
            "id": 101,
            "key": "https://desktop.example/news-1",
            "title": "Desktop Favorite News",
            "url": "https://desktop.example/news-1",
            "summary": "draft summary",
            "content": "draft content",
            "source": "Desktop Source",
            "category": "财经",
            "published_at": "2026-03-11T12:00:00+00:00",
            "image_url": "https://desktop.example/news-1.png",
            "is_favorite": True,
            "created_at": "2026-03-11T12:00:00+00:00",
            "updated_at": "2026-03-11T12:10:00+00:00",
            "version": 1,
            "device_id": "desktop-a",
            "sync_state": "local_only",
        },
    )
    assert create_response.status_code == 200, create_response.text
    created_item = create_response.json()["items"]
    assert created_item["remote_id"]
    assert created_item["title"] == "Desktop Favorite News"
    assert created_item["is_favorite"] is True

    remote_id = created_item["remote_id"]
    update_response = client.put(
        f"/personal/news/{remote_id}",
        headers=headers,
        json={
            "remote_id": remote_id,
            "key": "https://desktop.example/news-1",
            "title": "Desktop Favorite News Updated",
            "url": "https://desktop.example/news-1",
            "summary": "updated summary",
            "content": "updated content",
            "source": "Desktop Source",
            "category": "国际",
            "published_at": "2026-03-11T12:00:00+00:00",
            "image_url": "https://desktop.example/news-1.png",
            "is_favorite": True,
            "created_at": "2026-03-11T12:00:00+00:00",
            "updated_at": "2026-03-11T12:20:00+00:00",
            "version": 2,
            "device_id": "desktop-a",
            "sync_state": "dirty",
            "last_synced_at": "2026-03-11T12:21:00+00:00",
        },
    )
    assert update_response.status_code == 200, update_response.text
    updated_item = update_response.json()["items"]
    assert updated_item["remote_id"] == remote_id
    assert updated_item["title"] == "Desktop Favorite News Updated"
    assert updated_item["version"] == 2
    assert updated_item["category"] == "国际"

    list_response = client.get("/personal/news?limit=10", headers=headers)
    assert list_response.status_code == 200, list_response.text
    listed_items = list_response.json()["items"]
    assert len(listed_items) == 1
    assert listed_items[0]["remote_id"] == remote_id

    delete_response = client.delete(f"/personal/news/{remote_id}", headers=headers)
    assert delete_response.status_code == 200, delete_response.text
    deleted_item = delete_response.json()["items"]
    assert deleted_item["remote_id"] == remote_id
    assert deleted_item["deleted_at"] is not None
    assert deleted_item["is_favorite"] is False

    after_delete = client.get("/personal/news?limit=10", headers=headers)
    assert after_delete.status_code == 200, after_delete.text
    after_delete_items = after_delete.json()["items"]
    assert len(after_delete_items) == 1
    assert after_delete_items[0]["remote_id"] == remote_id
    assert after_delete_items[0]["deleted_at"] is not None


@pytest.fixture(autouse=True)
def _unlock_personal_mirror(monkeypatch):
    """V2-01 镜像锁默认 403 拒写；本文件验证的是写流程本身，
    按迁移期回退语义显式关锁（专测锁行为的 test_personal_mirror_lock.py 不受影响）。"""
    monkeypatch.setenv("SIYI_HUB_PERSONAL_MIRROR_LOCK", "0")
