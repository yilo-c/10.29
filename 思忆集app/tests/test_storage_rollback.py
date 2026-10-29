"""L7 一致性：存储层事务回滚测试（真实 LocalDataStore）。

聚焦事务原子性语义：
- 上下文管理器：正常退出自动提交 / 异常自动回滚
- 手动 begin/commit/rollback
- 状态机守卫（重复 begin、无事务操作、重复提交）
- transactional 装饰器（成功提交 / 失败回滚 / tx 注入）
- BatchWriter：flush 原子写入 / discard 丢弃 / auto_flush 阈值
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Callable

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

import pytest

from core.storage_transaction import (
    BatchWriter,
    StorageTransaction,
    transaction,
    transactional,
)
from data.local_storage import LocalDataStore


@pytest.fixture
def store(tmp_path) -> LocalDataStore:
    """真实 LocalDataStore（隔离目录）。"""
    ds = LocalDataStore(base_dir=str(tmp_path / "data"))
    yield ds
    ds.close()


class TestContextManager:
    def test_normal_exit_auto_commit(self, store: LocalDataStore) -> None:
        with StorageTransaction(store) as tx:
            tx.set("settings", {"lang": "zh"})
        assert store.load_json("settings", None) == {"lang": "zh"}

    def test_exception_rolls_back_all_changes(self, store: LocalDataStore) -> None:
        store.save_json("settings", {"lang": "en"})
        before = store.load_json("settings", None)
        with pytest.raises(RuntimeError):
            with StorageTransaction(store) as tx:
                tx.set("settings", {"lang": "ja"})
                tx.set("notes", [{"id": 1}])
                raise RuntimeError("boom")
        # 全部回滚：settings 复原，notes 未写入
        assert store.load_json("settings", None) == before
        assert store.load_json("notes", None) == []

    def test_multi_key_atomicity(self, store: LocalDataStore) -> None:
        """多键写在一个事务内：要么全写，要么全不写。"""
        with StorageTransaction(store) as tx:
            tx.set("tasks", [{"id": "a"}])
            tx.set("notes", [{"id": 1}])
        assert store.load_json("tasks", None) == [{"id": "a"}]
        assert store.load_json("notes", None) == [{"id": 1}]


class TestManualControl:
    def test_begin_commit(self, store: LocalDataStore) -> None:
        tx = StorageTransaction(store)
        tx.begin()
        tx.set("settings", {"lang": "fr"})
        assert tx.is_active is True
        tx.commit()
        assert store.load_json("settings", None) == {"lang": "fr"}

    def test_begin_rollback_restores_original(self, store: LocalDataStore) -> None:
        store.save_json("settings", {"lang": "de"})
        tx = StorageTransaction(store)
        tx.begin()
        tx.set("settings", {"lang": "es"})
        tx.rollback()
        # 回滚恢复原始值
        assert store.load_json("settings", None) == {"lang": "de"}

    def test_rollback_then_commit_is_noop(self, store: LocalDataStore) -> None:
        store.save_json("settings", {"lang": "de"})
        tx = StorageTransaction(store)
        tx.begin()
        tx.set("settings", {"lang": "es"})
        tx.rollback()
        tx.commit()  # 已回滚的事务提交为 noop（is_active=False 直接返回）
        assert store.load_json("settings", None) == {"lang": "de"}


class TestStateMachineGuards:
    def test_begin_twice_raises(self, store: LocalDataStore) -> None:
        tx = StorageTransaction(store)
        tx.begin()
        with pytest.raises(RuntimeError):
            tx.begin()

    def test_set_without_active_raises(self, store: LocalDataStore) -> None:
        tx = StorageTransaction(store)
        with pytest.raises(RuntimeError):
            tx.set("settings", {})

    def test_commit_without_active_raises(self, store: LocalDataStore) -> None:
        tx = StorageTransaction(store)
        with pytest.raises(RuntimeError):
            tx.commit()

    def test_commit_twice_raises(self, store: LocalDataStore) -> None:
        tx = StorageTransaction(store)
        tx.begin()
        tx.set("settings", {"lang": "it"})
        tx.commit()
        with pytest.raises(RuntimeError):
            tx.commit()

    def test_commit_failure_rolls_back(self, store: LocalDataStore, monkeypatch) -> None:
        """commit 中途失败（注入 save_json 异常）→ 已应用的变更全部回滚。"""
        store.save_json("settings", {"lang": "en"})
        original = store.save_json
        calls = [0]

        def failing_save(key: str, data: Any) -> None:
            calls[0] += 1
            if calls[0] == 2:  # 第二个键写入失败
                raise IOError("disk full")
            original(key, data)

        monkeypatch.setattr(store, "save_json", failing_save)
        tx = StorageTransaction(store)
        tx.begin()
        tx.set("notes", [{"id": 1}])
        tx.set("tasks", [{"id": "a"}])
        with pytest.raises(IOError):
            tx.commit()
        # 回滚恢复原值（settings 是原值、notes/tasks 为默认空）
        assert store.load_json("settings", None) == {"lang": "en"}
        assert store.load_json("notes", None) == []
        assert store.load_json("tasks", None) == []


class TestTransactionFactory:
    def test_contextmanager_factory_commit(self, store: LocalDataStore) -> None:
        with transaction(store) as tx:
            tx.set("settings", {"lang": "zh"})
        assert store.load_json("settings", None) == {"lang": "zh"}

    def test_contextmanager_factory_rollback(self, store: LocalDataStore) -> None:
        store.save_json("settings", {"lang": "en"})
        with pytest.raises(ValueError):
            with transaction(store) as tx:
                tx.set("settings", {"lang": "ko"})
                raise ValueError("bad")
        assert store.load_json("settings", None) == {"lang": "en"}


class TestTransactionalDecorator:
    def test_success_commits(self, store: LocalDataStore) -> None:
        class Svc:
            def __init__(self, storage: LocalDataStore) -> None:
                self._storage = storage

            @transactional(lambda self: self._storage)
            def batch(self, tx: StorageTransaction) -> None:
                tx.set("notes", [{"id": 1}])
                tx.set("tasks", [{"id": "a"}])

        Svc(store).batch()
        assert store.load_json("notes", None) == [{"id": 1}]
        assert store.load_json("tasks", None) == [{"id": "a"}]

    def test_failure_rolls_back(self, store: LocalDataStore) -> None:
        class Svc:
            def __init__(self, storage: LocalDataStore) -> None:
                self._storage = storage

            @transactional(lambda self: self._storage)
            def batch(self, tx: StorageTransaction) -> None:
                tx.set("notes", [{"id": 1}])
                raise ValueError("boom")

        with pytest.raises(ValueError):
            Svc(store).batch()
        assert store.load_json("notes", None) == []

    def test_injects_tx_with_positional_args(self, store: LocalDataStore) -> None:
        """装饰器把事务作为第一个位置参数注入，原参数顺延。"""
        class Svc:
            def __init__(self, storage: LocalDataStore) -> None:
                self._storage = storage

            @transactional(lambda self: self._storage)
            def save_note(self, tx: StorageTransaction, note_id: int) -> int:
                tx.set("notes", [{"id": note_id}])
                return note_id

        ret = Svc(store).save_note(42)
        assert ret == 42
        assert store.load_json("notes", None) == [{"id": 42}]


class TestBatchWriter:
    def test_flush_writes_atomically(self, store: LocalDataStore) -> None:
        w = BatchWriter(store)
        w.set("notes", [{"id": 1}])
        w.set("tasks", [{"id": "a"}])
        assert w.pending_count == 2
        assert store.load_json("notes", None) == []  # 尚未写入
        assert w.flush() == 2
        assert store.load_json("notes", None) == [{"id": 1}]
        assert store.load_json("tasks", None) == [{"id": "a"}]
        assert w.pending_count == 0

    def test_discard_drops_pending(self, store: LocalDataStore) -> None:
        w = BatchWriter(store)
        w.set("notes", [{"id": 1}])
        assert w.discard() == 1
        assert w.pending_count == 0
        assert store.load_json("notes", None) == []  # 从未写入

    def test_flush_empty_returns_zero(self, store: LocalDataStore) -> None:
        assert BatchWriter(store).flush() == 0

    def test_auto_flush_threshold(self, store: LocalDataStore) -> None:
        w = BatchWriter(store, auto_flush_count=3)
        w.set("a", 1)
        w.set("b", 2)
        assert store.load_json("a", None) is None  # 未达阈值
        w.set("c", 3)  # 触发自动 flush
        assert store.load_json("a", None) == 1
        assert store.load_json("b", None) == 2
        assert store.load_json("c", None) == 3
        assert w.pending_count == 0

    def test_update_reads_latest_for_pending(self, store: LocalDataStore) -> None:
        store.save_json("counter", {"n": 1})
        w = BatchWriter(store)
        w.update("counter", lambda cur: {"n": (cur or {}).get("n", 0) + 1})
        w.update("counter", lambda cur: {"n": (cur or {}).get("n", 0) + 1})
        w.flush()
        assert store.load_json("counter", None) == {"n": 3}


class TestChangeRecords:
    def test_get_changes_records_operations(self, store: LocalDataStore) -> None:
        tx = StorageTransaction(store)
        tx.begin()
        tx.set("settings", {"lang": "zh"})
        tx.update("notes", lambda cur: [{"id": 1}])
        tx.delete("tasks")
        changes = tx.get_changes()
        assert len(changes) == 3
        assert [c.operation for c in changes] == ["set", "update", "delete"]
        assert changes[0].key == "settings"
        assert changes[0].new_value == {"lang": "zh"}
        tx.rollback()
