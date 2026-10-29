"""D22-2 出口测试：upsert_items 事务性回归 + C-40 五方法（info repo CRUD）。

验证点（任务书出口断言）：
1. upsert_items 期间注入异常（updater 抛错）→ 键值不变（BEGIN IMMEDIATE 回滚）；
2. 五方法各 ≥1 用例：get_item 命中/未命中、update_item 合并补丁、
   toggle_read/toggle_saved 翻转、get_sources 去重；str|int id 兼容；
3. 损坏隔离拒写语义（沿用 _chat_session 先例）：SQLite 值损坏且现场保全失败
   → 抛 RuntimeError 拒写；保全成功 → 以空默认值继续、未命中返回 None。
"""
from __future__ import annotations

import json
import tempfile

import pytest

from data.local_storage import LocalDataStore
from repositories._info_item import LocalInfoItemRepository


def _new_store() -> LocalDataStore:
    return LocalDataStore(base_dir=tempfile.mkdtemp())


@pytest.fixture()
def repo() -> LocalInfoItemRepository:
    return LocalInfoItemRepository(_new_store())


def test_upsert_items_rolls_back_on_updater_error(monkeypatch):
    store = _new_store()
    repo = LocalInfoItemRepository(store)
    repo.upsert_items([{"url": "https://a.example/x", "title": "A"}])
    before = store.load_json("info_items", [])
    assert len(before) == 1

    real_update_json = LocalDataStore.update_json

    def _exploding(self, key, default, updater):
        def _wrapped(current):
            updated = updater(current)
            raise RuntimeError("injected updater failure")
        return real_update_json(self, key, default, _wrapped)

    monkeypatch.setattr(LocalDataStore, "update_json", _exploding)
    with pytest.raises(RuntimeError, match="injected"):
        repo.upsert_items([{"url": "https://b.example/y", "title": "B"}])
    monkeypatch.undo()

    # 事务回滚：磁盘值保持原样（绕开缓存，从 SQLite 重读）
    raw = store._sqlite.get("info_items", "[]")
    assert json.loads(raw) == before


def test_get_item_hit_and_miss(repo):
    repo.upsert_items([{"url": "https://a.example/1", "title": "One"}])
    items, _ = repo.list_items(limit=10)
    item_id = items[0]["id"]
    found = repo.get_item(item_id)
    assert found is not None and found["title"] == "One"
    assert repo.get_item("999999") is None
    assert repo.get_item(str(item_id)) is not None  # str|int 兼容


def test_update_item_merges_patch(repo):
    repo.upsert_items([{"url": "https://a.example/2", "title": "Two"}])
    items, _ = repo.list_items(limit=10)
    item_id = items[0]["id"]
    updated = repo.update_item(item_id, {"notes": "hello", "read": True})
    assert updated is not None and updated["notes"] == "hello" and updated["read"] is True
    assert repo.update_item(item_id, {}) is None
    assert repo.update_item("888888", {"read": True}) is None


def test_toggle_read_and_saved_flip(repo):
    repo.upsert_items([{"url": "https://a.example/3", "title": "Three"}])
    items, _ = repo.list_items(limit=10)
    item_id = items[0]["id"]
    assert repo.get_item(item_id)["read"] is False
    t1 = repo.toggle_read(item_id)
    assert t1 is not None and t1["read"] is True
    t2 = repo.toggle_read(item_id)
    assert t2 is not None and t2["read"] is False
    s1 = repo.toggle_saved(item_id)
    assert s1 is not None and s1["saved"] is True
    assert repo.toggle_read("777777") is None
    assert repo.toggle_saved("777777") is None


def test_toggle_persisted_across_store_instances(repo):
    repo.upsert_items([{"url": "https://a.example/4", "title": "Four"}])
    items, _ = repo.list_items(limit=10)
    item_id = items[0]["id"]
    repo.toggle_saved(item_id)
    repo2 = LocalInfoItemRepository(repo.data_store)
    assert repo2.get_item(item_id)["saved"] is True


def test_get_sources_dedup(repo):
    repo.upsert_items([
        {"url": "https://a.example/5", "title": "5", "source": "rss-a"},
        {"url": "https://b.example/6", "title": "6", "source": "rss-b"},
        {"url": "https://c.example/7", "title": "7", "source": "rss-a"},
    ])
    sources = repo.get_sources()
    assert sorted(sources) == ["rss-a", "rss-b"]


def test_corrupt_blob_preserve_failure_refuses_write(repo, monkeypatch):
    store = repo.data_store
    store._sqlite.set("info_items", "{ this is not valid json")
    monkeypatch.setattr(store, "_preserve_corrupt_blob", lambda key, text: None)
    with pytest.raises(RuntimeError, match="拒绝覆写"):
        store.update_json("info_items", [], lambda cur: cur)


def test_corrupt_blob_preserved_continues_with_default(repo, monkeypatch):
    store = repo.data_store
    store._sqlite.set("info_items", "{ this is not valid json")
    monkeypatch.setattr(store, "_preserve_corrupt_blob", lambda key, text: "fake/quarantine.path")
    assert repo.toggle_read("1") is None
    assert repo.get_sources() == []


# ── D22-5 表路径（info_items_tb）────────────────────────────────────


def _with_table_repo() -> LocalInfoItemRepository:
    store = _new_store()
    from data._hot_tables import ensure_hot_tables
    ensure_hot_tables(store._sqlite._connect())
    return LocalInfoItemRepository(store)


def test_table_path_upsert_merge_and_crud():
    repo = _with_table_repo()
    assert repo.upsert_items([{"url": "https://t.example/1", "title": "T1", "source": "s1"}]) == 1
    assert repo.upsert_items([{"url": "https://t.example/1", "title": "T1-renamed", "score": 9}]) == 1
    items, total = repo.list_items(limit=10)
    assert total == 1 and items[0]["title"] == "T1-renamed"
    assert isinstance(repo.get_item(items[0]["id"]), dict)
    assert repo.get_sources() == ["s1"]
    stats = repo.get_stats()
    assert stats["total"] == 1 and stats["unread"] == 1 and stats["saved"] == 0
    # 无 url 条目（真库 62.7% 形状）可经表路径 CRUD
    import json as _json
    orphan = {"id": 99, "title": "no-url", "source": "hn", "read": False, "saved": False, "tags": []}
    conn = repo._table_conn()
    conn.execute(
        "INSERT INTO info_items_tb (id, url, title, tags, read, saved, raw_json)"
        " VALUES (?,?,?,'[]',0,0,?)",
        (99, None, "no-url", _json.dumps(orphan)),
    )
    assert repo.get_item(99)["source"] == "hn"
    flipped = repo.toggle_saved(99)
    assert flipped["saved"] is True
    assert repo.get_item("99")["saved"] is True
    assert _json.loads(conn.execute(
        "SELECT raw_json FROM info_items_tb WHERE id=99").fetchone()[0])["saved"] is True
