"""assistant_transaction 单元测试。"""
from __future__ import annotations

import sys
import os
from pathlib import Path
from unittest.mock import MagicMock

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

import pytest
from assistant.transaction import FileTransaction, SnapshotManager, FileSnapshot


class TestFileTransaction:
    """FileTransaction 事务测试。"""

    def test_empty_transaction(self):
        txn = FileTransaction("txn_001", description="空事务")
        result = txn.execute()
        assert result["success"] is True

    def test_single_step_success(self):
        txn = FileTransaction("txn_002")
        txn.add_step("step_1", lambda: {"status": "ok", "data": "完成"}, description="第一步")
        result = txn.execute()
        assert result["success"] is True
        assert result["total"] == 1
        assert result["executed"] == 1
        assert result["failed_steps"] == []
        assert result["rolled_back"] is False

    def test_multi_step_all_success(self):
        txn = FileTransaction("txn_003")
        txn.add_step("step_1", lambda: {"status": "ok"}, description="第一步")
        txn.add_step("step_2", lambda: {"status": "ok"}, description="第二步")
        txn.add_step("step_3", lambda: {"status": "ok"}, description="第三步")
        result = txn.execute()
        assert result["success"] is True
        assert result["total"] == 3
        assert result["executed"] == 3

    def test_step_failure_triggers_rollback(self):
        """步骤失败触发回滚。"""
        executed_steps = []

        def step_1():
            executed_steps.append(1)
            return {"status": "ok"}

        def step_2():
            executed_steps.append(2)
            raise RuntimeError("步骤 2 失败")

        txn = FileTransaction("txn_004")
        txn.add_step("step_1", step_1, description="第一步")
        txn.add_step("step_2", step_2, description="第二步")
        result = txn.execute()
        assert result["success"] is False
        assert len(result["failed_steps"]) == 1
        assert result["failed_steps"][0]["step_id"] == "step_2"
        assert result["rolled_back"] is True

    def test_to_dict(self):
        txn = FileTransaction("txn_005", description="测试事务")
        txn.add_step("step_1", lambda: {}, description="测试步骤")
        d = txn.to_dict()
        assert d["transaction_id"] == "txn_005"
        assert len(d["steps"]) == 1
        assert d["steps"][0]["step_id"] == "step_1"


class TestFileSnapshot:
    """FileSnapshot 文件快照测试。"""

    def test_snapshot_nonexistent_file(self, tmp_path):
        fp = str(tmp_path / "nonexistent.txt")
        snapshot = FileSnapshot(fp)
        snapshot.take_snapshot()
        assert snapshot.exists_before is False

    def test_take_and_restore(self, tmp_path):
        fp = str(tmp_path / "test.txt")
        Path(fp).write_text("original content", encoding="utf-8")
        snapshot = FileSnapshot(fp)
        snapshot.take_snapshot()
        # 修改文件
        Path(fp).write_text("modified content", encoding="utf-8")
        assert Path(fp).read_text(encoding="utf-8") == "modified content"
        # 恢复
        snapshot.restore()
        assert Path(fp).read_text(encoding="utf-8") == "original content"


class TestSnapshotManager:
    """SnapshotManager 测试。"""

    def test_snapshot_and_restore(self, tmp_path):
        fp = str(tmp_path / "snapshot_test.txt")
        Path(fp).write_text("data", encoding="utf-8")
        snapshots = SnapshotManager.snapshot_files([fp])
        assert fp in snapshots
        assert snapshots[fp]["exists_before"] is True
        assert snapshots[fp]["content_before"] == "data"

        # 修改后恢复
        Path(fp).write_text("modified", encoding="utf-8")
        SnapshotManager.restore_files(snapshots)
        assert Path(fp).read_text(encoding="utf-8") == "data"

    def test_snapshot_nonexistent(self, tmp_path):
        fp = str(tmp_path / "nonexistent.txt")
        snapshots = SnapshotManager.snapshot_files([fp])
        assert snapshots[fp]["exists_before"] is False

    def test_restore_with_error_handling(self):
        """恢复不存在的快照不应崩溃。"""
        failed = SnapshotManager.restore_files({})
        assert failed == []
