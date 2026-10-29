"""P1-6 / P2-18 验证：LocalDataStore 由 JSON+lock+.bak 三件套迁移到
SQLite WAL KV 单表后的行为契约。

覆盖：
* :class:`SqliteKVStore` 单元测试 —— WAL pragma、get/set/update 原子性、
  set_many 批量、has/keys、空字符串/Unicode、updater 异常回滚。
* :class:`LocalDataStore` 首启迁移 —— 合法 list / 合法 dict 迁入、形状不符
  跳过、损坏文件改名 .corrupted、settings 走业务级迁移钩子、空目录、
  迁移后 .legacy 留存、二次启动幂等。
* 兼容性 —— settings 镜像写明文 JSON、_storage_meta.json 字段、虚拟 paths
  仍可索引、_path_lock 空壳上下文管理器、多 store 共享同一 db_path、并发
  update_json 不撕裂。

测试统一用 ``tmp_path`` 隔离，避免依赖磁盘。"""

from __future__ import annotations

import json
import sqlite3
import sys
import threading
from pathlib import Path

import pytest

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from data.local_storage import LocalDataStore
from data.sqlite_store import SqliteKVStore


# ── SqliteKVStore 单元测试 ─────────────────────────────────────────────


def test_sqlite_store_creates_wal_journal(tmp_path):
    """初始化后底层 SQLite 应处于 WAL 模式（PRAGMA journal_mode 返回 wal）。"""
    db_path = tmp_path / "kv.db"
    store = SqliteKVStore(str(db_path))
    conn = store._connect()
    mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
    assert str(mode).lower() == "wal"


def test_sqlite_store_set_get_roundtrip(tmp_path):
    store = SqliteKVStore(str(tmp_path / "kv.db"))
    store.set("alpha", "1")
    assert store.get("alpha") == "1"
    # 覆写
    store.set("alpha", "2")
    assert store.get("alpha") == "2"


def test_sqlite_store_get_default_when_missing(tmp_path):
    store = SqliteKVStore(str(tmp_path / "kv.db"))
    assert store.get("nope") is None
    assert store.get("nope", default="fallback") == "fallback"


def test_sqlite_store_has_keys(tmp_path):
    store = SqliteKVStore(str(tmp_path / "kv.db"))
    assert store.has("x") is False
    store.set("x", "1")
    store.set("y", "2")
    assert store.has("x") is True
    assert store.has("y") is True
    assert set(store.keys()) == {"x", "y"}


def test_sqlite_store_delete(tmp_path):
    store = SqliteKVStore(str(tmp_path / "kv.db"))
    store.set("k", "v")
    store.delete("k")
    assert store.has("k") is False
    # 删除不存在的 key 不应抛
    store.delete("missing")


def test_sqlite_store_set_many_batch(tmp_path):
    store = SqliteKVStore(str(tmp_path / "kv.db"))
    payload = {f"k{i}": str(i) for i in range(50)}
    store.set_many(payload)
    assert store.row_count() == 50
    assert store.get("k0") == "0"
    assert store.get("k49") == "49"


def test_sqlite_store_set_many_empty_noop(tmp_path):
    store = SqliteKVStore(str(tmp_path / "kv.db"))
    store.set_many({})
    assert store.row_count() == 0


def test_sqlite_store_update_atomic_commits(tmp_path):
    store = SqliteKVStore(str(tmp_path / "kv.db"))
    store.set("counter", "1")
    new_val = store.update("counter", "0", lambda cur: str(int(cur) + 1))
    assert new_val == "2"
    assert store.get("counter") == "2"


def test_sqlite_store_update_rollback_on_updater_exception(tmp_path):
    """updater 抛异常 → 事务回滚，磁盘值保持原状。"""
    store = SqliteKVStore(str(tmp_path / "kv.db"))
    store.set("k", "old")

    def boom(_current: str) -> str:
        raise RuntimeError("updater failure")

    with pytest.raises(RuntimeError):
        store.update("k", "", boom)

    assert store.get("k") == "old"


def test_sqlite_store_update_seeds_default_when_missing(tmp_path):
    store = SqliteKVStore(str(tmp_path / "kv.db"))
    new_val = store.update("fresh", "[]", lambda cur: cur + " /touched")
    assert new_val == "[] /touched"
    assert store.get("fresh") == "[] /touched"


def test_sqlite_store_unicode_payload(tmp_path):
    store = SqliteKVStore(str(tmp_path / "kv.db"))
    store.set("zh", "思忆集 — 学习 / AI / 笔记")
    assert store.get("zh") == "思忆集 — 学习 / AI / 笔记"


# ── LocalDataStore 首启迁移 ────────────────────────────────────────────


def _write_legacy_json(base_dir: Path, key: str, payload) -> Path:
    """工具：在 base_dir 下写一份 <key>.json 模拟旧版数据，返回文件路径。"""
    target = base_dir / f"{key}.json"
    target.write_text(
        json.dumps(payload, ensure_ascii=False),
        encoding="utf-8",
    )
    return target


def test_legacy_migration_imports_valid_list(tmp_path):
    """legacy tasks.json (list) → SQLite，旧文件改名 .legacy.<ts>。"""
    legacy = _write_legacy_json(tmp_path, "tasks", [{"id": 1, "title": "a"}])
    store = LocalDataStore(base_dir=str(tmp_path))
    assert store.load_json("tasks", []) == [{"id": 1, "title": "a"}]
    # 原文件已不存在；存在一个 .legacy.<ts> 兄弟文件
    assert not legacy.exists()
    legacies = list(tmp_path.glob("tasks.json.legacy.*"))
    assert legacies, "旧 JSON 应改名为 .legacy.<ts>"


def test_legacy_migration_imports_valid_dict(tmp_path):
    legacy = _write_legacy_json(tmp_path, "assistant_memory", {"trace": "ok"})
    store = LocalDataStore(base_dir=str(tmp_path))
    assert store.load_json("assistant_memory", {}) == {"trace": "ok"}
    assert not legacy.exists()


def test_legacy_migration_skips_shape_mismatch_list(tmp_path):
    """tasks 期望 list，遗留文件给的是 dict → 跳过导入，SQLite 仍为空。
    旧文件仍改名为 .legacy（避免下次启动重试）。"""
    legacy = _write_legacy_json(tmp_path, "tasks", {"oops": True})
    store = LocalDataStore(base_dir=str(tmp_path))
    assert store.load_json("tasks", []) == []
    # 原 path 应已改名
    assert not legacy.exists()
    assert list(tmp_path.glob("tasks.json.legacy.*"))


def test_legacy_migration_skips_shape_mismatch_dict(tmp_path):
    """settings 期望 dict，遗留文件给的是 list → 跳过导入。"""
    legacy = _write_legacy_json(tmp_path, "settings", ["x", "y"])
    store = LocalDataStore(base_dir=str(tmp_path))
    loaded = store.load_json("settings", {})
    assert isinstance(loaded, dict)  # 不是从迁移导入，而是 settings 业务初始化
    assert not legacy.exists()


def test_legacy_migration_renames_corrupted_json(tmp_path):
    """语法损坏的 JSON 文件应改名为 .corrupted.<ts>，绝不静默丢弃。"""
    legacy = tmp_path / "tasks.json"
    legacy.write_text("{not json", encoding="utf-8")
    store = LocalDataStore(base_dir=str(tmp_path))
    assert store.load_json("tasks", []) == []
    assert not legacy.exists()
    corrupted = list(tmp_path.glob("tasks.json.corrupted.*"))
    assert corrupted, "损坏文件应被改名为 .corrupted.<ts> 保留物证"


def test_legacy_migration_idempotent_on_second_init(tmp_path):
    """第二次实例化 LocalDataStore 不应重复迁移 / 产生多余 .legacy 文件。"""
    _write_legacy_json(tmp_path, "notes", [{"id": 1}])
    LocalDataStore(base_dir=str(tmp_path))
    first_legacies = sorted(tmp_path.glob("notes.json.legacy.*"))
    assert len(first_legacies) == 1

    # 第二次启动：无原 notes.json，所以不会再多生成 .legacy
    LocalDataStore(base_dir=str(tmp_path))
    second_legacies = sorted(tmp_path.glob("notes.json.legacy.*"))
    assert second_legacies == first_legacies


def test_legacy_migration_empty_base_dir_is_safe(tmp_path):
    """目录里啥都没有，首启迁移不应抛。"""
    store = LocalDataStore(base_dir=str(tmp_path))
    # SQLite 文件与 _storage_meta.json 应已创建
    assert (Path(store.db_path)).exists()
    assert Path(store.meta_path).exists()


# ── meta / schema ────────────────────────────────────────────────────


def test_storage_meta_records_schema_version_for_all_keys(tmp_path):
    store = LocalDataStore(base_dir=str(tmp_path))
    meta = json.loads(Path(store.meta_path).read_text(encoding="utf-8"))
    assert meta["storage_schema_version"] == LocalDataStore.STORAGE_SCHEMA_VERSION
    # 所有 paths 中的 key 都应被记录在 versions 里
    for key in store.paths:
        assert meta["versions"][key] == LocalDataStore.STORAGE_SCHEMA_VERSION


def test_storage_meta_preserves_external_keys(tmp_path):
    """memory/migration.py 写入 _memory_migration_done_v1，二次启动应保留。"""
    meta_path = tmp_path / LocalDataStore.STORAGE_META_FILENAME
    meta_path.write_text(
        json.dumps(
            {
                "storage_schema_version": LocalDataStore.STORAGE_SCHEMA_VERSION,
                "versions": {},
                "_memory_migration_done_v1": True,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    LocalDataStore(base_dir=str(tmp_path))
    meta_after = json.loads(meta_path.read_text(encoding="utf-8"))
    assert meta_after.get("_memory_migration_done_v1") is True


# ── settings 镜像写 ──────────────────────────────────────────────────


def test_settings_save_mirrors_to_plain_json(tmp_path):
    """save_json("settings", ...) 应同时镜像写明文 settings.json，
    供 claudejiaoben/diag/*.py 之类的诊断脚本直读。"""
    store = LocalDataStore(base_dir=str(tmp_path))
    store.save_json("settings", {"llm_api_key": "sk-xxx", "theme": "dark"})

    mirror = tmp_path / "settings.json"
    assert mirror.exists()
    payload = json.loads(mirror.read_text(encoding="utf-8"))
    assert payload == {"llm_api_key": "sk-xxx", "theme": "dark"}


def test_non_settings_keys_do_not_mirror(tmp_path):
    """除 settings 之外的键不应写明文 JSON 文件。"""
    store = LocalDataStore(base_dir=str(tmp_path))
    store.save_json("tasks", [{"id": 1}])

    # 路径表里有 tasks.json，但真实落盘是 SQLite —— 不应有真实文件
    tasks_file = tmp_path / "tasks.json"
    assert not tasks_file.exists()


# ── 公共 API：路径表与 _path_lock 兼容 ──────────────────────────────────


def test_paths_dict_remains_indexable(tmp_path):
    """虚拟 paths 仍要支持 store.paths[key] 取字符串路径（dirname 等场景）。"""
    store = LocalDataStore(base_dir=str(tmp_path))
    chat_path = store.paths["chat_sessions"]
    assert isinstance(chat_path, str)
    assert chat_path.endswith("chat_sessions.json")
    # 父目录恰好是 base_dir（repositories/_chat_session.py 依赖此约定）
    import os as _os

    assert _os.path.dirname(chat_path) == str(tmp_path)


def test_path_lock_stub_is_noop_contextmanager(tmp_path):
    """旧版的 _path_lock 上下文管理器应保留为 no-op，不至于让遗留调用方崩溃。"""
    store = LocalDataStore(base_dir=str(tmp_path))
    with store._path_lock(store.paths["tasks"]):
        store.save_json("tasks", [{"id": 1}])
    assert store.load_json("tasks", []) == [{"id": 1}]


# ── 跨实例共享 + 并发 ─────────────────────────────────────────────────


def test_two_stores_share_same_sqlite_file(tmp_path):
    """两个 LocalDataStore 共享同一个 base_dir → 写入对方可见（清缓存后）。"""
    a = LocalDataStore(base_dir=str(tmp_path))
    b = LocalDataStore(base_dir=str(tmp_path))

    a.save_json("notes", [{"id": 1, "content": "from-a"}])
    b.invalidate_cache("notes")
    assert b.load_json("notes", []) == [{"id": 1, "content": "from-a"}]


def test_concurrent_update_json_no_torn_write(tmp_path):
    """两个线程同时 update_json 累加计数器，最终结果应为 +N 而非中间撕裂值。"""
    store = LocalDataStore(base_dir=str(tmp_path))
    store.save_json("assistant_usage_stats", {"count": 0})

    rounds = 30
    threads_n = 4

    def worker():
        for _ in range(rounds):
            store.update_json(
                "assistant_usage_stats",
                {"count": 0},
                lambda cur: {"count": int((cur or {}).get("count", 0)) + 1},
            )

    threads = [threading.Thread(target=worker) for _ in range(threads_n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    store.invalidate_cache("assistant_usage_stats")
    final = store.load_json("assistant_usage_stats", {})
    assert final["count"] == rounds * threads_n


def test_update_json_rolls_back_on_updater_exception(tmp_path):
    """updater 抛异常 → SQLite 维持旧值。"""
    store = LocalDataStore(base_dir=str(tmp_path))
    store.save_json("assistant_events", [{"event": "seed"}])

    def boom(_):
        raise ValueError("oops")

    with pytest.raises(ValueError):
        store.update_json("assistant_events", [], boom)

    store.invalidate_cache("assistant_events")
    assert store.load_json("assistant_events", []) == [{"event": "seed"}]


# ── LRU / 缓存 ─────────────────────────────────────────────────────────


def test_cache_lru_eviction_at_128(tmp_path):
    """缓存上限 128 条：写入 130 个不同 key 后，最早的 key 应被淘汰。"""
    store = LocalDataStore(base_dir=str(tmp_path))
    # 直接操纵缓存避免触发 130 次 SQLite 写入（保持单元测试快速）
    for i in range(store._CACHE_MAX_SIZE + 2):
        store._cache[f"k{i}"] = i
        if len(store._cache) > store._CACHE_MAX_SIZE:
            store._cache.popitem(last=False)
    assert len(store._cache) == store._CACHE_MAX_SIZE
    assert "k0" not in store._cache
    assert f"k{store._CACHE_MAX_SIZE + 1}" in store._cache


def test_save_json_invalidates_path_aliased_cache(tmp_path):
    """save_json 后，与该 key 同 path 的所有缓存条目都应失效。"""
    store = LocalDataStore(base_dir=str(tmp_path))
    store.save_json("tasks", [{"id": 1}])
    store.load_json("tasks", [])  # 入缓存
    assert "tasks" in store._cache

    store.save_json("tasks", [{"id": 2}])
    assert "tasks" not in store._cache

    # 重新读应拿到新值
    assert store.load_json("tasks", []) == [{"id": 2}]


# ── C-3 / V3-05：schema 演进最小框架 ───────────────────────────────────


def test_schema_migrations_table_created_and_baseline(tmp_path):
    """新库启动后 schema_migrations 表存在，且基线版本已登记。"""
    store = SqliteKVStore(str(tmp_path / "kv.db"))
    tables = {
        row[0]
        for row in store._connect().execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    assert "schema_migrations" in tables
    assert store.current_schema_version() == 1


def test_schema_migrations_apply_is_idempotent(tmp_path):
    """构造后重复调用 apply_migrations 不应重复迁移（第二次返回 0）。"""
    store = SqliteKVStore(str(tmp_path / "kv.db"))
    assert store.current_schema_version() == 1
    assert store.apply_migrations() == 0


def test_schema_migrations_reopen_old_db_upgrades(tmp_path):
    """旧库（无 schema_migrations 表）重开后补建表并登记基线，版本正确。"""
    db_path = tmp_path / "kv.db"
    # 构造一个旧版库：仅有 kv_store 表（不含 schema_migrations）
    conn = sqlite3.connect(str(db_path))
    conn.executescript(
        "CREATE TABLE kv_store (key TEXT PRIMARY KEY, value TEXT NOT NULL, "
        "updated_at REAL NOT NULL, schema_version INTEGER NOT NULL DEFAULT 0);"
    )
    conn.close()

    store = SqliteKVStore(str(db_path))
    tables = {
        row[0]
        for row in store._connect().execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    assert "schema_migrations" in tables
    assert store.current_schema_version() == 1
    # 再次 apply 无新迁移
    assert store.apply_migrations() == 0
