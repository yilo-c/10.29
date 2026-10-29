"""L7 一致性：checkpoint 崩溃恢复测试（真实 LocalDataStore + 重启模拟）。

聚焦崩溃恢复语义（区别于 test_assistant_checkpoint.py 的内存 Mock 基础功能）：
- 持久化：checkpoint 落盘后新建 manager 实例（模拟崩溃重启）可从磁盘读回
- 重启后 scan_unfinished_tasks 能发现未完成任务
- 重启后 recover_task 返回完整上下文（completed_steps / step_results / context_snapshot）
- 终端状态（completed/failed/cancelled）不进入恢复候选
- 多任务、多版本并存时取最新版本
- DataStoreCheckpointAdapter 跨重启恢复剩余步骤
"""
from __future__ import annotations

import sys
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

import pytest

from assistant.checkpoint import CheckpointManager, DataStoreCheckpointAdapter
from data.local_storage import LocalDataStore


@pytest.fixture
def base_dir(tmp_path) -> str:
    return str(tmp_path / "data")


def make_manager(base_dir: str, **kw) -> CheckpointManager:
    """新实例 = 模拟一次进程重启（从磁盘读回状态）。"""
    ds = LocalDataStore(base_dir=base_dir)
    return CheckpointManager(ds, **kw)


class TestCrashRestartRecovery:
    def test_checkpoint_survives_restart(self, base_dir: str) -> None:
        """崩溃前保存 → 重启后最新 checkpoint 仍在且数据完整。"""
        m1 = make_manager(base_dir)
        cp = m1.save_checkpoint(
            "task_a",
            state="executing",
            completed_steps=[0, 1],
            step_results=[{"step": 0, "status": "ok"}, {"step": 1, "status": "ok"}],
            context_snapshot={"focus_topics": ["学习"]},
            plan={"steps": ["s0", "s1", "s2", "s3"]},
        )
        checkpoint_id = cp["checkpoint_id"]

        m2 = make_manager(base_dir)  # 重启
        latest = m2.get_latest_checkpoint("task_a")
        assert latest is not None
        assert latest["checkpoint_id"] == checkpoint_id
        assert latest["completed_steps"] == [0, 1]
        assert latest["step_results"][1] == {"step": 1, "status": "ok"}
        assert latest["context_snapshot"] == {"focus_topics": ["学习"]}
        assert latest["plan"]["steps"] == ["s0", "s1", "s2", "s3"]

    def test_scan_finds_unfinished_after_restart(self, base_dir: str) -> None:
        m1 = make_manager(base_dir)
        m1.save_checkpoint("task_run", state="executing", completed_steps=[0], step_results=[{}])

        m2 = make_manager(base_dir)  # 重启
        found = m2.scan_unfinished_tasks()
        assert [cp["task_id"] for cp in found] == ["task_run"]
        assert found[0]["state"] == "executing"

    def test_terminal_states_excluded_after_restart(self, base_dir: str) -> None:
        m1 = make_manager(base_dir)
        m1.save_checkpoint("t_completed", state="completed", completed_steps=[0], step_results=[{}])
        m1.save_checkpoint("t_failed", state="failed", completed_steps=[0], step_results=[{}])
        m1.save_checkpoint("t_cancelled", state="cancelled", completed_steps=[0], step_results=[{}])
        m1.save_checkpoint("t_executing", state="executing", completed_steps=[0], step_results=[{}])

        m2 = make_manager(base_dir)
        found = m2.scan_unfinished_tasks()
        assert [cp["task_id"] for cp in found] == ["t_executing"]

    def test_recover_returns_full_context_after_restart(self, base_dir: str) -> None:
        m1 = make_manager(base_dir)
        m1.save_checkpoint(
            "task_b",
            state="executing",
            completed_steps=[0, 1, 2],
            step_results=[{"step": i, "data": f"r{i}"} for i in range(3)],
            context_snapshot={"weather": "晴", "location": "北京"},
        )

        m2 = make_manager(base_dir)
        cp = m2.recover_task("task_b")
        assert cp is not None
        assert cp["completed_steps"] == [0, 1, 2]
        assert cp["context_snapshot"]["location"] == "北京"
        # 未完成任务存在 → 可续跑
        assert cp["state"] == "executing"

    def test_recover_missing_task_after_restart(self, base_dir: str) -> None:
        assert make_manager(base_dir).recover_task("ghost") is None

    def test_multiple_tasks_isolated_recovery(self, base_dir: str) -> None:
        m1 = make_manager(base_dir)
        m1.save_checkpoint("ta", state="executing", completed_steps=[0], step_results=[{}])
        m1.save_checkpoint("tb", state="executing", completed_steps=[0, 1], step_results=[{}, {}])

        m2 = make_manager(base_dir)
        a = m2.recover_task("ta")
        b = m2.recover_task("tb")
        assert a is not None and a["completed_steps"] == [0]
        assert b is not None and b["completed_steps"] == [0, 1]
        assert {cp["task_id"] for cp in m2.scan_unfinished_tasks()} == {"ta", "tb"}

    def test_latest_version_wins_after_restart(self, base_dir: str) -> None:
        m1 = make_manager(base_dir)
        m1.save_checkpoint("task_v", state="executing", completed_steps=[0], step_results=[{"v": 1}])
        m1.save_checkpoint("task_v", state="executing", completed_steps=[0, 1], step_results=[{"v": 1}, {"v": 2}])

        m2 = make_manager(base_dir)
        latest = m2.get_latest_checkpoint("task_v")
        assert latest is not None
        assert latest["version"] == 2
        assert latest["completed_steps"] == [0, 1]


class TestRecoveryLifecycle:
    def test_archive_excludes_from_recovery(self, base_dir: str) -> None:
        """max_versions 超限时旧版本归档 → 不进入未完成任务扫描。

        归档条件：新版本总是最新（保留），已有版本按版本号降序只保留
        ``max_versions - 1`` 个，其余标记 archived。因此 max_versions=1 时
        保存第 2 版即归档 v1；仅存 1 版不会触发归档路径。
        """
        m1 = make_manager(base_dir, max_versions=1)
        m1.save_checkpoint("task_arc", state="executing", completed_steps=[0], step_results=[{}])
        m1.save_checkpoint("task_arc", state="executing", completed_steps=[0, 1], step_results=[{}, {}])
        m1.save_checkpoint("task_arc", state="executing", completed_steps=[0, 1, 2], step_results=[{}, {}, {}])

        m2 = make_manager(base_dir, max_versions=1)
        # 未完成任务扫描只取每个任务的最新版本；归档的 v1 不进候选
        found = m2.scan_unfinished_tasks()
        assert len(found) == 1
        assert found[0]["version"] == 3
        # 归档确实发生：v1 被标记 archived
        archived_versions = {
            cp["version"]
            for cp in m2.get_task_checkpoints("task_arc", include_archived=True)
            if cp.get("archived")
        }
        assert 1 in archived_versions

    def test_delete_task_checkpoints_prevents_recovery(self, base_dir: str) -> None:
        m1 = make_manager(base_dir)
        m1.save_checkpoint("task_del", state="executing", completed_steps=[0], step_results=[{}])
        m1.delete_task_checkpoints("task_del")

        m2 = make_manager(base_dir)
        assert m2.recover_task("task_del") is None
        assert m2.scan_unfinished_tasks() == []

    def test_empty_store_scan_after_restart(self, base_dir: str) -> None:
        m1 = make_manager(base_dir)
        assert m1.scan_unfinished_tasks() == []
        m2 = make_manager(base_dir)
        assert m2.scan_unfinished_tasks() == []


class TestAdapterCrashRecovery:
    def test_restore_remaining_steps_after_restart(self, base_dir: str) -> None:
        m1 = make_manager(base_dir)
        m1.save_checkpoint(
            "task_adapter",
            state="executing",
            completed_steps=[0, 1],
            step_results=[{"step": 0}, {"step": 1}],
        )

        # 崩溃重启：新 adapter 从磁盘恢复
        m2 = make_manager(base_dir)
        adapter = DataStoreCheckpointAdapter(m2)
        cp, remaining = adapter.restore_and_get_remaining("task_adapter", total_steps=5)
        assert cp is not None
        assert remaining == [2, 3, 4]  # 只重跑未完成的步骤

    def test_no_checkpoint_returns_all_remaining(self, base_dir: str) -> None:
        adapter = DataStoreCheckpointAdapter(make_manager(base_dir))
        cp, remaining = adapter.restore_and_get_remaining("never_started", total_steps=4)
        assert cp is None
        assert remaining == [0, 1, 2, 3]

    def test_save_then_recover_roundtrip(self, base_dir: str) -> None:
        m1 = make_manager(base_dir)
        adapter1 = DataStoreCheckpointAdapter(m1)
        adapter1.save_on_step_complete(
            "task_rt",
            state="executing",
            step_index=2,
            step_result={"status": "ok", "summary": "第三步完成"},
            previous_results=[{"status": "ok"}, {"status": "ok"}],
            context_snapshot={"profile": "p1"},
        )

        adapter2 = DataStoreCheckpointAdapter(make_manager(base_dir))
        cp, remaining = adapter2.restore_and_get_remaining("task_rt", total_steps=4)
        assert cp is not None
        assert cp["completed_steps"] == [0, 1, 2]
        assert len(cp["step_results"]) == 3
        assert cp["context_snapshot"] == {"profile": "p1"}
        assert remaining == [3]
