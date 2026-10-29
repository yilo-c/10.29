"""assistant_task: 统一任务对象与总控入口。

所有入口（聊天、规则中心、定时、浏览器桥接）通过此模块进入主链。
提供标准的 AgentTask 创建、执行、状态生命周期管理。

## 状态生命周期

    pending (created + planned)
        │
        ▼
    executing
        │
        ├──→ paused ──→ executing (恢复)
        ├──→ completed
        ├──→ failed
        ├──→ blocked (全部步骤被拦截)
        ├──→ degraded (部分步骤降级)
        └──→ cancelled

    cancelled 可从 pending / executing / paused 到达
    blocked/degraded 执行器直接设置，不经过状态机
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from .models import AgentTask
from .task_lifecycle import AgentTaskLifecycle
from .state_machine import (
    TaskState,
    TaskStateMachine,
    StateChangeListener,
    start_task,
    pause_task,
    resume_task,
    complete_task,
    fail_task,
    cancel_task,
)

logger = logging.getLogger("siyi.assistant.task")

# ── 统一状态常量 ──────────────────────────────────────────
STATUS_PENDING = TaskState.PENDING.value
STATUS_EXECUTING = TaskState.EXECUTING.value
STATUS_PAUSED = TaskState.PAUSED.value
STATUS_COMPLETED = TaskState.COMPLETED.value
STATUS_FAILED = TaskState.FAILED.value
STATUS_CANCELLED = TaskState.CANCELLED.value

# 旧兼容别名
STATUS_RUNNING = STATUS_EXECUTING
STATUS_DEGRADED = "degraded"
STATUS_BLOCKED = "blocked"


def status_label(status: str) -> str:
    """返回状态的中文标签。"""
    labels = {
        STATUS_PENDING: "待处理",
        STATUS_EXECUTING: "执行中",
        STATUS_PAUSED: "已暂停",
        STATUS_COMPLETED: "已完成",
        STATUS_FAILED: "失败",
        STATUS_CANCELLED: "已取消",
        STATUS_DEGRADED: "降级",
        STATUS_BLOCKED: "已拦截",
    }
    return labels.get(status, status)


# ── 统一任务入口 ──────────────────────────────────────────

class AssistantTaskEntry:
    """统一任务入口。

    封装 AgentTaskLifecycle，为聊天、规则中心、定时、浏览器桥接
    提供一致的 AgentTask 创建和生命周期管理 API。

    新增：
    - 状态机管理（暂停/恢复/取消）
    - 可从中断点续做
    """

    def __init__(self, task_lifecycle: AgentTaskLifecycle):
        self._lifecycle = task_lifecycle
        self._state_machines: dict[str, TaskStateMachine] = {}  # task_id → state machine
        self._state_change_listeners: list[StateChangeListener] = []

    def on_state_change(self, listener: StateChangeListener) -> None:
        """注册全局状态变更监听器。"""
        self._state_change_listeners.append(listener)

    def _get_or_create_sm(self, task: AgentTask) -> TaskStateMachine:
        """获取或创建任务的状态机。"""
        if task.task_id not in self._state_machines:
            sm = TaskStateMachine(task.task_id, initial_state=task.status or STATUS_PENDING)
            for listener in self._state_change_listeners:
                sm.add_listener(listener)
            self._state_machines[task.task_id] = sm
        return self._state_machines[task.task_id]

    # ── 通用创建 ──────────────────────────────────────────

    def create_task(
        self,
        goal: str,
        *,
        source: str = "assistant.goal",
        delivery_channel: str | None = None,
        chat_session_id: str | None = None,
        profile_snapshot: dict[str, Any] | None = None,
    ) -> AgentTask:
        """创建统一的 AgentTask。"""
        return self._lifecycle.build_agent_task(
            goal,
            source=source,
            delivery_channel=delivery_channel,
            profile_snapshot=profile_snapshot,
            chat_session_id=chat_session_id,
        )

    # ── 按入口创建 ────────────────────────────────────────

    def from_chat(
        self,
        text: str,
        *,
        chat_session_id: str | None = None,
        profile_snapshot: dict[str, Any] | None = None,
    ) -> AgentTask:
        """聊天入口：默认交付到 chat 通道。"""
        return self.create_task(
            text,
            source="assistant.chat",
            delivery_channel="chat",
            chat_session_id=chat_session_id,
            profile_snapshot=profile_snapshot,
        )

    def from_rule_center(
        self,
        goal: str,
        *,
        rule_id: str = "",
        profile_snapshot: dict[str, Any] | None = None,
    ) -> AgentTask:
        """规则中心入口：默认交付到 inbox 通道。"""
        source = f"assistant.rule:{rule_id}" if rule_id else "assistant.rule"
        return self.create_task(
            goal,
            source=source,
            delivery_channel="inbox",
            profile_snapshot=profile_snapshot,
        )

    def from_timer(
        self,
        goal: str,
        *,
        event_name: str = "",
        profile_snapshot: dict[str, Any] | None = None,
    ) -> AgentTask:
        """定时入口：默认交付到 inbox 通道。"""
        source = f"assistant.schedule:{event_name}" if event_name else "assistant.schedule"
        return self.create_task(
            goal,
            source=source,
            delivery_channel="inbox",
            profile_snapshot=profile_snapshot,
        )

    def from_browser(
        self,
        goal: str,
        *,
        url: str = "",
        profile_snapshot: dict[str, Any] | None = None,
    ) -> AgentTask:
        """浏览器桥接入口：默认交付到 inbox 通道。"""
        return self.create_task(
            goal,
            source="assistant.browser",
            delivery_channel="inbox",
            profile_snapshot=profile_snapshot,
        )

    # ── 状态管理 ──────────────────────────────────────────

    def start_execution(self, task: AgentTask) -> bool:
        """pending → executing"""
        sm = self._get_or_create_sm(task)
        if not start_task(sm):
            return False
        task.set_status(STATUS_EXECUTING)
        task.transition_history = list(sm.history)
        self._lifecycle.persist_agent_task(task)
        return True

    def pause_execution(self, task: AgentTask, *, reason: str = "用户暂停") -> bool:
        """executing → paused"""
        sm = self._get_or_create_sm(task)
        if not pause_task(sm, reason=reason):
            return False
        task.set_status(STATUS_PAUSED)
        task.transition_history = list(sm.history)
        self._lifecycle.persist_agent_task(task)
        return True

    def resume_execution(self, task: AgentTask, *, reason: str = "用户恢复执行") -> bool:
        """paused → executing"""
        sm = self._get_or_create_sm(task)
        if not resume_task(sm, reason=reason):
            return False
        task.set_status(STATUS_EXECUTING)
        task.transition_history = list(sm.history)
        self._lifecycle.persist_agent_task(task)
        return True

    def cancel_execution(self, task: AgentTask, *, reason: str = "用户取消") -> bool:
        """任意活跃状态 → cancelled"""
        sm = self._get_or_create_sm(task)
        if not cancel_task(sm, reason=reason):
            return False
        task.set_status(STATUS_CANCELLED)
        task.transition_history = list(sm.history)
        self._lifecycle.persist_agent_task(task)
        return True

    def complete_execution(self, task: AgentTask, *, reason: str = "全部步骤执行成功") -> bool:
        """executing → completed"""
        sm = self._get_or_create_sm(task)
        if not complete_task(sm, reason=reason):
            return False
        task.set_status(STATUS_COMPLETED)
        task.transition_history = list(sm.history)
        self._lifecycle.persist_agent_task(task)
        return True

    def fail_execution(self, task: AgentTask, *, reason: str = "执行失败") -> bool:
        """executing → failed"""
        sm = self._get_or_create_sm(task)
        if not fail_task(sm, reason=reason):
            return False
        task.set_status(STATUS_FAILED)
        task.transition_history = list(sm.history)
        self._lifecycle.persist_agent_task(task)
        return True

    def can_resume(self, task: AgentTask) -> bool:
        """检查任务是否可以从 checkpoint 恢复。"""
        return task.status == STATUS_PAUSED or (
            task.status == STATUS_EXECUTING and task.completed_step_indices
        )

    # ── 执行融合 ──────────────────────────────────────────

    def execute_task(
        self,
        task: AgentTask,
        steps: list,
        executor,
        authorize_callback,
        *,
        user_profile: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """执行任务并通过 lifecycle 记录结果。"""
        if task.status == STATUS_PENDING:
            self.start_execution(task)

        self._lifecycle.persist_agent_task(task)
        summary = executor.execute_plan(
            steps,
            authorize_callback=authorize_callback,
            task=task,
            delivery_channel=task.delivery_channel,
            user_profile=user_profile,
        )
        resolved = _resolve_status(summary)

        if resolved == STATUS_COMPLETED:
            self.complete_execution(task)
        elif resolved == STATUS_FAILED:
            self.fail_execution(task, reason=_first_failure_reason(summary))
        else:
            task.set_status(resolved, result={"delivery": dict(summary.get("delivery") or {})})
            self._lifecycle.persist_agent_task(task)

        summary["task"] = task.to_dict()
        self._lifecycle.memory.append_decision(task.goal, steps, summary)
        return summary


def _resolve_status(summary: dict[str, Any]) -> str:
    """从执行摘要中解析任务最终状态。"""
    if int(summary.get("failed") or 0) > 0:
        return STATUS_FAILED
    if int(summary.get("blocked") or 0) > 0 and int(summary.get("ok") or 0) == 0:
        return STATUS_BLOCKED
    if int(summary.get("degraded") or 0) > 0 and int(summary.get("ok") or 0) == 0:
        return STATUS_DEGRADED
    return STATUS_COMPLETED


def _first_failure_reason(summary: dict[str, Any]) -> str:
    for outcome in list(summary.get("outcomes") or []):
        if outcome.get("status") in ("failed", "timeout"):
            return str(outcome.get("reason") or outcome.get("error") or "未知错误")
    return "执行失败"
