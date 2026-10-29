# -*- coding: utf-8 -*-
"""存储层事务保护。

提供：
- StorageTransaction: 上下文管理器，支持批量操作的原子性
- transactional 装饰器：自动包装方法为事务
- 回滚机制：操作失败时自动恢复
"""
from __future__ import annotations

import copy
import functools
import logging
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Callable, Generator, TypeVar

if TYPE_CHECKING:
    from data.local_storage import LocalDataStore

logger = logging.getLogger("siyi.core.storage_transaction")

F = TypeVar("F", bound=Callable[..., Any])


@dataclass
class StorageChange:
    """存储变更记录。"""

    key: str
    old_value: Any
    new_value: Any
    operation: str


@dataclass
class TransactionState:
    """事务状态。"""

    changes: list[StorageChange] = field(default_factory=list)
    is_active: bool = False
    is_committed: bool = False
    is_rolled_back: bool = False


class StorageTransaction:
    """存储层事务上下文管理器。

    提供批量操作的原子性：要么全部成功，要么全部回滚。

    用法：
        with StorageTransaction(data_store) as tx:
            tx.set("tasks", new_tasks)
            tx.set("notes", new_notes)
            # 如果这里抛出异常，所有更改都会回滚

        # 或者手动控制
        tx = StorageTransaction(data_store)
        tx.begin()
        try:
            tx.set("tasks", new_tasks)
            tx.commit()
        except Exception:
            tx.rollback()
            raise
    """

    def __init__(self, storage: "LocalDataStore"):
        """初始化事务。

        Args:
            storage: LocalDataStore 实例
        """
        self.storage = storage
        self._state = TransactionState()
        self._original_values: dict[str, Any] = {}

    def __enter__(self) -> "StorageTransaction":
        self.begin()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if exc_type is not None:
            # 异常时回滚
            self.rollback()
            logger.warning(
                "Transaction rolled back due to exception: %s",
                exc_val,
            )
            return None
        # 正常退出时，如果未手动提交则自动提交
        if not self._state.is_committed:
            self.commit()
            return None
        return None

    def begin(self) -> None:
        """开始事务。"""
        if self._state.is_active:
            raise RuntimeError("Transaction already active")
        self._state.is_active = True
        self._state.is_committed = False
        self._state.is_rolled_back = False
        self._state.changes.clear()
        self._original_values.clear()
        logger.debug("Transaction started")

    def commit(self) -> None:
        """提交事务。"""
        if self._state.is_rolled_back:
            # 已回滚的事务再提交为 noop（is_active=False 直接返回）
            return
        if not self._state.is_active:
            raise RuntimeError("No active transaction")
        if self._state.is_committed:
            raise RuntimeError("Transaction already committed")
        try:
            # 逐个应用变更
            for change in self._state.changes:
                if change.operation == "delete":
                    # delete 操作：new_value 为 None，表示删除
                    self.storage.save_json(change.key, change.new_value)
                else:
                    self.storage.save_json(change.key, change.new_value)
            self._state.is_committed = True
            self._state.is_active = False
            logger.debug(
                "Transaction committed: %d changes", len(self._state.changes)
            )
        except Exception as e:
            logger.error("Failed to commit transaction: %s", e)
            self.rollback()
            raise

    def rollback(self) -> None:
        """回滚事务。"""
        if not self._state.is_active:
            return
        # 恢复所有原始值
        for key, original_value in self._original_values.items():
            try:
                self.storage.save_json(key, original_value)
            except Exception as e:
                logger.error(
                    "Failed to rollback key %s: %s",
                    key,
                    e,
                )
        self._state.is_rolled_back = True
        self._state.is_active = False
        logger.debug(
            "Transaction rolled back: %d changes reverted",
            len(self._original_values),
        )

    def set(self, key: str, value: Any) -> None:
        """设置值。

        Args:
            key: 存储键
            value: 新值
        """
        if not self._state.is_active:
            raise RuntimeError("No active transaction")
        # 首次修改该键时记录原始值
        if key not in self._original_values:
            try:
                original = self.storage.load_json(key, None)
                self._original_values[key] = copy.deepcopy(original)
            except Exception:
                self._original_values[key] = None
        # 记录变更（不立即写入）
        self._state.changes.append(
            StorageChange(
                key=key,
                old_value=self._original_values.get(key),
                new_value=copy.deepcopy(value),
                operation="set",
            )
        )

    def update(self, key: str, updater: Callable[[Any], Any]) -> None:
        """更新值。

        Args:
            key: 存储键
            updater: 更新函数，接收当前值，返回新值
        """
        if not self._state.is_active:
            raise RuntimeError("No active transaction")
        # 读取当前值（优先用事务内已修改的值）
        try:
            current = self.storage.load_json(key, None)
        except Exception:
            current = None
        # 首次修改该键时记录原始值
        if key not in self._original_values:
            self._original_values[key] = copy.deepcopy(current)
        # 基于当前值的深拷贝应用更新
        new_value = updater(copy.deepcopy(current))
        # 记录变更
        self._state.changes.append(
            StorageChange(
                key=key,
                old_value=self._original_values.get(key),
                new_value=new_value,
                operation="update",
            )
        )

    def delete(self, key: str) -> None:
        """删除键。

        Args:
            key: 存储键
        """
        if not self._state.is_active:
            raise RuntimeError("No active transaction")
        # 首次修改该键时记录原始值
        if key not in self._original_values:
            try:
                original = self.storage.load_json(key, None)
                self._original_values[key] = copy.deepcopy(original)
            except Exception:
                self._original_values[key] = None
        # 记录变更（new_value 为 None 表示删除）
        self._state.changes.append(
            StorageChange(
                key=key,
                old_value=self._original_values.get(key),
                new_value=None,
                operation="delete",
            )
        )

    def get_changes(self) -> list[StorageChange]:
        """获取所有变更记录。"""
        return list(self._state.changes)

    @property
    def is_active(self) -> bool:
        """事务是否活跃。"""
        return self._state.is_active

    @property
    def change_count(self) -> int:
        """当前变更数量。"""
        return len(self._state.changes)


def transactional(storage_getter: Callable[..., "LocalDataStore"]) -> Callable[[F], F]:
    """事务装饰器。

    自动将方法包装在事务中，方法执行成功则提交，失败则回滚。

    用法：
        class MyService:
            def __init__(self, storage):
                self._storage = storage

            @transactional(lambda self: self._storage)
            def batch_update(self, storage):
                # storage 参数自动注入
                tx = StorageTransaction(storage)
                tx.set("key1", value1)
                tx.set("key2", value2)
                tx.commit()
    """

    def decorator(func: F) -> F:
        @functools.wraps(func)
        def wrapper(self, *args, **kwargs):
            storage = storage_getter(self)
            with transaction(storage) as tx:
                return func(self, tx, *args, **kwargs)

        return wrapper

    return decorator


@contextmanager
def transaction(storage: "LocalDataStore") -> Generator[StorageTransaction, None, None]:
    """事务上下文管理器工厂函数。

    用法：
        with transaction(data_store) as tx:
            tx.set("key", value)
    """
    tx = StorageTransaction(storage)
    tx.begin()
    try:
        yield tx
        tx.commit()
    except Exception:
        tx.rollback()
        raise


class BatchWriter:
    """批量写入器。

    收集多个写操作，然后一次性提交。

    用法：
        writer = BatchWriter(data_store)
        writer.set("tasks", new_tasks)
        writer.set("notes", new_notes)
        writer.flush()  # 一次性写入
    """

    def __init__(self, storage: "LocalDataStore", auto_flush_count: int = 10):
        """初始化批量写入器。

        Args:
            storage: LocalDataStore 实例
            auto_flush_count: 自动刷新的阈值（0 表示不自动刷新）
        """
        self.storage = storage
        self.auto_flush_count = auto_flush_count
        # 暂存的最终值（key -> value）
        self._pending: dict[str, Any] = {}
        self._change_count = 0

    def set(self, key: str, value: Any) -> None:
        """设置值（暂存）。

        Args:
            key: 存储键
            value: 新值
        """
        self._pending[key] = value
        self._change_count += 1
        # 达到阈值自动刷新
        if self.auto_flush_count > 0 and self._change_count >= self.auto_flush_count:
            self.flush()

    def update(self, key: str, updater: Callable[[Any], Any]) -> None:
        """更新值（暂存）。

        Args:
            key: 存储键
            updater: 更新函数
        """
        # 读取当前值（优先用已暂存的值）
        current = self._pending.get(key)
        if current is None:
            try:
                current = self.storage.load_json(key, None)
            except Exception:
                current = None
        # 应用更新并暂存
        self._pending[key] = updater(copy.deepcopy(current))
        self._change_count += 1
        # 达到阈值自动刷新
        if self.auto_flush_count > 0 and self._change_count >= self.auto_flush_count:
            self.flush()

    def flush(self) -> int:
        """刷新所有暂存的更改。

        Returns:
            刷新的更改数量
        """
        if not self._pending:
            return 0
        # 在单个事务中写入所有暂存的值
        count = len(self._pending)
        with transaction(self.storage) as tx:
            for key, value in self._pending.items():
                tx.set(key, value)
        # 清空暂存
        self._pending.clear()
        self._change_count = 0
        logger.debug("BatchWriter flushed %d changes", count)
        return count

    def discard(self) -> int:
        """丢弃所有暂存的更改。

        Returns:
            丢弃的更改数量
        """
        count = len(self._pending)
        self._pending.clear()
        self._change_count = 0
        return count

    @property
    def pending_count(self) -> int:
        """当前暂存的键数量。"""
        return len(self._pending)


__all__ = [
    "StorageChange",
    "TransactionState",
    "StorageTransaction",
    "BatchWriter",
    "transaction",
    "transactional",
]
