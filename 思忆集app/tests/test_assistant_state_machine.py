"""assistant_state_machine 单元测试。"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

import pytest
from assistant.state_machine import (
    TaskState,
    TaskStateMachine,
    start_task,
    pause_task,
    resume_task,
    complete_task,
    fail_task,
    cancel_task,
)


class TestTaskStateMachine:
    """TaskStateMachine 基础功能测试。"""

    def test_initial_state(self):
        sm = TaskStateMachine("test_001")
        assert sm.state == TaskState.PENDING

    def test_initial_with_custom_state(self):
        sm = TaskStateMachine("test_002", initial_state=TaskState.EXECUTING)
        assert sm.state == TaskState.EXECUTING

    def test_legal_transitions_pending(self):
        sm = TaskStateMachine("test_003")
        legal = sm.legal_transitions()
        assert TaskState.EXECUTING in legal
        assert TaskState.CANCELLED in legal
        assert TaskState.COMPLETED not in legal

    def test_legal_transitions_executing(self):
        sm = TaskStateMachine("test_004")
        sm.transition(TaskState.EXECUTING)
        legal = sm.legal_transitions()
        assert TaskState.PAUSED in legal
        assert TaskState.COMPLETED in legal
        assert TaskState.FAILED in legal
        assert TaskState.CANCELLED in legal

    def test_legal_transitions_paused(self):
        sm = TaskStateMachine("test_005")
        sm.transition(TaskState.EXECUTING)
        sm.transition(TaskState.PAUSED)
        legal = sm.legal_transitions()
        assert TaskState.EXECUTING in legal
        assert TaskState.CANCELLED in legal

    def test_valid_transition(self):
        sm = TaskStateMachine("test_006")
        assert sm.transition(TaskState.EXECUTING, reason="开始") is True
        assert sm.state == TaskState.EXECUTING

    def test_invalid_transition(self):
        sm = TaskStateMachine("test_007")
        # pending → completed 是非法转换
        assert sm.transition(TaskState.COMPLETED) is False
        assert sm.state == TaskState.PENDING

    def test_terminal_state_blocks_transition(self):
        sm = TaskStateMachine("test_008")
        sm.transition(TaskState.EXECUTING)
        sm.transition(TaskState.COMPLETED)
        # 从 terminal state 不能再转换
        assert sm.transition(TaskState.PAUSED) is False

    def test_transition_history(self):
        sm = TaskStateMachine("test_009")
        sm.transition(TaskState.EXECUTING, reason="用户确认")
        sm.transition(TaskState.PAUSED, reason="用户暂停")
        sm.transition(TaskState.EXECUTING, reason="恢复执行")
        history = sm.history
        assert len(history) == 3
        assert history[0]["from"] == "pending"
        assert history[0]["to"] == "executing"
        assert history[1]["to"] == "paused"
        assert history[2]["to"] == "executing"

    def test_notify_listeners(self):
        sm = TaskStateMachine("test_010")
        events = []

        def listener(task_id, from_state, to_state, metadata):
            events.append((task_id, from_state, to_state))

        sm.add_listener(listener)
        sm.transition(TaskState.EXECUTING)
        assert len(events) == 1
        assert events[0][0] == "test_010"
        assert events[0][1] == TaskState.PENDING
        assert events[0][2] == TaskState.EXECUTING

    def test_remove_listener(self):
        sm = TaskStateMachine("test_011")
        events = []

        def listener(task_id, from_state, to_state, metadata):
            events.append((task_id, from_state, to_state))

        sm.add_listener(listener)
        sm.remove_listener(listener)
        sm.transition(TaskState.EXECUTING)
        assert len(events) == 0

    def test_can_transition_to(self):
        sm = TaskStateMachine("test_012")
        assert sm.can_transition_to(TaskState.EXECUTING) is True
        assert sm.can_transition_to(TaskState.COMPLETED) is False

    def test_to_dict(self):
        sm = TaskStateMachine("test_013")
        sm.transition(TaskState.EXECUTING)
        d = sm.to_dict()
        assert d["task_id"] == "test_013"
        assert d["state"] == "executing"
        assert len(d["transition_history"]) == 1


class TestConvenienceFunctions:
    """便捷转换函数测试。"""

    def test_start_task(self):
        sm = TaskStateMachine("test_101")
        assert start_task(sm) is True
        assert sm.state == TaskState.EXECUTING

    def test_pause_and_resume(self):
        sm = TaskStateMachine("test_102")
        start_task(sm)
        assert pause_task(sm) is True
        assert sm.state == TaskState.PAUSED
        assert resume_task(sm) is True
        assert sm.state == TaskState.EXECUTING

    def test_complete(self):
        sm = TaskStateMachine("test_103")
        start_task(sm)
        assert complete_task(sm) is True
        assert sm.state == TaskState.COMPLETED
        assert sm.state in TaskState.terminal()

    def test_fail(self):
        sm = TaskStateMachine("test_104")
        start_task(sm)
        assert fail_task(sm) is True
        assert sm.state == TaskState.FAILED

    def test_cancel_from_pending(self):
        sm = TaskStateMachine("test_105")
        assert cancel_task(sm) is True
        assert sm.state == TaskState.CANCELLED

    def test_cancel_from_executing(self):
        sm = TaskStateMachine("test_106")
        start_task(sm)
        assert cancel_task(sm) is True
        assert sm.state == TaskState.CANCELLED

    def test_cancel_from_paused(self):
        sm = TaskStateMachine("test_107")
        start_task(sm)
        pause_task(sm)
        assert cancel_task(sm) is True
        assert sm.state == TaskState.CANCELLED

    def test_cancel_from_completed_fails(self):
        sm = TaskStateMachine("test_108")
        start_task(sm)
        complete_task(sm)
        assert cancel_task(sm) is False

    def test_start_from_completed_fails(self):
        sm = TaskStateMachine("test_109")
        start_task(sm)
        complete_task(sm)
        assert start_task(sm) is False


class TestTaskStateEnum:
    """TaskState 枚举测试。"""

    def test_terminal_states(self):
        terminal = TaskState.terminal()
        assert TaskState.COMPLETED in terminal
        assert TaskState.FAILED in terminal
        assert TaskState.CANCELLED in terminal
        assert TaskState.PENDING not in terminal
        assert TaskState.EXECUTING not in terminal

    def test_active_states(self):
        active = TaskState.active()
        assert TaskState.PENDING in active
        assert TaskState.EXECUTING in active
        assert TaskState.PAUSED in active
        assert TaskState.COMPLETED not in active
