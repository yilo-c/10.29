"""Checkpoint 持久化：每步执行后保存 checkpoint，崩溃可恢复。

参考：
- Windsurf Flow 的状态持久化
- LangGraph 的持久化检查点设计
- AutoGPT 的进度持久化机制
"""

from __future__ import annotations

import logging
import os
import uuid
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger("siyi.assistant.checkpoint")

# ── 默认配置 ────────────────────────────────────────────────
DEFAULT_CHECKPOINT_DIR = "checkpoints"
DEFAULT_MAX_VERSIONS = 10  # 保留最近 N 个版本
DEFAULT_ARCHIVE_AFTER_DAYS = 7  # 超过 N 天自动归档


class CheckpointError(Exception):
    """checkpoint 操作异常。"""

    pass


class CheckpointManager:
    """Checkpoint 管理器：保存、恢复、清理 checkpoint。

    每个 checkpoint 包含：
    - task_id: 任务 ID
    - state: 当前状态
    - completed_steps: 已成功执行的步骤索引列表
    - step_results: 已执行步骤的中间结果
    - context_snapshot: 上下文快照（用户画像、环境状态等）
    - file_snapshots: 文件修改前的快照（供回滚用）
    - created_at: 创建时间
    - version: 版本号（自动递增）
    - internal: 是否为内部事件任务（S1-3，见 save_checkpoint）
    - total_steps: 显式提供的步骤总数（S1-3；None = 未知）
    - goal: 任务目标文本（若调用方提供，S1-3 用于 internal 判定）

    注意：本 schema **不包含** ``waiting_since`` 字段。该字段只在旧实测报告的
    schema 描述中出现过，全仓（后端 + desk-notes 前端）无任何写路径与消费方
    （grep 零命中），属"永不写的谎言 schema"，S1-3 定夺为删除/不引入。
    waiting_approval 的时间可观测性由 ``created_at`` 承担。
    """

    def __init__(
        self,
        data_store: Any,
        *,
        max_versions: int = DEFAULT_MAX_VERSIONS,
        archive_after_days: int = DEFAULT_ARCHIVE_AFTER_DAYS,
    ):
        self._data_store = data_store
        self._max_versions = max_versions
        self._archive_after_days = archive_after_days

    # ── 保存 checkpoint ─────────────────────────────────

    # 终态集合：与 scan_unfinished_tasks 的排除集一致。
    TERMINAL_STATES = frozenset({"completed", "failed", "cancelled"})
    # 内部事件 goal 前缀（event:startup / event:shutdown / event:off_work …）。
    INTERNAL_GOAL_PREFIX = "event:"

    @classmethod
    def _is_internal_goal_text(cls, text: Any) -> bool:
        """S1-3：goal/标题以 ``event:`` 前缀开头 → 内部事件任务。"""
        return str(text or "").strip().lower().startswith(cls.INTERNAL_GOAL_PREFIX)

    @classmethod
    def _checkpoint_goal_texts(cls, cp: dict[str, Any]) -> list[str]:
        """从 checkpoint 自身可得的 goal 文本（plan.goal / plan.title / goal）。"""
        plan = cp.get("plan")
        texts = [cp.get("goal", "")]
        if isinstance(plan, dict):
            texts.extend([plan.get("goal", ""), plan.get("title", "")])
        return [str(t or "") for t in texts]

    @classmethod
    def _is_internal_checkpoint(cls, cp: dict[str, Any], *, task_goal: str = "") -> bool:
        """checkpoint 是否内部事件任务。

        双保险：internal 标记（新写路径）+ goal 以 ``event:`` 开头
        （**存量兼容过滤**：S1-3 之前的旧数据没有 internal 标记，
        goal 落在 task store / plan.title 里，这里按前缀兜底过滤）。
        ``task_goal`` 为从 assistant_agent_tasks join 来的存量任务 goal。
        """
        if cp.get("internal") is True:
            return True
        if task_goal and cls._is_internal_goal_text(task_goal):
            return True
        return any(cls._is_internal_goal_text(t) for t in cls._checkpoint_goal_texts(cp))

    def _legacy_event_goal_ids(self) -> set[str]:
        """存量兼容：task store 中 goal 以 ``event:`` 开头的 task_id 集合。

        S1-3 之前的 checkpoint 不写 goal/internal，goal 只存在于
        assistant_agent_tasks；僵尸横幅的 ``event:startup`` 类条目即靠
        此 join 过滤。读取失败时返回空集（过滤退化为 internal 标记）。
        """
        try:
            tasks = self._data_store.load_json("assistant_agent_tasks", [])
        except Exception:
            logger.debug("legacy event-goal join failed", exc_info=True)
            return set()
        ids: set[str] = set()
        for item in tasks if isinstance(tasks, list) else []:
            if isinstance(item, dict) and self._is_internal_goal_text(item.get("goal", "")):
                tid = str(item.get("task_id") or "")
                if tid:
                    ids.add(tid)
        return ids

    @classmethod
    def _is_steps_full_nonterminal(cls, cp: dict[str, Any]) -> bool:
        """S1-3：executing 且步数已满（completed_steps >= total_steps）。

        新写路径在 save_checkpoint 已直接落 completed；这里再拦一道是
        **存量兼容过滤**——旧数据步满却滞留 executing 的僵尸不再进
        unfinished 视图。total_steps 缺失/≤0 视为未知，不做推断。
        """
        if cp.get("state") != "executing":
            return False
        total = cp.get("total_steps")
        if not isinstance(total, int) or total <= 0:
            return False
        return len(cp.get("completed_steps") or []) >= total

    @classmethod
    def _infer_total_steps(cls, plan: dict[str, Any]) -> int | None:
        """plan.steps 是列表时以其长度为总步数；否则未知返回 None。"""
        steps = plan.get("steps") if isinstance(plan, dict) else None
        if isinstance(steps, list) and steps:
            return len(steps)
        return None

    @classmethod
    def _apply_terminal_state(cls, state: str, completed_count: int, total_steps: int | None) -> str:
        """S1-3 收尾判定：executing 且步数已满 → 直接落 completed 终态。

        仅作用于 executing；paused / waiting_approval 语义上可步满挂起，
        不强推终态。
        """
        if state == "executing" and isinstance(total_steps, int) and total_steps > 0 and completed_count >= total_steps:
            return "completed"
        return state

    def save_checkpoint(
        self,
        task_id: str,
        *,
        state: str,
        completed_steps: list[int],
        step_results: list[dict[str, Any]],
        context_snapshot: dict[str, Any] | None = None,
        file_snapshots: dict[str, Any] | None = None,
        plan: dict[str, Any] | None = None,
        goal: str = "",
        total_steps: int | None = None,
        internal: bool | None = None,
    ) -> dict[str, Any]:
        """保存一个 checkpoint。

        S1-3 修复：
        - ``total_steps``：显式提供（或由 plan.steps 推断）时，executing 且
          步数已满 → 直接落 completed 终态，不再滞留 unfinished 集
          （154 条 executing 2/2 僵尸的上游根因）。
        - ``internal``：None 时按 goal / plan.goal / plan.title 的
          ``event:`` 前缀自动判定；内部事件（startup/shutdown/off_work 等）
          打 internal=true，scan_unfinished_tasks 默认过滤。
        - 本 schema **不含** ``waiting_since``：全仓 grep（后端 + desk-notes
          前端）零写路径零消费方，属"永不写的谎言 schema"，定夺删除；
          waiting_approval 的时间可观测性由 created_at 承担。
        """
        if not task_id:
            raise CheckpointError("task_id is required")

        plan_dict = dict(plan or {})
        if internal is None:
            internal = self._is_internal_goal_text(goal) or any(
                self._is_internal_goal_text(t) for t in self._checkpoint_goal_texts({"plan": plan_dict})
            )

        resolved_total: int | None
        if isinstance(total_steps, int) and total_steps > 0:
            resolved_total = total_steps
        else:
            resolved_total = self._infer_total_steps(plan_dict)
        state = self._apply_terminal_state(state, len(completed_steps), resolved_total)

        checkpoint = {
            "checkpoint_id": uuid.uuid4().hex[:12],
            "task_id": task_id,
            "state": state,
            "completed_steps": list(completed_steps),
            "step_results": list(step_results),
            "context_snapshot": dict(context_snapshot or {}),
            "file_snapshots": dict(file_snapshots or {}),
            "plan": plan_dict,
            "goal": str(goal or ""),
            "internal": bool(internal),
            "total_steps": resolved_total,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "version": 0,  # 将在 _store_checkpoint 中设置
        }

        return self._store_checkpoint(checkpoint)

    def _store_checkpoint(self, checkpoint: dict[str, Any]) -> dict[str, Any]:
        """存储 checkpoint 到 datastore，自动管理版本和保留策略。"""
        task_id = checkpoint["task_id"]

        def _updater(checkpoints: Any) -> list[dict[str, Any]]:
            normalized = list(checkpoints) if isinstance(checkpoints, list) else []
            # 获取当前 task 的已有 checkpoints
            task_cps = [cp for cp in normalized if isinstance(cp, dict) and cp.get("task_id") == task_id]
            # 计算下一个版本号
            next_version = max([int(cp.get("version", 0)) for cp in task_cps], default=0) + 1
            checkpoint["version"] = next_version

            # 归档旧版本：标记超过保留数量的为 archived。
            # 新 checkpoint 总是最新（永不归档），因此已有版本按版本号降序
            # 只保留 max_versions - 1 个，其余归档 —— 保证任务未归档总数
            # 恰好等于 max_versions（修复 off-by-one：旧逻辑会多留 1 个）。
            keep_existing = max(0, self._max_versions - 1)
            task_cps_sorted = sorted(task_cps, key=lambda cp: int(cp.get("version", 0)), reverse=True)
            for idx, cp in enumerate(task_cps_sorted):
                if idx >= keep_existing:
                    cp["archived"] = True

            # 添加新 checkpoint
            normalized.append(checkpoint)
            return normalized[-200:]  # 总上限 200

        self._data_store.update_json("assistant_checkpoints", [], _updater)
        return checkpoint

    # ── 读取 checkpoint ─────────────────────────────────

    def load_checkpoint(self, checkpoint_id: str) -> dict[str, Any] | None:
        """按 checkpoint_id 加载。"""
        all_cps = self._data_store.load_json("assistant_checkpoints", [])
        normalized = list(all_cps) if isinstance(all_cps, list) else []
        for cp in normalized:
            if isinstance(cp, dict) and cp.get("checkpoint_id") == checkpoint_id:
                return dict(cp)
        return None

    def get_task_checkpoints(
        self,
        task_id: str,
        *,
        include_archived: bool = False,
    ) -> list[dict[str, Any]]:
        """获取任务的所有 checkpoint（按版本降序）。"""
        all_cps = self._data_store.load_json("assistant_checkpoints", [])
        normalized = list(all_cps) if isinstance(all_cps, list) else []
        result = []
        for cp in normalized:
            if not isinstance(cp, dict):
                continue
            if cp.get("task_id") != task_id:
                continue
            if not include_archived and cp.get("archived"):
                continue
            result.append(dict(cp))
        return sorted(result, key=lambda cp: int(cp.get("version", 0)), reverse=True)

    def get_latest_checkpoint(self, task_id: str) -> dict[str, Any] | None:
        """获取任务的最新（最高版本）checkpoint。"""
        cps = self.get_task_checkpoints(task_id, include_archived=False)
        return cps[0] if cps else None

    # ── 恢复检测 ────────────────────────────────────────

    def scan_unfinished_tasks(self, *, include_internal: bool = False) -> list[dict[str, Any]]:
        """扫描所有未完成任务的 checkpoint。

        返回非终端状态的任务的最新 checkpoint 列表。
        用于应用崩溃后重启时检测可恢复的任务。

        S1-3 过滤（默认生效）：
        - 内部事件任务（internal=true，或 **存量兼容**：goal/event 文本以
          ``event:`` 前缀开头——旧数据没有 internal 标记，按前缀双保险兜底）
          不进视图；``include_internal=True`` 时恢复可见。
        - 存量兼容：executing 且 total_steps 已知且步数已满 → 不再当未完成。
        """
        all_cps = self._data_store.load_json("assistant_checkpoints", [])
        normalized = list(all_cps) if isinstance(all_cps, list) else []

        # 存量兼容：旧 checkpoint 不写 goal/internal，其 event:* goal 只存在于
        # assistant_agent_tasks，这里 join 出 event 前缀 task_id 兜底过滤。
        legacy_event_ids = set() if include_internal else self._legacy_event_goal_ids()

        # 按 task_id 分组，取每个 task 的最新 checkpoint
        task_latest: dict[str, dict[str, Any]] = {}
        for cp in normalized:
            if not isinstance(cp, dict):
                continue
            tid = cp.get("task_id", "")
            if not tid:
                continue
            if cp.get("archived"):
                continue
            # 只关注非终端状态
            if cp.get("state") in self.TERMINAL_STATES:
                continue
            if not include_internal and (tid in legacy_event_ids or self._is_internal_checkpoint(cp)):
                continue
            if self._is_steps_full_nonterminal(cp):
                continue
            existing = task_latest.get(tid)
            if existing is None or int(cp.get("version", 0)) > int(existing.get("version", 0)):
                task_latest[tid] = dict(cp)

        return list(task_latest.values())

    def recover_task(self, task_id: str) -> dict[str, Any] | None:
        """从 checkpoint 恢复任务上下文。

        返回最新 checkpoint，包含：
        - 已完成步骤列表
        - 已执行步骤的中间结果
        - 上下文快照
        """
        return self.get_latest_checkpoint(task_id)

    # ── 清理 ─────────────────────────────────────────────

    def archive_old_checkpoints(self, *, before_days: int | None = None) -> int:
        """归档超出保留期限的旧 checkpoint。返回归档数量。"""
        days = before_days or self._archive_after_days
        cutoff = datetime.now(timezone.utc).isoformat()

        archived_count = 0

        def _updater(checkpoints: Any) -> list[dict[str, Any]]:
            nonlocal archived_count
            normalized = list(checkpoints) if isinstance(checkpoints, list) else []
            from datetime import datetime, timedelta

            result = []
            for cp in normalized:
                if not isinstance(cp, dict):
                    result.append(cp)
                    continue
                try:
                    created = datetime.fromisoformat(cp.get("created_at", ""))
                    if (datetime.now(timezone.utc) - created) > timedelta(days=days):
                        cp["archived"] = True
                        archived_count += 1
                except (ValueError, TypeError):
                    logger.debug("Invalid created_at in checkpoint: %s", cp.get("created_at"), exc_info=True)
                result.append(cp)
            return result

        self._data_store.update_json("assistant_checkpoints", [], _updater)
        return archived_count

    def delete_task_checkpoints(self, task_id: str) -> int:
        """删除指定任务的所有 checkpoint。返回删除数量。"""
        deleted = [0]  # mutable for closure

        def _updater(checkpoints: Any) -> list[dict[str, Any]]:
            normalized = list(checkpoints) if isinstance(checkpoints, list) else []
            remaining = []
            for cp in normalized:
                if isinstance(cp, dict) and cp.get("task_id") == task_id:
                    deleted[0] += 1
                else:
                    remaining.append(cp)
            return remaining

        self._data_store.update_json("assistant_checkpoints", [], _updater)
        return deleted[0]


# ── DataStore 助手 ──────────────────────────────────────────


class DataStoreCheckpointAdapter:
    """将 CheckpointManager 适配到应用级 data_store。

    提供 save_on_step_complete / restore_from_last 等高层 API。
    """

    def __init__(self, checkpoint_manager: CheckpointManager):
        self._manager = checkpoint_manager

    def save_on_step_complete(
        self,
        task_id: str,
        state: str,
        step_index: int,
        step_result: dict[str, Any],
        context_snapshot: dict[str, Any] | None = None,
        file_snapshots: dict[str, Any] | None = None,
        plan: dict[str, Any] | None = None,
        *,
        previous_results: list[dict[str, Any]] | None = None,
        total_steps: int | None = None,
        goal: str = "",
    ) -> dict[str, Any]:
        """每步完成后保存 checkpoint。"""
        completed = list(range(step_index + 1))
        results = list(previous_results or []) + [step_result]
        return self._manager.save_checkpoint(
            task_id,
            state=state,
            completed_steps=completed,
            step_results=results,
            context_snapshot=context_snapshot,
            file_snapshots=file_snapshots,
            plan=plan,
            total_steps=total_steps,
            goal=goal,
        )

    def restore_and_get_remaining(
        self,
        task_id: str,
        total_steps: int,
    ) -> tuple[dict[str, Any] | None, list[int]]:
        """从 checkpoint 恢复，返回（checkpoint, 未完成的步骤索引列表）。"""
        cp = self._manager.recover_task(task_id)
        if cp is None:
            return None, list(range(total_steps))

        completed = set(int(i) for i in cp.get("completed_steps", []) if isinstance(i, (int, str)))
        remaining = [i for i in range(total_steps) if i not in completed]
        return cp, remaining
