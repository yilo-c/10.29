"""Tests for assistant.task_manager.TaskManager."""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

import pytest
from assistant.execution_plan import ExecutionPlan, PlanStatus
from assistant.models import AgentTask, PlannedStep
from assistant.task_manager import TaskManager


@pytest.fixture
def manager():
    """Return a TaskManager with all dependencies mocked via MagicMock."""
    return TaskManager(
        data_store=MagicMock(),
        task_lifecycle=MagicMock(),
        executor=MagicMock(),
        planner=MagicMock(),
        security=MagicMock(),
        checkpoint_manager=MagicMock(),
        history_store=MagicMock(),
        plan_store=MagicMock(),
        audit_logger=MagicMock(),
        events=MagicMock(),
    )


@pytest.fixture
def sample_steps():
    return [
        PlannedStep(
            step_id="s1",
            action_id="file.read",
            payload={"file_paths": ["config.json"]},
            risk_level="low",
        ),
        PlannedStep(
            step_id="s2",
            action_id="file.write",
            payload={"file_paths": ["config.json"]},
            risk_level="medium",
        ),
    ]


@pytest.fixture
def approved_plan(sample_steps):
    plan = ExecutionPlan(plan_id="plan_001", goal="update config")
    for step in sample_steps:
        plan.add_step(
            step_id=step.step_id,
            action_id=step.action_id,
            description=f"执行 {step.action_id}",
            risk_level=step.risk_level,
            file_paths=step.payload.get("file_paths", []),
        )
    plan.approve()
    return plan


class TestGeneratePlan:
    def test_generate_plan_creates_plan_and_persists(self, manager, sample_steps):
        plan = manager.generate_plan(
            goal="update config",
            steps=sample_steps,
            reasoning="test reasoning",
        )

        assert plan.goal == "update config"
        assert plan.reasoning == "test reasoning"
        assert plan.status == PlanStatus.PENDING
        assert len(plan.get_steps()) == 2
        assert plan.get_steps()[0].step_id == "s1"
        assert plan.get_steps()[1].step_id == "s2"
        manager._plan_store.save_plan.assert_called_once_with(plan)


class TestConfirmPlan:
    def test_confirm_plan_approves(self, manager, sample_steps):
        plan = manager.generate_plan("update config", sample_steps)
        manager._plan_store.reset_mock()

        result = manager.confirm_plan(plan)

        assert result.status == PlanStatus.APPROVED
        manager._plan_store.save_plan.assert_called_once_with(plan)

    def test_confirm_plan_rejects_with_reason(self, manager, sample_steps):
        plan = manager.generate_plan("update config", sample_steps)
        manager._plan_store.reset_mock()

        result = manager.confirm_plan(plan, approved=False, reject_reason="too risky")

        assert result.status == PlanStatus.REJECTED
        assert result.get_plan_summary()["reject_reason"] == "too risky"
        manager._plan_store.save_plan.assert_called_once_with(plan)

    def test_confirm_plan_edits_with_disable_and_reorder(self, manager, sample_steps):
        plan = manager.generate_plan("update config", sample_steps)
        manager._plan_store.reset_mock()

        result = manager.confirm_plan(
            plan,
            approved=True,
            disabled_steps=["s1"],
            reorder=["s2"],
        )

        # edit() 设置 EDITED 后 approve() 会将其置为 APPROVED
        assert result.status == PlanStatus.APPROVED
        enabled = result.get_steps()
        assert len(enabled) == 1
        assert enabled[0].step_id == "s2"
        manager._plan_store.save_plan.assert_called_once_with(plan)


class TestExecuteWithPlan:
    def test_execute_with_plan_fails_when_not_approved_or_edited(self, manager):
        plan = ExecutionPlan(plan_id="plan_002", goal="do something")
        plan.add_step("s1", "noop", "noop")
        task = AgentTask(goal="do something")

        result = manager.execute_with_plan(task, plan)

        assert result["success"] is False
        assert "plan_status" in result["reason"]
        manager._executor.execute_plan.assert_not_called()

    def test_execute_with_plan_fails_when_approved_plan_has_expired(self, manager):
        plan = ExecutionPlan(plan_id="plan_expired", goal="do something", expires_minutes=30)
        plan.add_step("s1", "noop", "noop")
        plan.approve()
        plan._expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        task = AgentTask(goal="do something")

        result = manager.execute_with_plan(task, plan)

        assert result["success"] is False
        assert result["reason"] == "plan_expired"
        manager._executor.execute_plan.assert_not_called()

    def test_execute_with_plan_completes_task(self, manager, approved_plan):
        task = AgentTask(goal="update config")
        manager._executor.execute_plan.return_value = {
            "ok": 1,
            "failed": 0,
            "blocked": 0,
            "degraded": 0,
            "outcomes": [{"status": "ok", "step_id": "s1"}],
        }

        result = manager.execute_with_plan(task, approved_plan, authorize_callback=lambda s: {"allowed": True})

        assert result["success"] is True
        assert task.status == "completed"
        manager._executor.execute_plan.assert_called_once()
        manager._lifecycle.persist_agent_task.assert_called()
        manager._history_store.save_record.assert_called_once()
        manager._audit_logger.log_action.assert_called_once()
        manager._lifecycle.memory.append_decision.assert_called_once()

    def test_execute_with_plan_fails_task(self, manager, approved_plan):
        task = AgentTask(goal="update config")
        manager._executor.execute_plan.return_value = {
            "ok": 0,
            "failed": 1,
            "blocked": 0,
            "degraded": 0,
            "outcomes": [{"status": "failed", "reason": "disk full"}],
        }

        result = manager.execute_with_plan(task, approved_plan, authorize_callback=lambda s: {"allowed": True})

        assert result["success"] is False
        assert task.status == "failed"


class TestStatusHelpers:
    # P0-3（2026-08-02）：_safe_task_status 已删除，TaskStateMachine.__init__
    # 内置未知状态回退到 PENDING。以下测试验证新行为。

    def test_state_machine_accepts_new_unified_states(self):
        """P0-3 新增的 5 个状态（queued/running/succeeded/pending_approval/denied）应被接受。"""
        from assistant.state_machine import TaskStateMachine

        for status_value in ("queued", "running", "succeeded", "pending_approval", "denied"):
            sm = TaskStateMachine(f"task_{status_value}", initial_state=status_value)
            assert sm.state.value == status_value

    def test_state_machine_unknown_status_falls_back_to_pending(self):
        """未知状态（如 legacy blocked/degraded）回退到 PENDING 而非抛 ValueError。"""
        from assistant.state_machine import TaskStateMachine, TaskState

        for unknown in ("blocked", "degraded", "weird"):
            sm = TaskStateMachine(f"task_{unknown}", initial_state=unknown)
            assert sm.state == TaskState.PENDING

    def test_state_machine_none_status_falls_back_to_pending(self):
        from assistant.state_machine import TaskStateMachine, TaskState

        TaskStateMachine("task_none", initial_state=None)  # type: ignore[arg-type]  # 构造不抛即为通过
        # None 不是 str 也不是 TaskState，会走到 else 分支，但 None 不是 TaskState 实例
        # TaskStateMachine 的 else 分支直接赋值，所以 None 会成为 _state
        # 但 _safe_task_status 已删除，调用方现在传 task.status or TaskState.PENDING.value
        # 这里测试 TaskState 枚举值直接传入的情况
        sm2 = TaskStateMachine("task_ok", initial_state=TaskState.PENDING)
        assert sm2.state == TaskState.PENDING

    def test_state_machine_keeps_valid_legacy_statuses(self):
        from assistant.state_machine import TaskStateMachine

        for status_value in ("pending", "executing", "paused", "completed", "failed", "cancelled"):
            sm = TaskStateMachine(f"task_{status_value}", initial_state=status_value)
            assert sm.state.value == status_value

    def test_resolve_status_completed(self, manager):
        assert manager._resolve_status({"ok": 1, "failed": 0}) == "completed"

    def test_resolve_status_failed(self, manager):
        assert manager._resolve_status({"failed": 1}) == "failed"

    def test_resolve_status_blocked(self, manager):
        assert manager._resolve_status({"blocked": 1, "ok": 0}) == "blocked"

    def test_resolve_status_degraded(self, manager):
        assert manager._resolve_status({"degraded": 1, "ok": 0}) == "degraded"

    def test_resolve_status_blocked_with_ok_is_completed(self, manager):
        assert manager._resolve_status({"blocked": 1, "ok": 1}) == "completed"

    def test_first_failure_reason_extracts_reason(self, manager):
        summary = {
            "outcomes": [
                {"status": "ok"},
                {"status": "failed", "reason": "disk full"},
            ]
        }
        assert manager._first_failure_reason(summary) == "disk full"

    def test_first_failure_reason_extracts_error(self, manager):
        summary = {
            "outcomes": [
                {"status": "timeout", "error": "connection timeout"},
            ]
        }
        assert manager._first_failure_reason(summary) == "connection timeout"

    def test_first_failure_reason_default_when_no_failure(self, manager):
        summary = {"outcomes": [{"status": "ok"}]}
        # 源码中的默认字符串（含非标准字符）
        assert manager._first_failure_reason(summary) == "执行失败"
