"""LocalDataStore 单元测试 '"覆盖核心读写、键完整性和边界场景。"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from data.local_storage import LocalDataStore

# 鈹€鈹€ 基本读写 鈹€鈹€


def test_save_and_load_json(tmp_path):
    store = LocalDataStore(base_dir=str(tmp_path))
    store.save_json("tasks", [{"id": 1, "name": "hello"}])
    result = store.load_json("tasks", [])
    assert result == [{"id": 1, "name": "hello"}]


def test_load_missing_returns_default(tmp_path):
    store = LocalDataStore(base_dir=str(tmp_path))
    assert store.load_json("tasks", []) == []
    settings = store.load_json("settings", {"a": 1})
    assert isinstance(settings, dict)
    assert settings


def test_load_corrupted_json_returns_default(tmp_path):
    store = LocalDataStore(base_dir=str(tmp_path))
    path = store.paths["tasks"]
    Path(path).write_text("not valid json{{{", encoding="utf-8")
    assert store.load_json("tasks", []) == []


# 鈹€鈹€ 閿畬鏁存€"鈹€鈹€


def test_all_used_keys_are_registered(tmp_path):
    """纭繚代码涓娇用的 key 都在 paths 涓敞册，避免 KeyError。"""
    store = LocalDataStore(base_dir=str(tmp_path))
    required_keys = [
        "tasks",
        "cloud_tasks",
        "remote_snapshot_tasks",
        "settings",
        "notes",
        "cloud_notes",
        "remote_snapshot_notes",
        "translations",
        "cloud_translations",
        "remote_snapshot_translations",
        "words",
        "cloud_words",
        "remote_snapshot_words",
        "wordbooks",
        "cloud_wordbooks",
        "remote_snapshot_wordbooks",
        "news",
        "cloud_news",
        "remote_snapshot_news",
        "news_cache",
        "web_search_cache",
        "news_history",
        "reminders",
        "inbox_entries",
        "improvement_proposals",
        "integration_oauth_tokens",
        "integration_sync_cursors",
        "integration_conflicts",
        "integration_audit_logs",
        "assistant_memory",
        "assistant_memory_index",
        "assistant_decisions",
        "assistant_rules",
        "assistant_events",
        "assistant_agent_tasks",
        "assistant_usage_stats",
        "assistant_execution_runs",
        "assistant_codex_runs",
        "permission_matrix",
        "permission_quotas",
        "audit_trail",
        "rule_center_rules",
        # assistant_memory_* keys (registered in paths but missing from test)
        "assistant_memory_pending",
        "assistant_memory_conflicts",
        "assistant_memory_extraction_log",
        "assistant_memory_schedules",
        "assistant_memory_schedules_settings",
        "assistant_memory_links",
        "assistant_memory_co_access",
        "assistant_decision_memory_history",
        "assistant_imported_memory_md",
        "assistant_session_summaries",
        "supertag_structured_data",
    ]
    for key in required_keys:
        assert key in store.paths, f"key '{key}' missing from LocalDataStore.paths"


def test_assistant_codex_runs_roundtrip(tmp_path):
    """回归测试：assistant_codex_runs 閿繀须可正常读写。"""
    store = LocalDataStore(base_dir=str(tmp_path))
    store.save_json("assistant_codex_runs", [{"task_id": "t1", "status": "success"}])
    loaded = store.load_json("assistant_codex_runs", [])
    assert len(loaded) == 1
    assert loaded[0]["task_id"] == "t1"


# 鈹€鈹€ next_id 鈹€鈹€


def test_next_id_empty(tmp_path):
    # 2026-09-17 泄漏守卫：LocalDataStore() 不传 base_dir 会回落到生产 data/，
    # 显式指向 tmp_path（本用例只调 next_id，但仍不给任何回落的可能）。
    store = LocalDataStore(base_dir=str(tmp_path))
    assert store.next_id([]) == 1


def test_next_id_existing(tmp_path):
    store = LocalDataStore(base_dir=str(tmp_path))
    assert store.next_id([{"id": 3}, {"id": 7}]) == 8


# 鈹€鈹€ 涓枃鍐呭 鈹€鈹€


def test_chinese_content_roundtrip(tmp_path):
    """P1-6 迁移后：底层是 SQLite，不再是单独的 .json 文件。
    这里只校验中文内容能完整 round-trip（写入 + 读取 + 清缓存后重读）。"""
    store = LocalDataStore(base_dir=str(tmp_path))
    store.save_json("notes", [{"id": 1, "content": "思忆集测试笔璁", "tags": ["学习", "AI"]}])
    loaded = store.load_json("notes", [])
    assert loaded[0]["content"] == "思忆集测试笔璁"
    assert loaded[0]["tags"] == ["学习", "AI"]
    # 清缓存后再读：底层 SQLite 也持有 UTF-8 原文
    store.invalidate_cache("notes")
    reloaded = store.load_json("notes", [])
    assert reloaded[0]["content"] == "思忆集测试笔璁"


def test_storage_meta_created_on_init(tmp_path):
    store = LocalDataStore(base_dir=str(tmp_path))
    meta_path = Path(store.meta_path)
    assert meta_path.exists()
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    assert meta["storage_schema_version"] == store.STORAGE_SCHEMA_VERSION
    assert meta["versions"]["tasks"] == store.STORAGE_SCHEMA_VERSION
    assert meta["versions"]["settings"] == store.STORAGE_SCHEMA_VERSION


def test_storage_migration_coerces_invalid_top_level_shape(tmp_path):
    tasks_path = Path(tmp_path) / "tasks.json"
    tasks_path.write_text(json.dumps({"unexpected": True}, ensure_ascii=False), encoding="utf-8")

    store = LocalDataStore(base_dir=str(tmp_path))

    assert store.load_json("tasks", []) == []
    meta = json.loads(Path(store.meta_path).read_text(encoding="utf-8"))
    assert meta["versions"]["tasks"] == store.STORAGE_SCHEMA_VERSION


def test_update_json_applies_atomic_read_modify_write(tmp_path):
    store = LocalDataStore(base_dir=str(tmp_path))
    store.save_json("assistant_events", [{"event": "seed"}])

    updated = store.update_json(
        "assistant_events",
        [],
        lambda items: (list(items) if isinstance(items, list) else []) + [{"event": "next"}],
    )

    assert updated == [{"event": "seed"}, {"event": "next"}]
    assert store.load_json("assistant_events", []) == [{"event": "seed"}, {"event": "next"}]


# 鈹€鈹€ fts_search_items 鈹€鈹€


def test_fts_search_items_basic():
    """回归：fts_search_items 必须作为 LocalDataStore 的类方法鍙皟鐢ㄣ€"""
    items = [
        {"id": 1, "title": "学习 Python", "content": "鍩虹璇硶"},
        {"id": 2, "title": "AI 绗旇", "content": "transformer 模型"},
        {"id": 3, "title": "日常事务", "content": "买菜"},
    ]
    result = LocalDataStore.fts_search_items(items, "AI", ["title", "content"])
    assert len(result) == 1
    assert result[0]["id"] == 2


def test_fts_search_items_priority_field():
    """title 瀛楁命中权重高于 content。"""
    items = [
        {"id": 1, "title": "Python 教程", "content": "学习鍩虹"},
        {"id": 2, "title": "日常绗旇", "content": "Python 代码鐗囨"},
    ]
    result = LocalDataStore.fts_search_items(items, "Python", ["title", "content"])
    assert len(result) == 2
    assert result[0]["id"] == 1  # title 命中排在前面


def test_fts_search_items_empty_query():
    items = [{"id": 1, "title": "test"}]
    assert LocalDataStore.fts_search_items(items, "", ["title"]) == []
    assert LocalDataStore.fts_search_items(items, "   ", ["title"]) == []


# 鈹€鈹€ TTL 缓存 鈹€鈹€


def test_cache_hit_returns_cached_data_without_reload(tmp_path):
    store = LocalDataStore(base_dir=str(tmp_path))
    store.save_json("tasks", [{"id": 1, "name": "first"}])

    store.load_json("tasks", [])  # populates cache

    import json
    from pathlib import Path

    path = Path(store.paths["tasks"])
    path.write_text(json.dumps([{"id": 2, "name": "second"}], ensure_ascii=False), encoding="utf-8")

    result = store.load_json("tasks", [])
    assert result == [{"id": 1, "name": "first"}], "cache should return stale data, disk was modified directly"


def test_cache_miss_reads_from_disk(tmp_path):
    store = LocalDataStore(base_dir=str(tmp_path))
    store.save_json("tasks", [{"id": 42}])
    store.load_json("tasks", [])  # populates cache
    store.invalidate_cache("tasks")
    result = store.load_json("tasks", [])
    assert result == [{"id": 42}]


def test_invalidate_cache_force_reload(tmp_path):
    """P1-6 迁移后：底层 SQLite，没有用户能直接写的「文件」。
    这里改成模拟两个 store 共享同一个 db_path：A 写 → B（清缓存后）能读到。"""
    store = LocalDataStore(base_dir=str(tmp_path))
    store.save_json("tasks", [{"id": 1}])
    store.load_json("tasks", [])  # populates cache

    # 第二个 store 实例对应同一个 SQLite 文件（模拟「外部修改」）
    other = LocalDataStore(base_dir=str(tmp_path))
    other.save_json("tasks", [{"id": 2}])

    # 第一个 store 缓存仍是旧值
    assert store.load_json("tasks", []) == [{"id": 1}]
    # 清缓存后再读：SQLite 已经被 other 改写
    store.invalidate_cache("tasks")
    result = store.load_json("tasks", [])
    assert result == [{"id": 2}]


def test_clear_cache_clears_all(tmp_path):
    store = LocalDataStore(base_dir=str(tmp_path))
    store.save_json("tasks", [{"id": 1}])
    store.save_json("notes", [{"id": "a"}])
    store.load_json("tasks", [])
    store.load_json("notes", [])
    store.clear_cache()
    assert len(store._cache) == 0


# ── QUA-0 容错三件套：损坏 JSON → 改名保全 + 空默认续用 + 写回原路径可读 ──


def test_chat_session_corrupt_latest_quarantine_resume(tmp_path):
    """损坏的 latest.json → ①改名保全现场 + ②空默认续用 + 写成功后原路径可正常读。

    回归：此前 _load_json_file 遇损坏静默返回空默认，随后的 _save_latest 会整体
    覆盖写坏数据（QUA-0）。修复后损坏文件应被 os.rename 为 <name>.corrupt-<ts>
    保全，功能继续，新写入落回原路径而不抹除损坏现场。
    """
    from repositories._chat_session import LocalChatSessionRepository

    store = LocalDataStore(base_dir=str(tmp_path))
    repo = LocalChatSessionRepository(store)
    sid = repo.create_session("测试会话")
    repo.add_message(sid, "你", "hello")

    base = os.path.dirname(store.paths["chat_sessions"])
    session_dir = Path(base) / "chat_sessions" / str(sid)
    latest = session_dir / "latest.json"
    assert latest.exists()
    latest.write_text("not valid json{{{", encoding="utf-8")

    # 读会触发损坏 → 隔离保全 + 空默认续用；随后写回原路径
    repo.add_message(sid, "你", "world")

    # ① 损坏现场被改名保全
    corrupts = list(session_dir.glob("latest.json.corrupt-*"))
    assert len(corrupts) == 1, f"损坏文件未被隔离保全: {corrupts}"

    # ②③ 原路径存在且新写入可正常读回
    assert latest.exists()
    msgs = repo.get_messages(sid)
    assert msgs and msgs[-1]["message"] == "world"
    assert latest.read_text(encoding="utf-8") and "world" in latest.read_text(encoding="utf-8")
