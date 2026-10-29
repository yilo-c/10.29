"""AgentTask 生命周期助手：profile 快照、任务构建、持久化、执行-记录模式。

从 AutonomousAssistantManager 中提取，让总控类只关注编排，
新增行为不需要继续堆到 autonomy 总控类里。
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from datetime import datetime, timezone

from .models import AgentTask, PlannedStep
from .state_machine import TaskState

logger = logging.getLogger("siyi.assistant.task_lifecycle")


class AgentTaskLifecycle:
    """封装 AgentTask 的 profile → build → persist → execute → record 流程。

    新增：
    - Checkpoint 持久化集成（每步执行后保存 checkpoint）
    - 中断恢复支持
    """

    def __init__(self, *, data_store, memory, user_profile_service=None, checkpoint_manager=None, history_store=None):
        self.data_store = data_store
        self.memory = memory
        self.user_profile_service = user_profile_service
        self.checkpoint_manager = checkpoint_manager
        self.history_store = history_store

    # ── profile 快照 ────────────────────────────────────────
    def get_user_profile_snapshot(self) -> dict[str, object]:
        if self.user_profile_service is not None:
            try:
                snapshot = self.user_profile_service.build_routing_context()
                if isinstance(snapshot, dict):
                    return dict(snapshot)
            except Exception as exc:
                logger.info("读取 UserProfileService 画像失败，回退 memory snapshot: %s", exc, exc_info=True)
        getter = getattr(self.memory, "get_user_profile_snapshot", None)
        if callable(getter):
            try:
                snapshot = getter()
                if isinstance(snapshot, dict):
                    return dict(snapshot)
            except Exception as exc:
                logger.info("读取用户画像失败，回退为空画像: %s", exc, exc_info=True)
        return {}

    # ── 状态 / 通道解析 ─────────────────────────────────────
    @staticmethod
    def resolve_task_status(summary: dict[str, object]) -> str:
        if int(summary.get("failed") or 0) > 0:
            return "failed"
        if int(summary.get("blocked") or 0) > 0 and int(summary.get("ok") or 0) == 0:
            return "blocked"
        if int(summary.get("degraded") or 0) > 0 and int(summary.get("ok") or 0) == 0:
            return "degraded"
        return "completed"

    @staticmethod
    def resolve_delivery_channel(delivery_channel: str | None, profile_snapshot: dict[str, object]) -> str:
        explicit = str(delivery_channel or "").strip()
        if explicit:
            return explicit
        preferred = str(profile_snapshot.get("preferred_delivery") or "").strip()
        return preferred or "chat"

    # ── 构建 ────────────────────────────────────────────────
    def build_agent_task(
        self,
        goal: str,
        *,
        source: str,
        delivery_channel: str | None = None,
        profile_snapshot: dict[str, object] | None = None,
        chat_session_id: str | None = None,
    ) -> AgentTask:
        snapshot = dict(profile_snapshot or {})
        task = AgentTask(
            goal=str(goal or "").strip(),
            source=str(source or "assistant.goal").strip() or "assistant.goal",
            delivery_channel=self.resolve_delivery_channel(delivery_channel, snapshot),
            chat_session_id=str(chat_session_id or "").strip(),
            profile_snapshot=snapshot,
        )
        task.updated_at = datetime.now(timezone.utc).isoformat()
        return task

    # ── 持久化 ──────────────────────────────────────────────
    def persist_agent_task(self, task: AgentTask) -> None:
        if self.data_store is None:
            return

        def _updater(tasks: list[dict[str, object]]) -> list[dict[str, object]]:
            normalized = list(tasks) if isinstance(tasks, list) else []
            replacement = task.to_dict()
            updated = False
            for index, item in enumerate(normalized):
                if not isinstance(item, dict):
                    continue
                if str(item.get("task_id") or "") != task.task_id:
                    continue
                normalized[index] = replacement
                updated = True
                break
            if not updated:
                normalized.append(replacement)
            return normalized[-200:]

        self.data_store.update_json("assistant_agent_tasks", [], _updater)

    def load_agent_task(self, task_id: str) -> AgentTask | None:
        """从 data_store 加载指定 task_id 的 AgentTask。"""
        if self.data_store is None:
            return None
        try:
            tasks = self.data_store.load_json("assistant_agent_tasks", [])
            for item in tasks if isinstance(tasks, list) else []:
                if not isinstance(item, dict):
                    continue
                if str(item.get("task_id") or "") == str(task_id):
                    return AgentTask.from_dict(item)
        except Exception as exc:
            logger.warning("load_agent_task failed: %s", exc)
        return None

    # ── 执行-记录 组合 ──────────────────────────────────────
    def execute_and_record(
        self,
        *,
        goal: str,
        steps: list[PlannedStep],
        source: str,
        executor,
        authorize_callback: Callable,
        delivery_channel: str | None = None,
        profile_snapshot: dict[str, object] | None = None,
        chat_session_id: str | None = None,
        checkpoint_adapter=None,
        cancellation_event: threading.Event | None = None,
    ) -> dict[str, object]:
        """构建 task → 执行 → 更新状态 → 持久化 → 记忆，返回 summary。

        如果提供 checkpoint_adapter，每步执行后自动保存 checkpoint。
        """
        snapshot = profile_snapshot if profile_snapshot is not None else self.get_user_profile_snapshot()
        task = self.build_agent_task(
            goal,
            source=source,
            delivery_channel=delivery_channel,
            profile_snapshot=snapshot,
            chat_session_id=chat_session_id,
        )
        task.set_steps(steps)
        task.set_status(TaskState.EXECUTING.value)
        self.persist_agent_task(task)
        summary = executor.execute_plan(
            steps,
            authorize_callback=authorize_callback,
            task=task,
            delivery_channel=delivery_channel,
            user_profile=snapshot,
            cancellation_event=cancellation_event,
        )
        task.set_status(
            self.resolve_task_status(summary),
            result={"delivery": dict(summary.get("delivery") or {})},
        )
        self.persist_agent_task(task)
        summary["task"] = task.to_dict()
        self.memory.append_decision(goal, steps, summary)

        # Checkpoint 保存
        if checkpoint_adapter and task.task_id:
            checkpoint_adapter.save_on_step_complete(
                task_id=task.task_id,
                state=task.status,
                step_index=len(steps) - 1 if steps else 0,
                step_result={"summary": summary},
                context_snapshot=snapshot,
                # S1-3：收尾判定 + 内部事件标记所需的总步数与 goal
                total_steps=len(steps),
                goal=goal,
            )

        # 执行历史记录
        if self.history_store is not None:
            from .execution_history import ExecutionRecord

            record = ExecutionRecord(
                task_id=task.task_id,
                goal=goal,
                source=source,
                triggered_by="user",
            )
            record.start()
            record.steps = list(summary.get("outcomes", []))
            record.complete(summary)
            self.history_store.save_record(record)

        return summary
