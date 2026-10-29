"""AutomationHubClient 契约测试。

覆盖：
- 实体注册表（SUPPORTED_ENTITIES）与 hub `/personal/*` 实际端点一致（防 9:6 漂移回归）
- 泛型公开 API（pull/pull_all/upsert/delete）的 URL 构造与错误处理
- 非法实体给出清晰错误，而非 KeyError
"""

from __future__ import annotations

import pytest

from integrations.automation_hub_client import (
    AutomationHubClient,
    AutomationHubClientError,
)


class _FakeSession:
    """记录请求构造、固定返回体，用于离线验证公开 API。"""

    def __init__(self, payload: dict, status: int = 200):
        self.payload = payload
        self.status = status
        self.last_request = None

    def request(self, method, url, headers=None, json=None, timeout=None):
        self.last_request = (method, url, headers, json)
        payload = self.payload
        if self.status != 200:
            raise AutomationHubClientError("fake non-200")

        class R:
            def raise_for_status(self):
                return None

            def json(self):
                return payload

        return R()


def _make_client(session, base_url="http://127.0.0.1:8000", token="secret-token"):
    return AutomationHubClient(base_url=base_url, api_token=token, session=session)


def test_supported_entities_match_hub_personal_endpoints():
    """注册表必须只含 hub 实际暴露的 6 类 `/personal/*` 实体。"""
    assert set(AutomationHubClient.SUPPORTED_ENTITIES) == {
        "note", "task", "news", "translation", "wordbook", "word",
    }


def test_unsupported_entity_raises_clear_error():
    """云同步实体（inbox_item 等）不得经 hub 直连，报清晰错误而非 KeyError。"""
    client = _make_client(_FakeSession({}))
    with pytest.raises(AutomationHubClientError) as excinfo:
        client.pull("inbox_item")
    assert "不支持的实体 'inbox_item'" in str(excinfo.value)


def test_pull_builds_correct_url_and_parses_list_field():
    session = _FakeSession({"notes": [{"id": "n1"}], "next_cursor": None})
    client = _make_client(session)
    resp = client.pull("note", since_updated_at="2026-01-01T00:00:00", limit=50)
    assert resp.items == [{"id": "n1"}]
    method, url, _, _ = session.last_request
    assert method == "GET"
    assert url == "http://127.0.0.1:8000/personal/notes?limit=50&since_updated_at=2026-01-01T00%3A00%3A00"


def test_pull_all_loops_until_no_cursor():
    session = _FakeSession({"items": [{"id": "x"}], "next_cursor": None})
    client = _make_client(session)
    resp = client.pull_all("news")
    assert resp.items == [{"id": "x"}]


def test_upsert_posts_when_no_remote_id():
    session = _FakeSession({"task": {"id": "t1"}})
    client = _make_client(session)
    resp = client.upsert("task", {"id": "t1"})
    assert resp.item == {"id": "t1"}
    method, url, _, body = session.last_request
    assert method == "POST"
    assert url == "http://127.0.0.1:8000/personal/tasks"
    assert body["id"] == "t1"


def test_upsert_puts_when_remote_id_present():
    session = _FakeSession({"word": {"id": "w1"}})
    client = _make_client(session)
    resp = client.upsert("word", {"id": "w1", "remote_id": "r9"})
    assert resp.item == {"id": "w1"}
    method, url, _, _ = session.last_request
    assert method == "PUT"
    assert url == "http://127.0.0.1:8000/personal/words/r9"


def test_upsert_requires_local_id():
    client = _make_client(_FakeSession({}))
    with pytest.raises(AutomationHubClientError):
        client.upsert("note", {"remote_id": "r1"})


def test_delete_requires_remote_id():
    client = _make_client(_FakeSession({}))
    with pytest.raises(AutomationHubClientError):
        client.delete("note", {"id": "n1"})


def test_delete_uses_delete_method():
    session = _FakeSession({"note": {"remote_id": "r5"}})
    client = _make_client(session)
    resp = client.delete("note", {"id": "n1", "remote_id": "r5"})
    assert resp.item["remote_id"] == "r5"
    method, url, _, _ = session.last_request
    assert method == "DELETE"
    assert url == "http://127.0.0.1:8000/personal/notes/r5"


def test_private_aliases_delegate_to_public_api():
    """改写后私有 alias 仍可用，保证既有调用不破坏。"""
    session = _FakeSession({"tasks": [{"id": "t1"}]})
    client = _make_client(session)
    resp = client._pull_all("task")
    assert resp.items == [{"id": "t1"}]


def test_missing_base_url_raises():
    client = AutomationHubClient(base_url=None, api_token=None, session=_FakeSession({}))
    with pytest.raises(AutomationHubClientError):
        client.pull("note")