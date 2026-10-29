"""assistant_checkpoint 单元测试。"""
from __future__ import annotations

import sys
import json
from pathlib import Path
from unittest.mock import MagicMock

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

import pytest
from assistant.checkpoint import CheckpointManager, DataStoreCheckpointAdapter


class TestCheckpointManager:
    """CheckpointManager 基础功能测试。"""

    @pytest.fixture
    def data_store(self):
        """模拟 data_store（内存存储）。"""
        ds = MagicMock()
        store: list = []

        def load_json(key, default):
            return list(store)

        def update_json(key, default, updater):
            nonlocal store
            store = updater(list(store))
            return store

        ds.load_json.side_effect = load_json
        ds.update_json.side_effect = update_json
        return ds

    @pytest.fixture
    def manager(self, data_store):
        return CheckpointManager(data_store, max_versions=3)

    def test_save_checkpoint(self, manager):
        cp = manager.save_checkpoint(
            task_id="task_001",
            state="executing",
            completed_steps=[0],
            step_results=[{"status": "ok"}],
        )
        assert cp["task_id"] == "task_001"
        assert cp["state"] == "executing"
        assert cp["completed_steps"] == [0]
        assert cp["version"] >= 1
        assert "checkpoint_id" in cp
        assert "created_at" in cp

    def test_save_checkpoint_requires_task_id(self, manager):
        with pytest.raises(Exception):
            manager.save_checkpoint(task_id="", state="executing", completed_steps=[], step_results=[])

    def test_get_task_checkpoints(self, manager):
        # 保存两个 checkpoint
        manager.save_checkpoint("task_002", state="executing", completed_steps=[0], step_results=[{"status": "ok"}])
        manager.save_checkpoint("task_002", state="executing", completed_steps=[0, 1], step_results=[{"s": "ok"}, {"s": "ok"}])

        cps = manager.get_task_checkpoints("task_002")
        assert len(cps) == 2
        # 按版本降序
        assert cps[0]["version"] > cps[1]["version"]

    def test_get_latest_checkpoint(self, manager):
        manager.save_checkpoint("task_003", state="executing", completed_steps=[0], step_results=[{}])
        manager.save_checkpoint("task_003", state="executing", completed_steps=[0, 1], step_results=[{}, {}])
        latest = manager.get_latest_checkpoint("task_003")
        assert latest is not None
        assert latest["version"] == 2
        assert latest["completed_steps"] == [0, 1]

    def test_get_latest_nonexistent(self, manager):
        assert manager.get_latest_checkpoint("nonexistent") is None

    def test_scan_unfinished_tasks(self, manager):
        # 未完成任务
        manager.save_checkpoint("task_unfinished", state="executing", completed_steps=[0], step_results=[{}])
        # 已完成任务
        manager.save_checkpoint("task_done", state="completed", completed_steps=[0, 1], step_results=[{}, {}])

        unfinished = manager.scan_unfinished_tasks()
        task_ids = [cp["task_id"] for cp in unfinished]
        assert "task_unfinished" in task_ids
        assert "task_done" not in task_ids

    def test_version_retention(self, data_store):
        """验证旧版本被归档。"""
        manager = CheckpointManager(data_store, max_versions=2)
        for i in range(4):
            manager.save_checkpoint("task_retention", state="executing", completed_steps=list(range(i + 1)), step_results=[{}] * (i + 1))

        cps = manager.get_task_checkpoints("task_retention", include_archived=True)
        archived = [cp for cp in cps if cp.get("archived")]
        assert len(archived) >= 1


class TestDataStoreCheckpointAdapter:
    """DataStoreCheckpointAdapter 测试。"""

    @pytest.fixture
    def adapter(self):
        """创建 adapter 并使用内存存储。"""
        ds = MagicMock()
        store: list = []

        def load_json(key, default):
            return list(store)

        def update_json(key, default, updater):
            nonlocal store
            store = updater(list(store))
            return store

        ds.load_json.side_effect = load_json
        ds.update_json.side_effect = update_json
        manager = CheckpointManager(ds, max_versions=5)
        return DataStoreCheckpointAdapter(manager)

    def test_save_on_step_complete(self, adapter):
        cp = adapter.save_on_step_complete(
            task_id="task_adapter",
            state="executing",
            step_index=2,
            step_result={"status": "ok", "briefing": "第三步完成"},
            previous_results=[{"status": "ok"}, {"status": "ok"}],
        )
        assert cp is not None
        assert cp["completed_steps"] == [0, 1, 2]
        assert len(cp["step_results"]) == 3

    def test_restore_and_get_remaining(self, adapter):
        adapter.save_on_step_complete("task_remain", "executing", 0, {"status": "ok"})
        cp, remaining = adapter.restore_and_get_remaining("task_remain", total_steps=3)
        assert cp is not None
        assert remaining == [1, 2]  # 步骤 0 已完成

    def test_restore_and_get_remaining_no_checkpoint(self, adapter):
        cp, remaining = adapter.restore_and_get_remaining("nonexistent", total_steps=5)
        assert cp is None
        assert remaining == [0, 1, 2, 3, 4]
