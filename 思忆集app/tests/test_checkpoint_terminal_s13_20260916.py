"""S1-3 代码面：checkpoint 终态直落 + internal 事件过滤 + waiting_since 处置。

对应八轮实测 J 节（163 条僵尸任务的上游生成机制）。三个缺陷各自的断言：

① 步数已满（completed_steps >= total_steps）的非终态 checkpoint：
   - 写侧：``save_checkpoint(total_steps=N)`` 在 executing 且步满时直接落
     ``completed`` 终态 → 天然不进 ``scan_unfinished_tasks``。
   - 读侧存量兼容：即使数据里滞留着 ``executing``（旧版本写入、没有收尾判定），
     scan 也按 checkpoint 自身 ``total_steps`` 过滤掉。

② 内部事件（goal = ``event:startup`` / ``event:shutdown`` / ``event:off_work``）：
   - 写侧自动打 ``internal: true``；
   - ``scan_unfinished_tasks()`` 默认过滤，``include_internal=True`` 恢复可见；
   - 存量兼容双保险：旧数据没有 internal 标记，goal 存在 assistant_agent_tasks
     的，按 ``event:`` 前缀 join 过滤。

③ ``waiting_since``：全仓 grep（后端 + desk-notes 前端）零写路径、零消费方，
   定夺为「不存在于本 schema」——断言任何写路径产出的 checkpoint 都不含该键。

④ 回归：``archive_old_checkpoints`` 行为不破（仍按天数归档、计数正确）。
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

import pytest

from assistant.checkpoint import CheckpointManager, DataStoreCheckpointAdapter


class MemoryStore:
    """内存 data_store，只需满足 load_json / update_json 契约。"""

    def __init__(self) -> None:
        self._kv: dict[str, Any] = {}

    def load_json(self, key: str, default: Any) -> Any:
        return self._kv.get(key, default)

    def update_json(self, key: str, default: Any, updater) -> Any:
        current = self._kv.get(key, default)
        result = updater(current)
        self._kv[key] = result
        return result


def make_manager(**kw) -> CheckpointManager:
    return CheckpointManager(MemoryStore(), **kw)


def task_ids(cps: list[dict[str, Any]]) -> set[str]:
    return {cp["task_id"] for cp in cps}


# ── ① 步满落终态，不进 unfinished ──────────────────────────────


class TestStepsFullGoesTerminal:
    def test_save_executing_steps_full_falls_to_completed(self):
        m = make_manager()
        cp = m.save_checkpoint(
            "t_full",
            state="executing",
            completed_steps=[0, 1],
            step_results=[{"ok": True}, {"ok": True}],
            total_steps=2,
        )
        assert cp["state"] == "completed"
        assert "t_full" not in task_ids(m.scan_unfinished_tasks())

    def test_save_executing_steps_not_full_stays_unfinished(self):
        m = make_manager()
        cp = m.save_checkpoint(
            "t_half",
            state="executing",
            completed_steps=[0],
            step_results=[{"ok": True}],
            total_steps=3,
        )
        assert cp["state"] == "executing"
        assert task_ids(m.scan_unfinished_tasks()) == {"t_half"}

    def test_total_steps_inferred_from_plan_steps(self):
        m = make_manager()
        cp = m.save_checkpoint(
            "t_plan",
            state="executing",
            completed_steps=[0, 1],
            step_results=[{}, {}],
            plan={"steps": ["s0", "s1"]},
        )
        assert cp["total_steps"] == 2
        assert cp["state"] == "completed"
        assert m.scan_unfinished_tasks() == []

    def test_paused_steps_full_not_force_completed(self):
        """paused 语义上可步满挂起，收尾判定只作用于 executing。"""
        m = make_manager()
        cp = m.save_checkpoint(
            "t_paused",
            state="paused",
            completed_steps=[0, 1],
            step_results=[{}, {}],
            total_steps=2,
        )
        assert cp["state"] == "paused"
        assert task_ids(m.scan_unfinished_tasks()) == {"t_paused"}

    def test_legacy_steps_full_executing_filtered_by_scan(self):
        """存量兼容：旧数据无收尾判定，滞留在 executing 2/2 → scan 过滤。"""
        m = make_manager()
        # 直接伪造一条 S1-3 之前写法的僵尸（绕过 save_checkpoint 判定）
        m._data_store.update_json(
            "assistant_checkpoints",
            [],
            lambda cps: (
                (list(cps) if isinstance(cps, list) else [])
                + [
                    {
                        "checkpoint_id": "zombie1",
                        "task_id": "t_zombie",
                        "state": "executing",
                        "completed_steps": [0, 1],
                        "step_results": [{}, {}],
                        "plan": {},
                        "total_steps": 2,
                        "created_at": "2026-05-01T00:00:00+00:00",
                        "version": 2,
                    }
                ]
            ),
        )
        assert m.scan_unfinished_tasks() == []
        # total_steps 未知时不猜测，保持可见（不误伤真未完成）
        m2 = make_manager()
        m2._data_store.update_json(
            "assistant_checkpoints",
            [],
            lambda cps: [
                {
                    "checkpoint_id": "unknown1",
                    "task_id": "t_unknown",
                    "state": "executing",
                    "completed_steps": [0, 1],
                    "step_results": [{}, {}],
                    "plan": {},
                    "created_at": "2026-05-01T00:00:00+00:00",
                    "version": 1,
                }
            ],
        )
        assert task_ids(m2.scan_unfinished_tasks()) == {"t_unknown"}

    def test_adapter_save_on_step_complete_forwards_total_steps(self):
        m = make_manager()
        adapter = DataStoreCheckpointAdapter(m)
        cp = adapter.save_on_step_complete(
            "t_adapter",
            state="executing",
            step_index=1,
            step_result={"status": "ok"},
            total_steps=2,
        )
        assert cp["state"] == "completed"
        assert m.scan_unfinished_tasks() == []


# ── ② 内部事件标记与过滤 ──────────────────────────────────────


class TestInternalEventFilter:
    @pytest.mark.parametrize("goal", ["event:startup", "event:shutdown", "event:off_work", "EVENT:Startup"])
    def test_internal_goal_autotagged_and_filtered(self, goal: str):
        m = make_manager()
        cp = m.save_checkpoint(
            "t_evt",
            state="executing",
            completed_steps=[0],
            step_results=[{}],
            total_steps=5,
            goal=goal,
        )
        assert cp["internal"] is True
        assert task_ids(m.scan_unfinished_tasks()) == set()
        assert task_ids(m.scan_unfinished_tasks(include_internal=True)) == {"t_evt"}

    def test_internal_flag_can_be_forced_explicit(self):
        m = make_manager()
        m.save_checkpoint(
            "t_forced",
            state="executing",
            completed_steps=[0],
            step_results=[{}],
            total_steps=5,
            goal="真实用户任务",
            internal=True,
        )
        assert m.scan_unfinished_tasks() == []
        assert task_ids(m.scan_unfinished_tasks(include_internal=True)) == {"t_forced"}

    def test_normal_goal_not_internal(self):
        m = make_manager()
        cp = m.save_checkpoint(
            "t_user",
            state="executing",
            completed_steps=[0],
            step_results=[{}],
            total_steps=5,
            goal="推荐几个 SaaS 工具",
        )
        assert cp["internal"] is False
        assert task_ids(m.scan_unfinished_tasks()) == {"t_user"}

    def test_plan_title_event_prefix_detected(self):
        m = make_manager()
        cp = m.save_checkpoint(
            "t_title",
            state="executing",
            completed_steps=[0],
            step_results=[{}],
            total_steps=5,
            plan={"title": "event:startup"},
        )
        assert cp["internal"] is True

    def test_legacy_checkpoint_without_internal_flag_filtered_by_taskstore_goal(self):
        """存量兼容双保险：旧 checkpoint 无 goal/internal 字段，
        但 assistant_agent_tasks 里 goal=event:startup → scan 按前缀 join 过滤。"""
        store = MemoryStore()
        store.update_json(
            "assistant_agent_tasks",
            [],
            lambda _x: [{"task_id": "0aeb08143599", "goal": "event:startup", "steps": [{}, {}]}],
        )
        store.update_json(
            "assistant_checkpoints",
            [],
            lambda _x: [
                {
                    "checkpoint_id": "legacy1",
                    "task_id": "0aeb08143599",
                    "state": "executing",
                    "completed_steps": [0, 1],
                    "step_results": [{}, {}],
                    "plan": {},
                    "created_at": "2026-05-01T00:00:00+00:00",
                    "version": 2,
                }
            ],
        )
        m = CheckpointManager(store)
        assert m.scan_unfinished_tasks() == []
        assert task_ids(m.scan_unfinished_tasks(include_internal=True)) == {"0aeb08143599"}

    def test_mixed_store_only_real_user_task_visible(self):
        m = make_manager()
        m.save_checkpoint("u1", state="executing", completed_steps=[0], step_results=[{}], total_steps=3, goal="写周报")
        m.save_checkpoint("e1", state="executing", completed_steps=[0], step_results=[{}], total_steps=3, goal="event:startup")
        m.save_checkpoint("e2", state="executing", completed_steps=[0], step_results=[{}], total_steps=3, goal="event:off_work")
        assert task_ids(m.scan_unfinished_tasks()) == {"u1"}
        assert task_ids(m.scan_unfinished_tasks(include_internal=True)) == {"u1", "e1", "e2"}

    def test_task_manager_and_info_layer_filter_internal(self):
        """消费链：TaskManager.scan_unfinished_tasks / get_unfinished_tasks_info
        默认同样过滤，include_internal 透传。"""
        from assistant.checkpoint import CheckpointManager as CM

        store = MemoryStore()
        m: CM = CheckpointManager(store)
        m.save_checkpoint("e1", state="executing", completed_steps=[0], step_results=[{}], total_steps=3, goal="event:startup")
        m.save_checkpoint("u1", state="executing", completed_steps=[0], step_results=[{}], total_steps=3, goal="真任务")

        # 只测 scan/get_unfinished_tasks_info 两层：手搓一个最小 TaskManager 替身
        from assistant.task_manager import TaskManager

        tm = TaskManager.__new__(TaskManager)
        tm._checkpoint_manager = m
        tm._data_store = store
        assert task_ids(tm.scan_unfinished_tasks()) == {"u1"}
        assert task_ids(tm.scan_unfinished_tasks(include_internal=True)) == {"u1", "e1"}
        info = tm.get_unfinished_tasks_info()
        assert [t["task_id"] for t in info] == ["u1"]
        assert len(tm.get_unfinished_tasks_info(include_internal=True)) == 2


# ── ③ waiting_since 处置：不存在（删除派） ─────────────────────


class TestWaitingSinceRemoved:
    def test_no_waiting_since_key_in_any_written_checkpoint(self):
        """waiting_since 定夺为删除：任何写路径（含 waiting_approval 态）
        产出的 checkpoint 都不得含该键——不留永不写的谎言字段。"""
        m = make_manager()
        cps = [
            m.save_checkpoint("w1", state="waiting_approval", completed_steps=[], step_results=[], goal="需要审批"),
            m.save_checkpoint("w2", state="executing", completed_steps=[0], step_results=[{}], total_steps=2),
            m.save_checkpoint("w3", state="executing", completed_steps=[0], step_results=[{}], goal="event:startup", total_steps=2),
        ]
        for cp in cps:
            assert "waiting_since" not in cp, f"{cp['task_id']} 不应含 waiting_since"
        persisted = m._data_store.load_json("assistant_checkpoints", [])
        for cp in persisted:
            assert "waiting_since" not in cp

    def test_waiting_approval_still_visible_and_carries_created_at(self):
        """删字段的替代可观测性：挂起态时间由 created_at 承担。"""
        m = make_manager()
        cp = m.save_checkpoint("w4", state="waiting_approval", completed_steps=[], step_results=[])
        assert "created_at" in cp
        assert cp["state"] in [c["state"] for c in m.scan_unfinished_tasks()]

    def test_waiting_since_absent_from_scan_payload_keys(self):
        m = make_manager()
        m.save_checkpoint("w5", state="waiting_approval", completed_steps=[], step_results=[])
        for cp in m.scan_unfinished_tasks():
            assert "waiting_since" not in cp


# ── ④ archive_old_checkpoints 回归 ────────────────────────────


class TestArchiveRegression:
    def test_archive_old_checkpoints_still_archives_by_days(self):
        m = make_manager()
        old = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
        fresh = datetime.now(timezone.utc).isoformat()

        def seed(cps: Any) -> list[dict[str, Any]]:
            return [
                {
                    "checkpoint_id": "a1",
                    "task_id": "old1",
                    "state": "executing",
                    "completed_steps": [],
                    "step_results": [],
                    "created_at": old,
                    "version": 1,
                },
                {
                    "checkpoint_id": "a2",
                    "task_id": "new1",
                    "state": "executing",
                    "completed_steps": [],
                    "step_results": [],
                    "created_at": fresh,
                    "version": 1,
                },
            ]

        m._data_store.update_json("assistant_checkpoints", [], seed)
        assert m.archive_old_checkpoints(before_days=7) == 1
        stored = m._data_store.load_json("assistant_checkpoints", [])
        by_id = {cp["checkpoint_id"]: cp for cp in stored}
        assert by_id["a1"].get("archived") is True
        assert not by_id["a2"].get("archived")
        # 归档后不进 unfinished（原有语义）
        assert task_ids(m.scan_unfinished_tasks()) == {"new1"}

    def test_archive_uses_default_days_and_survives_new_fields(self):
        """新增 internal/total_steps/goal 字段后归档路径不受影响。"""
        m = make_manager(archive_after_days=7)
        old_ts = (datetime.now(timezone.utc) - timedelta(days=10)).isoformat()
        cp = m.save_checkpoint("t_old_evt", state="executing", completed_steps=[0], step_results=[{}], goal="event:startup", total_steps=5)

        # 覆写 created_at 成过期时间
        def age(cps: Any) -> list[dict[str, Any]]:
            for item in cps:
                if item.get("checkpoint_id") == cp["checkpoint_id"]:
                    item["created_at"] = old_ts
            return list(cps)

        m._data_store.update_json("assistant_checkpoints", [], age)
        assert m.archive_old_checkpoints() == 1
        assert cp["internal"] is True
        assert m.scan_unfinished_tasks() == []

    def test_invalid_created_at_not_archived(self):
        m = make_manager()

        def seed(cps: Any) -> list[dict[str, Any]]:
            return [
                {
                    "checkpoint_id": "b1",
                    "task_id": "bad",
                    "state": "executing",
                    "completed_steps": [],
                    "step_results": [],
                    "created_at": "not-a-date",
                    "version": 1,
                },
            ]

        m._data_store.update_json("assistant_checkpoints", [], seed)
        assert m.archive_old_checkpoints(before_days=1) == 0
        assert task_ids(m.scan_unfinished_tasks()) == {"bad"}


# ── 综合：僵尸场景端到端复现 ──────────────────────────────────


class TestZombieScenario:
    def test_zombie_pattern_no_longer_accumulates(self):
        """复现 J 节场景：事件任务步满（2/2）→ 修复后既落终态又被过滤，
        unfinished 视图只剩真实未完成任务。"""
        m = make_manager()
        m.save_checkpoint("z1", state="executing", completed_steps=[0, 1], step_results=[{}, {}], total_steps=2, goal="event:startup")
        m.save_checkpoint("z2", state="executing", completed_steps=[0, 1], step_results=[{}, {}], total_steps=2, goal="event:shutdown")
        m.save_checkpoint("z3", state="executing", completed_steps=[0], step_results=[{}], total_steps=4, goal="event:off_work")
        m.save_checkpoint("real", state="executing", completed_steps=[1], step_results=[{}], total_steps=4, goal="打开微信发文件")
        assert task_ids(m.scan_unfinished_tasks()) == {"real"}

    def test_terminal_states_still_excluded(self):
        m = make_manager()
        m.save_checkpoint("c", state="completed", completed_steps=[0], step_results=[{}])
        m.save_checkpoint("f", state="failed", completed_steps=[0], step_results=[{}])
        m.save_checkpoint("x", state="cancelled", completed_steps=[0], step_results=[{}])
        m.save_checkpoint("r", state="recovering", completed_steps=[], step_results=[])
        assert task_ids(m.scan_unfinished_tasks()) == {"r"}
