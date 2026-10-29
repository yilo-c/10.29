"""D22-5 出口测试：_inbox.py 读写路径改道 inbox_entries_tb。

验证点：
1. 表路径 add/update/list/get 全通，monkeypatch save_json 计数 == 0
   （:70 裸写在表路径彻底消灭，kv 回退亦改走 update_json 事务）；
2. 表路径 string-id 命中（①-3 类失效在表路径修正）；
3. 表未建 → kv 回退路径行为保持；
4. 迁移脚本灌数后 repo 立即可从表读到（迁移-读取闭环）。
"""
from __future__ import annotations

import importlib.util
import json
import os
import tempfile

import pytest

from data._hot_tables import ensure_hot_tables
from data.local_storage import LocalDataStore
from repositories._inbox import LocalPersonalInboxRepository

_SCRIPT = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "scripts", "migrate_kv_to_tables.py",
)


def _new_store() -> LocalDataStore:
    return LocalDataStore(base_dir=tempfile.mkdtemp())


def _with_table() -> LocalDataStore:
    store = _new_store()
    ensure_hot_tables(store._sqlite._connect())
    return store


@pytest.fixture()
def save_counter(monkeypatch):
    calls = {"n": 0}
    real = LocalDataStore.save_json

    def _counting(self, key, data, *args, **kwargs):
        calls["n"] += 1
        return real(self, key, data, *args, **kwargs)

    monkeypatch.setattr(LocalDataStore, "save_json", _counting)
    return calls


def test_table_path_add_update_list_get(save_counter):
    store = _with_table()
    repo = LocalPersonalInboxRepository(store)
    new_id = repo.add_entry({"title": "表路径条目", "tags": [" a ", "b"], "metadata": {"k": 1}})
    assert new_id == 1
    entry = repo.get_entry(1)
    assert entry is not None and entry["title"] == "表路径条目"
    assert entry["tags"] == ["a", "b"]
    assert entry["status"] == "new"
    repo.update_entry(new_id, {"status": "archived"})
    assert repo.list_entries() == []
    archived = repo.list_entries(include_archived=True)
    assert len(archived) == 1 and archived[0]["status"] == "archived"
    assert save_counter["n"] == 0, "表路径不得触碰 save_json"
    # 未建表的库上同样断言 kv 回退不再裸写
    store2 = _new_store()
    repo2 = LocalPersonalInboxRepository(store2)
    repo2.add_entry({"title": "kv 条目"})
    repo2.list_entries()
    assert save_counter["n"] == 0


def test_table_path_string_id_hits(save_counter):
    store = _with_table()
    repo = LocalPersonalInboxRepository(store)
    repo.add_entry({"title": "T"})
    repo.update_entry("1", {"title": "T2"})  # str id（前端 IdeaPanel 形状）
    assert repo.get_entry(1)["title"] == "T2"
    assert repo.get_entry("1")["title"] == "T2"
    assert save_counter["n"] == 0


def test_kv_fallback_before_migration(save_counter):
    store = _new_store()  # 不建表
    repo = LocalPersonalInboxRepository(store)
    new_id = repo.add_entry({"title": "旧世界"})
    assert new_id == 1
    entries = repo.list_entries(include_archived=True)
    assert entries and entries[0]["title"] == "旧世界"
    raw = store._sqlite.get("inbox_entries", "[]")
    assert json.loads(raw)[0]["title"] == "旧世界"
    assert save_counter["n"] == 0


def test_migrated_kv_visible_via_table(save_counter):
    store = _new_store()
    store.save_json("inbox_entries", [
        {"id": 5, "title": "历史1", "content": "", "source": "manual",
         "status": "new", "tags": ["x"], "metadata": {},
         "created_at": "2026-01-01T00:00:00+00:00",
         "updated_at": "2026-01-01T00:00:00+00:00"},
        {"id": 6, "title": "历史2", "content": "", "source": "relay",
         "status": "archived", "tags": [], "metadata": {},
         "created_at": "2026-01-02T00:00:00+00:00",
         "updated_at": "2026-01-02T00:00:00+00:00"},
    ])
    baseline = save_counter["n"]  # 播种调用不计入改道违例
    spec = importlib.util.spec_from_file_location("migrate_kv_to_tables", _SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.main(["--data-dir", store.base_dir]) == 0
    repo = LocalPersonalInboxRepository(store)
    titles = [e["title"] for e in repo.list_entries()]
    assert titles == ["历史1"]
    assert save_counter["n"] == baseline
    # 迁移后写路径落表：kv 锚值不变
    repo.add_entry({"title": "新条目"})
    raw = store._sqlite.get("inbox_entries", "[]")
    assert [it["title"] for it in json.loads(raw)] == ["历史1", "历史2"]
    assert len(repo.list_entries(include_archived=True)) == 3
