"""assistant_execution_history 单元测试。"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

import pytest
from assistant.execution_history import (
    ExecutionRecord,
    ExecutionHistoryStore,
    AuditLogWriter,
    SnapshotManager,
)


class TestExecutionRecord:
    """ExecutionRecord 测试。"""

    def test_create_record(self):
        record = ExecutionRecord(
            task_id="task_001",
            goal="测试任务",
            source="assistant.chat",
            triggered_by="user",
        )
        assert record.status == "pending"
        assert record.record_id is not None
        assert record.record_id != ""
        assert record.started_at is None

    def test_start(self):
        record = ExecutionRecord(task_id="t1", goal="g", source="s")
        record.start()
        assert record.status == "running"
        assert record.started_at is not None

    def test_complete(self):
        record = ExecutionRecord(task_id="t1", goal="g", source="s")
        record.start()
        record.complete({"ok": 3, "failed": 0})
        assert record.status == "completed"
        assert record.completed_at is not None
        assert record.summary["ok"] == 3

    def test_fail(self):
        record = ExecutionRecord(task_id="t1", goal="g", source="s")
        record.start()
        record.fail("网络错误")
        assert record.status == "failed"
        assert record.summary["error"] == "网络错误"

    def test_cancel(self):
        record = ExecutionRecord(task_id="t1", goal="g", source="s")
        record.start()
        record.cancel("用户取消")
        assert record.status == "cancelled"

    def test_to_dict_roundtrip(self):
        record = ExecutionRecord(task_id="t1", goal="测试", source="s")
        record.start()
        record.complete({"ok": 1})
        record.steps.append({"step_id": "s1", "status": "ok"})
        d = record.to_dict()
        restored = ExecutionRecord.from_dict(d)
        assert restored.task_id == "t1"
        assert restored.goal == "测试"
        assert restored.status == "completed"
        assert len(restored.steps) == 1


class TestExecutionHistoryStore:
    """ExecutionHistoryStore 测试。"""

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

    def test_save_and_get_record(self, data_store):
        store = ExecutionHistoryStore(data_store)
        record = ExecutionRecord(task_id="t1", goal="测试", source="s")
        record.start()
        record.complete({})
        store.save_record(record)
        loaded = store.get_record(record.record_id)
        assert loaded is not None
        assert loaded.task_id == "t1"
        assert loaded.status == "completed"

    def test_list_records(self, data_store):
        store = ExecutionHistoryStore(data_store)
        r1 = ExecutionRecord(task_id="t1", goal="g1", source="s")
        r1.start()
        r1.complete({})
        r2 = ExecutionRecord(task_id="t2", goal="g2", source="s")
        r2.start()
        r2.complete({})
        store.save_record(r1)
        store.save_record(r2)

        all_records = store.list_records()
        assert len(all_records) >= 2

        filtered = store.list_records(task_id="t1")
        assert len(filtered) >= 1
        assert all(r.task_id == "t1" for r in filtered)

    def test_get_replay_data(self, data_store):
        store = ExecutionHistoryStore(data_store)
        record = ExecutionRecord(task_id="t1", goal="测试", source="s")
        record.start()
        record.steps = [
            {"step_id": "s1", "action_id": "file.read", "status": "ok", "payload": {"briefing": "成功"}},
        ]
        record.complete({})
        store.save_record(record)

        replay = store.get_replay_data(record.record_id)
        assert replay is not None
        assert replay["total_steps"] == 1
        assert replay["steps"][0]["action_id"] == "file.read"

    def test_get_replay_data_not_found(self, data_store):
        store = ExecutionHistoryStore(data_store)
        assert store.get_replay_data("nonexistent") is None


class TestAuditLogWriter:
    """AuditLogWriter 审计日志测试。"""

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

    def test_log_action(self, data_store):
        writer = AuditLogWriter(data_store)
        writer.log_action(
            action_id="file.write",
            source="assistant",
            triggered_by="user",
            status="allowed",
            details={"path": "config.json"},
        )
        # data_store.update_json 应被调用
        assert data_store.update_json.called

    def test_get_logs_empty(self, data_store):
        writer = AuditLogWriter(data_store)
        logs = writer.get_logs()
        assert logs == []


class TestRollback:
    """回滚功能测试。"""

    def test_rollback_not_reversible(self, tmp_path):
        """不可逆的操作不能回滚（没有文件快照）。"""
        ds = MagicMock()
        store_data: list = []

        def load_json(key, default):
            return list(store_data)

        def update_json(key, default, updater):
            nonlocal store_data
            store_data = updater(list(store_data))
            return store_data

        ds.load_json.side_effect = load_json
        ds.update_json.side_effect = update_json

        store = ExecutionHistoryStore(ds)

        record = ExecutionRecord(task_id="t1", goal="只读操作", source="s")
        record.start()
        record.complete({"ok": 1})
        store.save_record(record)

        result = store.rollback_record(record.record_id)
        assert result["success"] is False
        # 没有文件快照，所以不能回滚
        assert result["reason"] in ("no_file_snapshots", "not_reversible")

    def test_rollback_non_existent(self, tmp_path):
        ds = MagicMock()
        ds.load_json.return_value = []
        store = ExecutionHistoryStore(ds)

        result = store.rollback_record("nonexistent")
        assert result["success"] is False
        assert result["reason"] == "record_not_found"
