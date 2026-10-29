"""任务状态机：定义状态、合法转换矩阵、状态变更事件通知。

参考：
- LangGraph StateGraph 的有状态图设计
- Windsurf Flow 的状态持久化
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime, timezone
from enum import Enum
from typing import Any

logger = logging.getLogger("siyi.assistant.state_machine")


# ── 状态定义 ────────────────────────────────────────────────

class TaskState(str, Enum):
    PENDING = "pending"          # 已创建，等待执行
    EXECUTING = "executing"      # 执行中
    PAUSED = "paused"            # 暂停（用户手动暂停 / 等待输入）
    COMPLETED = "completed"      # 全部步骤执行成功
    FAILED = "failed"            # 执行失败（不可自动恢复）
    CANCELLED = "cancelled"      # 用户取消

    # P0-3（2026-08-02）：并入 automation_hub tool_runs 状态词表，
    # 消除 _safe_task_status 的 special-case 映射。
    # 语义对应：queued=已入队待执行 / running≈EXECUTING / succeeded≈COMPLETED
    #          pending_approval=等待审批 / denied=审批拒绝（终端态）
    QUEUED = "queued"                  # 已入队，等待 worker 取走执行
    RUNNING = "running"                # worker 正在执行（与 EXECUTING 语义等价，命名对齐 automation_hub）
    SUCCEEDED = "succeeded"            # 执行成功（与 COMPLETED 语义等价，命名对齐 automation_hub）
    PENDING_APPROVAL = "pending_approval"  # 等待人工审批决策
    DENIED = "denied"                  # 审批被拒绝（终端态）

    @classmethod
    def terminal(cls) -> set[TaskState]:
        """终端状态：到达后不再转换。"""
        return {cls.COMPLETED, cls.FAILED, cls.CANCELLED, cls.SUCCEEDED, cls.DENIED}

    @classmethod
    def active(cls) -> set[TaskState]:
        """活跃状态：任务仍在进行中。"""
        return {cls.PENDING, cls.EXECUTING, cls.PAUSED, cls.QUEUED, cls.RUNNING, cls.PENDING_APPROVAL}


# ── 状态转换矩阵 ────────────────────────────────────────────
# {from_state: {to_state: reason_hint}}

STATE_TRANSITIONS: dict[TaskState, dict[TaskState, str]] = {
    TaskState.PENDING: {
        TaskState.EXECUTING: "任务开始执行",
        TaskState.QUEUED: "任务入队等待 worker",
        TaskState.CANCELLED: "用户取消（尚未执行）",
    },
    TaskState.QUEUED: {
        TaskState.RUNNING: "worker 取走任务开始执行",
        TaskState.EXECUTING: "直接开始执行（兼容旧路径）",
        TaskState.PENDING_APPROVAL: "任务需要审批",
        TaskState.FAILED: "入队失败（P0-4 看门狗）",
        TaskState.CANCELLED: "用户取消（排队中）",
    },
    TaskState.PENDING_APPROVAL: {
        TaskState.QUEUED: "审批通过，重新入队执行",
        TaskState.DENIED: "审批被拒绝",
        TaskState.CANCELLED: "用户取消（待审批）",
    },
    TaskState.EXECUTING: {
        TaskState.PAUSED: "暂停执行",
        TaskState.COMPLETED: "全部步骤执行成功",
        TaskState.SUCCEEDED: "执行成功（automation_hub 命名）",
        TaskState.FAILED: "执行失败，不可自动恢复",
        TaskState.CANCELLED: "用户取消执行中任务",
    },
    TaskState.RUNNING: {
        TaskState.PAUSED: "暂停执行",
        TaskState.SUCCEEDED: "执行成功",
        TaskState.COMPLETED: "执行成功（兼容旧路径）",
        TaskState.FAILED: "执行失败，不可自动恢复",
        TaskState.CANCELLED: "用户取消执行中任务",
    },
    TaskState.PAUSED: {
        TaskState.EXECUTING: "恢复执行",
        TaskState.RUNNING: "恢复执行（automation_hub 命名）",
        TaskState.CANCELLED: "用户取消（暂停态）",
    },
    TaskState.COMPLETED: {},      # 终端状态
    TaskState.SUCCEEDED: {},      # 终端状态
    TaskState.FAILED: {},         # 终端状态
    TaskState.DENIED: {},         # 终端状态
    TaskState.CANCELLED: {},      # 终端状态
}


# ── 事件监听器 ──────────────────────────────────────────────

StateChangeListener = Callable[[str, TaskState, TaskState, dict[str, Any]], None]
"""(task_id, from_state, to_state, metadata) → None"""


# ── 状态机核心 ──────────────────────────────────────────────

class TaskStateMachine:
    """任务状态机：管理状态转换和事件通知。

    用法:
        sm = TaskStateMachine(task_id="task_001")
        sm.transition(TaskState.EXECUTING, reason="用户确认执行")
    """

    def __init__(self, task_id: str, initial_state: TaskState | str = TaskState.PENDING):
        self._task_id = task_id
        # P0-3：未知状态字符串（如 legacy "blocked"/"degraded"）回退到 PENDING 并记录警告，
        # 不再依赖 _safe_task_status 的 special-case 映射。
        if isinstance(initial_state, str):
            try:
                self._state = TaskState(initial_state)
            except ValueError:
                logger.warning(
                    "未知任务状态 %r，回退到 PENDING（任务 %s）",
                    initial_state, task_id,
                )
                self._state = TaskState.PENDING
        else:
            self._state = initial_state
        self._listeners: list[StateChangeListener] = []
        self._transition_history: list[dict[str, Any]] = []

    @property
    def state(self) -> TaskState:
        return self._state

    @property
    def history(self) -> list[dict[str, Any]]:
        return list(self._transition_history)

    def add_listener(self, listener: StateChangeListener) -> None:
        """注册状态变更监听器。"""
        self._listeners.append(listener)

    def remove_listener(self, listener: StateChangeListener) -> None:
        if listener in self._listeners:
            self._listeners.remove(listener)

    def can_transition_to(self, target: TaskState | str) -> bool:
        """检查到目标状态的转换是否合法。"""
        target_state = TaskState(target) if isinstance(target, str) else target
        return target_state in STATE_TRANSITIONS.get(self._state, {})

    def legal_transitions(self) -> list[TaskState]:
        """返回当前状态下所有合法的目标状态。"""
        return list(STATE_TRANSITIONS.get(self._state, {}).keys())

    def transition(
        self,
        target: TaskState | str,
        *,
        reason: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> bool:
        """执行状态转换。成功返回 True，失败（非法转换）返回 False。"""
        target_state = TaskState(target) if isinstance(target, str) else target

        if self._state in TaskState.terminal():
            logger.warning(
                "任务 %s 已是终端状态 %s，忽略转换到 %s",
                self._task_id, self._state.value, target_state.value,
            )
            return False

        if not self.can_transition_to(target_state):
            logger.warning(
                "非法状态转换: %s → %s （任务 %s）",
                self._state.value, target_state.value, self._task_id,
            )
            return False

        from_state = self._state
        self._state = target_state

        record = {
            "from": from_state.value,
            "to": target_state.value,
            "reason": reason or STATE_TRANSITIONS.get(from_state, {}).get(target_state, ""),
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "metadata": dict(metadata or {}),
        }
        self._transition_history.append(record)

        # 通知监听器
        for listener in self._listeners:
            try:
                listener(self._task_id, from_state, target_state, record)
            except Exception as exc:
                logger.exception("状态变更监听器异常: %s", exc)

        logger.info(
            "状态转换: %s [%s → %s] %s",
            self._task_id, from_state.value, target_state.value,
            record["reason"],
        )
        return True

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self._task_id,
            "state": self._state.value,
            "transition_history": self._transition_history,
        }


# ── 便捷转换方法 ────────────────────────────────────────────

# 这些方法封装常用转换，提供更语义化的 API

def start_task(sm: TaskStateMachine, *, reason: str = "用户确认执行") -> bool:
    """pending → executing"""
    return sm.transition(TaskState.EXECUTING, reason=reason)


def pause_task(sm: TaskStateMachine, *, reason: str = "用户暂停") -> bool:
    """executing → paused"""
    return sm.transition(TaskState.PAUSED, reason=reason)


def resume_task(sm: TaskStateMachine, *, reason: str = "用户恢复执行") -> bool:
    """paused → executing"""
    return sm.transition(TaskState.EXECUTING, reason=reason)


def complete_task(sm: TaskStateMachine, *, reason: str = "全部步骤执行成功") -> bool:
    """executing → completed"""
    return sm.transition(TaskState.COMPLETED, reason=reason)


def fail_task(sm: TaskStateMachine, *, reason: str = "执行失败") -> bool:
    """executing → failed"""
    return sm.transition(TaskState.FAILED, reason=reason)


def cancel_task(sm: TaskStateMachine, *, reason: str = "用户取消") -> bool:
    """任意活跃状态 → cancelled"""
    if sm.state not in TaskState.active():
        logger.warning("任务 %s 状态 %s 不可取消", sm._task_id, sm.state.value)
        return False
    return sm.transition(TaskState.CANCELLED, reason=reason)
