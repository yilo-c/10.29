"""AI 助手任务领域模型。

对应 assistant/models.py 中 AgentTask 的 Pydantic 化版本，
作为跨层契约的一部分与数据存储层解耦。
"""

from __future__ import annotations

import uuid
from typing import Any

from pydantic import BaseModel, Field

from ._base import SyncMetadataMixin


def _default_task_id() -> str:
    return uuid.uuid4().hex[:12]


class AssistantTask(SyncMetadataMixin):
    """AI 助手任务实体 — 表达一个由 AI 决定执行的任务。

    与 domain/models/task.py 中用户级别的 Task 不同，
    AssistantTask 是 AI 内部工作流中的任务单元，包含执行计划、
    步骤追踪和检查点等内部状态。
    """

    goal: str = ""
    task_id: str = Field(default_factory=_default_task_id)
    source: str = "assistant.goal"
    status: str = "pending"
    delivery_channel: str = "chat"
    chat_session_id: str = ""
    steps: list[dict[str, Any]] = Field(default_factory=list)
    profile_snapshot: dict[str, Any] = Field(default_factory=dict)
    result: dict[str, Any] = Field(default_factory=dict)
    # Checkpoint
    checkpoint_id: str | None = None
    checkpoint_version: int = 0
    completed_step_indices: list[int] = Field(default_factory=list)
    transition_history: list[dict[str, Any]] = Field(default_factory=list)
    # Plan
    plan_id: str | None = None
    # Transaction
    is_transactional: bool = False
    file_snapshots: dict[str, Any] = Field(default_factory=dict)

    def set_status(self, status: str, *, result: dict[str, Any] | None = None) -> None:
        self.status = status
        if result is not None:
            self.result = result

    def mark_step_completed(self, step_index: int) -> None:
        if step_index not in self.completed_step_indices:
            self.completed_step_indices.append(step_index)

    def is_step_completed(self, step_index: int) -> bool:
        return step_index in self.completed_step_indices
