"""统一任务管理器：集成状态机、Checkpoint、事务、执行计划、历史记录和回滚。

将所有底层组件编织成内聚的工作流：
- assistant_state_machine → 状态转换 + 事件通知
- assistant_checkpoint → 每步持久化 + 崩溃恢复
- assistant_transaction → 原子性多文件修改 + 回滚
- assistant_execution_plan → 计划生成 + 用户确认
- assistant_execution_history → 历史记录 + 重放 + 回滚
- audit_rollback → 审计日志
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from .checkpoint import CheckpointManager, DataStoreCheckpointAdapter
from .execution_history import AuditLogWriter, ExecutionHistoryStore, ExecutionRecord
from .execution_plan import ExecutionPlan, ExecutionPlanStore, PlanStatus
from .models import AgentTask, PlannedStep
from .state_machine import (
    TaskState,
    TaskStateMachine,
    start_task,
    pause_task,
    resume_task,
    complete_task,
    fail_task,
    cancel_task,
)
from .transaction import FileTransaction, SnapshotManager

try:
    from core.events import AppEvent, event_bus
except ImportError:
    # Fallback if EventBus is not available in the environment
    class AppEvent:  # type: ignore[no-redef]
        def __init__(self, event_type, payload=None, source=""):
            self.event_type = event_type
            self.payload = payload or {}
            self.source = source

    event_bus = type("NullBus", (), {"emit": lambda self, event: None})()  # type: ignore[no-redef]

if TYPE_CHECKING:
    from data.local_storage import LocalDataStore
    from .events import AssistantEventLayer
    from .execution import AssistantExecutionLayer
    from .planner import AssistantPlannerLayer
    from .security_layer import AssistantSecurityLayer
    from .task_lifecycle import AgentTaskLifecycle

logger = logging.getLogger("siyi.assistant.task_manager")

# ── 常量 ──────────────────────────────────────────────────────
STATUS_PENDING = TaskState.PENDING.value
STATUS_EXECUTING = TaskState.EXECUTING.value
STATUS_PAUSED = TaskState.PAUSED.value
STATUS_COMPLETED = TaskState.COMPLETED.value
STATUS_FAILED = TaskState.FAILED.value
STATUS_CANCELLED = TaskState.CANCELLED.value


class TaskManager:
    """统一任务管理器。

    编排所有底层组件，提供：
    - 支持计划确认的任务执行流（Windsurf 风格）
    - 支持检查点的持久化执行与崩溃恢复
    - 支持事务多文件修改的原子执行
    - 执行历史查询、重放与回滚
    - 审计日志
    """

    def __init__(
        self,
        *,
        data_store: LocalDataStore,
        task_lifecycle: AgentTaskLifecycle,
        executor: AssistantExecutionLayer,
        planner: AssistantPlannerLayer,
        security: AssistantSecurityLayer,
        checkpoint_manager: CheckpointManager | None = None,
        history_store: ExecutionHistoryStore | None = None,
        plan_store: ExecutionPlanStore | None = None,
        audit_logger: AuditLogWriter | None = None,
        events: AssistantEventLayer | None = None,
        cron_service: Any | None = None,
    ) -> None:
        self._data_store = data_store
        self._lifecycle = task_lifecycle
        self._executor = executor
        self._planner = planner
        self._security = security
        self._checkpoint_manager = checkpoint_manager
        self._history_store = history_store
        self._plan_store = plan_store
        self._audit_logger = audit_logger
        self._events = events
        self._cron_service = cron_service

        # 任务 ID → TaskStateMachine 缓存
        self._state_machines: dict[str, TaskStateMachine] = {}

    # ── 内部辅助方法 ──────────────────────────────────────────

    def _get_or_create_sm(self, task: AgentTask) -> TaskStateMachine:
        if task.task_id not in self._state_machines:
            # P0-3：_safe_task_status 已删除，TaskStateMachine.__init__ 内置未知状态回退到 PENDING
            sm = TaskStateMachine(task.task_id, initial_state=task.status or TaskState.PENDING.value)
            self._state_machines[task.task_id] = sm
        return self._state_machines[task.task_id]

    def _record_history(
        self,
        *,
        task_id: str,
        goal: str,
        source: str,
        triggered_by: str = "user",
        plan_id: str | None = None,
        steps: list[dict[str, Any]] | None = None,
        summary: dict[str, Any] | None = None,
        file_snapshots: dict[str, Any] | None = None,
        success: bool = True,
    ) -> str | None:
        """记录执行历史。返回 record_id 或 None。"""
        if self._history_store is None:
            return None
        record = ExecutionRecord(
            task_id=task_id,
            goal=goal,
            source=source,
            triggered_by=triggered_by,
            plan_id=plan_id,
        )
        record.start()
        record.steps = list(steps or [])
        if summary:
            has_snapshots = bool(file_snapshots)
            reversible = has_snapshots and success
            record.file_snapshots = dict(file_snapshots or {})
            record.reversible = reversible
            if success:
                record.complete(summary)
            else:
                reason = summary.get("message") or summary.get("error") or "执行失败"
                record.fail(reason)
        self._history_store.save_record(record)
        return record.record_id

    def _audit_log(
        self,
        *,
        action_id: str,
        source: str,
        triggered_by: str = "user",
        status: str,
        details: dict[str, Any] | None = None,
        record_id: str | None = None,
    ) -> None:
        if self._audit_logger is None:
            return
        self._audit_logger.log_action(
            action_id=action_id,
            source=source,
            triggered_by=triggered_by,
            status=status,
            details=details,
            record_id=record_id,
        )

    def _resolve_status(self, summary: dict[str, Any]) -> str:
        if int(summary.get("failed") or 0) > 0:
            return STATUS_FAILED
        blocked = int(summary.get("blocked") or 0)
        ok = int(summary.get("ok") or 0)
        if blocked > 0 and ok == 0:
            return "blocked"
        degraded = int(summary.get("degraded") or 0)
        if degraded > 0 and ok == 0:
            return "degraded"
        return STATUS_COMPLETED

    def _first_failure_reason(self, summary: dict[str, Any]) -> str:
        for outcome in list(summary.get("outcomes") or []):
            if outcome.get("status") in ("failed", "timeout"):
                return str(outcome.get("reason") or outcome.get("error") or "未知错误")
        return "执行失败"

    @property
    def checkpoint_adapter(self) -> DataStoreCheckpointAdapter | None:
        if self._checkpoint_manager is None:
            return None
        if not hasattr(self, "_checkpoint_adapter") or self._checkpoint_adapter is None:
            self._checkpoint_adapter = DataStoreCheckpointAdapter(self._checkpoint_manager)
        return self._checkpoint_adapter

    # ═══════════════════════════════════════════════════════════
    # 1. 计划 → 确认 → 执行流（Windsurf Cascade 风格）
    # ═══════════════════════════════════════════════════════════

    def generate_plan(
        self,
        goal: str,
        steps: list[PlannedStep],
        *,
        reasoning: str = "",
    ) -> ExecutionPlan:
        """为目标生成执行计划，供用户确认。"""
        plan = ExecutionPlan(
            plan_id=uuid.uuid4().hex[:12],
            goal=goal,
            source="task_manager",
            reasoning=reasoning or "自动生成的执行计划",
        )
        for step in steps:
            plan.add_step(
                step_id=step.step_id,
                action_id=step.action_id,
                description=f"执行 {step.action_id}",
                risk_level=step.risk_level,
                file_paths=list(step.payload.get("file_paths", [])) if isinstance(step.payload, dict) else [],
            )
        # 保存计划
        if self._plan_store is not None:
            self._plan_store.save_plan(plan)
        return plan

    def confirm_plan(
        self,
        plan: ExecutionPlan,
        *,
        approved: bool = True,
        disabled_steps: list[str] | None = None,
        reorder: list[str] | None = None,
        reject_reason: str = "",
    ) -> ExecutionPlan:
        """用户确认/编辑/拒绝计划。"""
        if approved:
            if disabled_steps or reorder:
                plan.edit(disabled_steps=disabled_steps, reorder=reorder)
            plan.approve()
        else:
            plan.reject(reason=reject_reason or "用户拒绝")
        # 保存更新后的计划
        if self._plan_store is not None:
            self._plan_store.save_plan(plan)
        return plan

    def execute_with_plan(
        self,
        task: AgentTask,
        plan: ExecutionPlan,
        *,
        authorize_callback: Callable[[Any], dict[str, Any]] | None = None,
        user_profile: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """按确认后的计划执行任务。

        步骤：
        1. 检查计划状态（必须是 APPROVED 或 EDITED）
        2. 构建计划步骤列表（跳过禁用的步骤）
        3. 执行步骤，每步保存 checkpoint
        4. 更新任务状态
        5. 记录执行历史和审计日志
        """
        if plan.status not in (PlanStatus.APPROVED, PlanStatus.EDITED):
            return {
                "success": False,
                "reason": f"plan_status_{plan.status.value}",
                "message": f"计划状态为 {plan.status.value}，需要 APPROVED 或 EDITED",
            }
        if plan.is_expired():
            plan.status = PlanStatus.EXPIRED
            if self._plan_store is not None:
                self._plan_store.save_plan(plan)
            return {
                "success": False,
                "reason": "plan_expired",
                "message": "执行计划已过期，需要重新确认",
            }

        # 从计划中获取启用的步骤
        plan_steps = plan.get_steps()
        if not plan_steps:
            return {"success": False, "reason": "no_steps", "message": "计划中没有启用的步骤"}

        # 构建 PlannedStep 列表
        planned_steps: list[PlannedStep] = [
            PlannedStep(
                step_id=ps.step_id,
                action_id=ps.action_id,
                payload={},
                risk_level=ps.risk_level,
            )
            for ps in plan_steps
        ]

        # 状态机：pending → executing
        sm = self._get_or_create_sm(task)
        if task.status == STATUS_PENDING or task.status is None:
            start_task(sm)
            task.set_status(STATUS_EXECUTING)

        # 执行
        # V2.1 批次2（P0-4）：默认授权回调改为**拒绝**（保守缺省）。
        # 之前 `lambda s: {"allowed": True}` 使未传 authorize_callback 的调用默认放行，
        # 攻击面：任何进程内路径漏传回调即绕过审批。现在未提供回调 = 默认拒绝，
        # 调用方必须显式接 security.authorize_step（见 _router_schedule/_execute_plan 路径）。
        self._lifecycle.persist_agent_task(task)
        summary = self._executor.execute_plan(
            planned_steps,
            authorize_callback=authorize_callback or (lambda s: {"allowed": False, "blocked": True, "reason": "no_authorize_callback"}),
            task=task,
            delivery_channel=task.delivery_channel,
            user_profile=user_profile,
        )

        # 更新任务状态
        resolved = self._resolve_status(summary)
        if resolved == STATUS_COMPLETED:
            complete_task(sm)
            task.set_status(STATUS_COMPLETED)
        elif resolved == STATUS_FAILED:
            fail_task(sm, reason=self._first_failure_reason(summary))
            task.set_status(STATUS_FAILED)
        else:
            # "blocked" / "degraded" → 保留在 executing 状态，记录扩展状态
            task.set_status(resolved, result={"delivery": dict(summary.get("delivery") or {})})

        task.transition_history = list(sm.history)
        self._lifecycle.persist_agent_task(task)
        summary["task"] = task.to_dict()

        # 记录执行历史
        record_id = self._record_history(
            task_id=task.task_id,
            goal=task.goal,
            source=task.source,
            triggered_by="user",
            plan_id=plan.plan_id,
            steps=list(summary.get("outcomes", [])),
            summary=summary,
            success=(resolved == STATUS_COMPLETED),
        )

        # 审计日志
        self._audit_log(
            action_id=task.task_id,
            source=task.source,
            status=resolved,
            details={"goal": task.goal, "plan_id": plan.plan_id},
            record_id=record_id,
        )

        # 记录到记忆
        self._lifecycle.memory.append_decision(task.goal, planned_steps, summary)

        summary["success"] = resolved == STATUS_COMPLETED
        summary["record_id"] = record_id
        return summary

    def plan_and_execute(
        self,
        goal: str,
        *,
        source: str = "assistant.goal",
        user_profile: dict[str, Any] | None = None,
        confirm_before_execute: bool = True,
        authorized_actions: set[str] | None = None,
    ) -> dict[str, Any]:
        """一体化：规划 → （可选确认） → 执行。

        Args:
            goal: 任务目标
            source: 来源
            user_profile: 用户画像
            confirm_before_execute: 是否在执行前展示计划供用户确认
            authorized_actions: 预授权的 action_id 集合

        Returns:
            包含 plan_summary 和执行结果的字典
        """
        # 1. 规划
        steps = self._planner.plan_goal(goal, user_profile=user_profile)
        if not steps:
            return {"success": False, "reason": "no_steps", "message": "无法为目标生成执行步骤"}

        # 2. 创建任务
        profile = dict(user_profile or {}) or self._lifecycle.get_user_profile_snapshot()
        task = self._lifecycle.build_agent_task(goal, source=source, profile_snapshot=profile)
        task.set_steps(steps)

        # 3. 生成计划
        plan = self.generate_plan(goal, steps)

        result: dict[str, Any] = {
            "plan_summary": plan.get_plan_summary(),
            "task_id": task.task_id,
            "confirmed": False,
            "execution_result": None,
        }

        if not confirm_before_execute:
            # 无需确认，直接执行
            plan.approve()
            if self._plan_store is not None:
                self._plan_store.save_plan(plan)

        # 构建授权回调
        if authorized_actions is not None:

            def _auth(step, _source="assistant.plan"):
                return self._security.authorize_step(step, source=_source, pre_approved=authorized_actions)
        else:
            _auth = self._security.authorize_step

        # 执行
        exec_result = self.execute_with_plan(
            task,
            plan,
            authorize_callback=_auth,
            user_profile=profile,
        )
        result["execution_result"] = exec_result
        result["confirmed"] = plan.status in (PlanStatus.APPROVED, PlanStatus.EDITED)
        result["success"] = exec_result.get("success", False)
        return result

    # ═══════════════════════════════════════════════════════════
    # 2. 事务性多文件执行（Cursor Composer 风格）
    # ═══════════════════════════════════════════════════════════

    def execute_transactional(
        self,
        task: AgentTask,
        steps: list[PlannedStep],
        *,
        file_paths: list[str] | None = None,
        authorize_callback: Callable[[Any], dict[str, Any]] | None = None,
        user_profile: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """以原子事务方式执行任务。

        全部成功 → 统一提交
        部分失败 → 全部回滚并报告具体失败步骤
        """
        txn = FileTransaction(
            transaction_id=task.task_id,
            description=f"事务性任务: {task.goal}",
        )

        # 收集受影响文件路径
        all_file_paths: list[str] = list(file_paths or [])
        for step in steps:
            if isinstance(step.payload, dict):
                fp = step.payload.get("path") or step.payload.get("file_path") or ""
                if fp and fp not in all_file_paths:
                    all_file_paths.append(fp)

        # 对所有文件拍快照（事务外，供历史回滚使用）
        file_snapshots = SnapshotManager.snapshot_files(all_file_paths)

        # 将步骤添加到事务
        for step in steps:
            txn.add_step(
                step_id=step.step_id,
                # R0-B15：与 execute_with_plan（:316）对齐——未传 authorize_callback 时
                # 默认拒绝（曾为默认放行，事务路径绕过审批）。
                action=lambda s=step: self._executor.execute_step(
                    s,
                    authorize_callback=authorize_callback or (lambda st: {"allowed": False, "blocked": True, "reason": "no_authorize_callback"}),
                ),
                description=f"执行 {step.action_id}",
                file_paths=all_file_paths,
            )

        # 执行事务
        txn_result = txn.execute()

        # 更新任务状态
        sm = self._get_or_create_sm(task)
        if txn_result.get("success"):
            task.set_status(STATUS_COMPLETED)
            complete_task(sm)
            file_snapshots = {}  # 提交成功，清除快照
        else:
            task.set_status(STATUS_FAILED)
            fail_task(sm, reason=str(txn_result.get("message", "事务执行失败")))

        task.transition_history = list(sm.history)
        self._lifecycle.persist_agent_task(task)

        # 记录执行历史（含文件快照，支持回滚）
        outcomes = [
            {
                "step_id": fs.get("step_id", ""),
                "status": "failed" if fs.get("error") else "ok",
                "error": fs.get("error", ""),
            }
            for fs in txn_result.get("failed_steps", [])
        ]
        failed_count = len(txn_result.get("failed_steps", []))
        summary = {
            "success": txn_result.get("success", False),
            "total": len(steps),
            "ok": len(steps) - failed_count,
            "failed": failed_count,
            "outcomes": outcomes,
            "rolled_back": txn_result.get("rolled_back", False),
            "message": txn_result.get("message", ""),
            "task_id": task.task_id,
        }

        record_id = self._record_history(
            task_id=task.task_id,
            goal=task.goal,
            source=task.source,
            triggered_by="user",
            steps=outcomes,
            summary=summary,
            file_snapshots=file_snapshots,
            success=txn_result.get("success", False),
        )

        self._audit_log(
            action_id=task.task_id,
            source=task.source,
            status=STATUS_COMPLETED if txn_result.get("success") else STATUS_FAILED,
            details={"goal": task.goal, "transactional": True, "rolled_back": txn_result.get("rolled_back", False)},
            record_id=record_id,
        )

        summary["record_id"] = record_id
        return summary

    # ═══════════════════════════════════════════════════════════
    # 3. Checkpoint 恢复（崩溃恢复）
    # ═══════════════════════════════════════════════════════════

    def scan_unfinished_tasks(self, *, include_internal: bool = False) -> list[dict[str, Any]]:
        """扫描所有未完成的任务 checkpoint。

        用于应用启动后检测可恢复的任务。S1-3：内部事件任务（event:* 等）
        默认不进视图，``include_internal=True`` 恢复可见。
        """
        if self._checkpoint_manager is None:
            return []
        return self._checkpoint_manager.scan_unfinished_tasks(include_internal=include_internal)

    def get_unfinished_tasks_info(self, *, include_internal: bool = False) -> list[dict[str, Any]]:
        """获取未完成任务的可读信息，供 UI 展示。"""
        unfinished = self.scan_unfinished_tasks(include_internal=include_internal)
        if not unfinished:
            return []

        # 一次性加载所有任务数据
        task_data = self._data_store.load_json("assistant_agent_tasks", [])
        normalized = list(task_data) if isinstance(task_data, list) else []
        task_map: dict[str, dict[str, Any]] = {}
        for item in normalized:
            if isinstance(item, dict) and item.get("task_id"):
                task_map[item["task_id"]] = item

        result = []
        for cp in unfinished:
            tid = cp.get("task_id", "")
            task_info = task_map.get(tid)

            completed = len(cp.get("completed_steps", []))
            total = len((task_info or {}).get("steps", []))
            # S1-3 存量兼容双保险：旧 checkpoint 无 total_steps 字段时，
            # scan 层的步满过滤拿不到总数，这里用 task store 的 steps 再拦
            # 一道——executing 且步数已满 → 视图上等同已终结，不展示。
            if cp.get("state") == "executing" and total > 0 and completed >= total:
                continue
            result.append(
                {
                    "task_id": tid,
                    "goal": (task_info or {}).get("goal", tid),
                    "state": cp.get("state", ""),
                    "completed_steps": completed,
                    "total_steps": total or max(completed, 1),
                    "progress": f"{completed}/{total or '?'}",
                    "checkpoint_version": cp.get("version", 0),
                    "checkpoint_id": cp.get("checkpoint_id", ""),
                    "created_at": cp.get("created_at", ""),
                }
            )
        return result

    def recover_task(self, task_id: str, *, confirmed: bool = True) -> dict[str, Any]:
        """从 checkpoint 恢复任务执行。

        需要用户确认后才执行恢复。

        Args:
            task_id: 任务 ID
            confirmed: 用户是否确认恢复

        Returns:
            包含恢复详情和执行结果的字典
        """
        if not confirmed:
            # 用户拒绝恢复 → 标记为已取消
            def _cancel_updater(tasks: Any) -> list[dict[str, Any]]:
                normalized = list(tasks) if isinstance(tasks, list) else []
                for item in normalized:
                    if isinstance(item, dict) and item.get("task_id") == task_id:
                        item["status"] = STATUS_CANCELLED
                        break
                return normalized

            self._data_store.update_json("assistant_agent_tasks", [], _cancel_updater)
            return {"recovered": False, "reason": "user_declined", "task_id": task_id}

        if self._checkpoint_manager is None or self.checkpoint_adapter is None:
            return {"recovered": False, "reason": "checkpoint_unavailable", "task_id": task_id}

        # 获取最新的 checkpoint
        cp = self._checkpoint_manager.recover_task(task_id)
        if cp is None:
            return {"recovered": False, "reason": "no_checkpoint_found", "task_id": task_id}

        # 加载任务
        task_data = self._data_store.load_json("assistant_agent_tasks", [])
        normalized = list(task_data) if isinstance(task_data, list) else []
        task_dict = None
        for item in normalized:
            if isinstance(item, dict) and item.get("task_id") == task_id:
                task_dict = item
                break

        if task_dict is None:
            return {"recovered": False, "reason": "task_not_found", "task_id": task_id}

        # 从 checkpoint 恢复任务
        task = AgentTask(
            goal=task_dict.get("goal", ""),
            task_id=task_dict.get("task_id", task_id),
            source=task_dict.get("source", "assistant.goal"),
            status=cp.get("state", STATUS_PAUSED),
        )
        task.completed_step_indices = list(cp.get("completed_steps", []))
        task.checkpoint_id = cp.get("checkpoint_id", "")
        task.checkpoint_version = int(cp.get("version", 0))
        task.steps = list(task_dict.get("steps", []))
        task.profile_snapshot = dict(cp.get("context_snapshot", {}))

        # 计算剩余步骤
        total_steps_count = len(task.steps)
        completed_indices = set(int(i) for i in cp.get("completed_steps", []) if isinstance(i, (int, str)))
        remaining_indices = [i for i in range(total_steps_count) if i not in completed_indices]

        remaining_steps = []
        for idx in remaining_indices:
            s = task.steps[idx]
            remaining_steps.append(
                PlannedStep(
                    step_id=str(s.get("step_id", f"step_{idx + 1}")),
                    action_id=str(s.get("action_id", "")),
                    payload=dict(s.get("payload", {})),
                    risk_level=str(s.get("risk_level", "medium")),
                )
            )

        # 恢复执行
        sm = self._get_or_create_sm(task)
        task.set_status(STATUS_EXECUTING)
        # paused → executing (如果之前是 paused)
        if cp.get("state") == STATUS_PAUSED:
            resume_task(sm)

        self._lifecycle.persist_agent_task(task)

        if remaining_steps:
            # P0-fix(2): 恢复执行也必须走授权闸门，不能再 lambda 放行
            # 之前 `authorize_callback=lambda s: {"allowed": True}` 让所有
            # 高风险步骤（file.write/os.remove 等）在 recover 时绕过 sandbox 审批
            if self._security is not None:

                def _recover_auth(step, _source="assistant.recover"):
                    return self._security.authorize_step(step, source=_source)
            else:
                # 兜底：无 security 服务时拒绝所有 high/destructive risk 步骤（保守策略）
                def _recover_auth(step, _source="assistant.recover"):
                    if getattr(step, "risk_level", "low") in ("high", "destructive"):
                        return {"allowed": False, "reason": "security_service_unavailable_high_risk"}
                    return {"allowed": True}

            summary = self._executor.execute_plan(
                remaining_steps,
                authorize_callback=_recover_auth,
                task=task,
                delivery_channel=task_dict.get("delivery_channel", "inbox"),
                user_profile=task.profile_snapshot,
            )
        else:
            summary = {"ok": 0, "failed": 0, "blocked": 0, "degraded": 0, "outcomes": [], "total": 0}
            complete_task(sm)
            task.set_status(STATUS_COMPLETED)

        # 更新最终状态
        resolved = self._resolve_status(summary)
        if resolved == STATUS_COMPLETED:
            complete_task(sm)
            task.set_status(STATUS_COMPLETED)
        elif resolved == STATUS_FAILED:
            fail_task(sm, reason=self._first_failure_reason(summary))
            task.set_status(STATUS_FAILED)

        task.transition_history = list(sm.history)
        self._lifecycle.persist_agent_task(task)
        summary["task"] = task.to_dict()

        # 记录历史
        record_id = self._record_history(
            task_id=task.task_id,
            goal=task.goal,
            source=task.source,
            triggered_by="system",
            steps=list(summary.get("outcomes", [])),
            summary=summary,
            success=(resolved == STATUS_COMPLETED),
        )

        self._audit_log(
            action_id=task.task_id,
            source="recovery",
            triggered_by="system",
            status=resolved,
            details={
                "goal": task.goal,
                "recovered": True,
                "checkpoint_version": cp.get("version", 0),
                "remaining_steps": len(remaining_steps),
            },
            record_id=record_id,
        )

        return {
            "recovered": True,
            "task_id": task_id,
            "completed_before": len(completed_indices),
            "remaining_steps": len(remaining_steps),
            "total_steps": total_steps_count,
            "execution_summary": summary,
            "record_id": record_id,
        }

    # ═══════════════════════════════════════════════════════════
    # 4. 执行历史 + 重放 + 回滚
    # ═══════════════════════════════════════════════════════════

    def get_history(
        self,
        *,
        task_id: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """查询执行历史。"""
        if self._history_store is None:
            return []
        records = self._history_store.list_records(task_id=task_id, limit=limit, offset=offset)
        return [r.to_dict() for r in records]

    def get_replay_data(self, record_id: str) -> dict[str, Any] | None:
        """获取执行重放数据（逐步骤展示）。"""
        if self._history_store is None:
            return None
        return self._history_store.get_replay_data(record_id)

    def rollback_execution(self, record_id: str) -> dict[str, Any]:
        """回滚指定执行记录。

        前提：
        - 记录状态为 completed
        - 记录包含文件快照
        - 记录标记为可逆 (reversible)

        返回回滚结果。
        """
        if self._history_store is None:
            return {"success": False, "reason": "history_store_unavailable"}

        result = self._history_store.rollback_record(record_id)

        self._audit_log(
            action_id=f"rollback:{record_id}",
            source="rollback",
            status="completed" if result.get("success") else "failed",
            details={"record_id": record_id, "result": result},
        )

        return result

    # ═══════════════════════════════════════════════════════════
    # 5. 状态管理（暂停/恢复/取消）
    # ═══════════════════════════════════════════════════════════

    def pause_task(self, task: AgentTask, *, reason: str = "用户暂停") -> bool:
        sm = self._get_or_create_sm(task)
        if not pause_task(sm, reason=reason):
            return False
        task.set_status(STATUS_PAUSED)
        task.transition_history = list(sm.history)
        self._lifecycle.persist_agent_task(task)
        # 保存 checkpoint（暂停前保存状态）
        if self._checkpoint_manager is not None:
            self._checkpoint_manager.save_checkpoint(
                task_id=task.task_id,
                state=STATUS_PAUSED,
                completed_steps=task.completed_step_indices,
                step_results=[],
                context_snapshot=task.profile_snapshot,
            )
        return True

    def resume_task(self, task: AgentTask, *, reason: str = "用户恢复执行") -> bool:
        sm = self._get_or_create_sm(task)
        if not resume_task(sm, reason=reason):
            return False
        task.set_status(STATUS_EXECUTING)
        task.transition_history = list(sm.history)
        self._lifecycle.persist_agent_task(task)
        return True

    def cancel_task(self, task: AgentTask, *, reason: str = "用户取消") -> bool:
        sm = self._get_or_create_sm(task)
        if not cancel_task(sm, reason=reason):
            return False
        task.set_status(STATUS_CANCELLED)
        task.transition_history = list(sm.history)
        self._lifecycle.persist_agent_task(task)
        return True

    # ═══════════════════════════════════════════════════════════
    # 6. 状态变更事件通知
    # ═══════════════════════════════════════════════════════════

    def on_state_change(self, task_id: str, from_state: TaskState, to_state: TaskState, metadata: dict[str, Any]) -> None:
        """状态变更回调：通知事件层、全局 EventBus 和审计日志。"""
        # 通过事件层投递状态变更通知
        if self._events is not None:
            try:
                self._events.emit_system_event(
                    f"task.state.{to_state.value}",
                    payload={
                        "task_id": task_id,
                        "from_state": from_state.value,
                        "to_state": to_state.value,
                        "reason": metadata.get("reason", ""),
                    },
                )
            except Exception as exc:
                logger.warning("状态变更事件通知失败: %s", exc, exc_info=True)

        # 全局 EventBus 广播
        try:
            event_bus.emit(
                AppEvent(
                    f"task.state.{to_state.value}",
                    payload={
                        "task_id": task_id,
                        "from_state": from_state.value,
                        "to_state": to_state.value,
                        "reason": metadata.get("reason", ""),
                    },
                    source="task_manager",
                )
            )
        except Exception as exc:
            logger.debug("task event emit skipped: %s", exc)

        # 审计日志
        self._audit_log(
            action_id=task_id,
            source="state_machine",
            status=to_state.value,
            details={"from": from_state.value, "to": to_state.value, "reason": metadata.get("reason", "")},
        )

    def register_state_listener(self, task: AgentTask) -> None:
        """为任务注册状态变更监听器。"""
        sm = self._get_or_create_sm(task)
        sm.add_listener(self.on_state_change)

    # ═══════════════════════════════════════════════════════════
    # 7. Checkpoint 清理
    # ═══════════════════════════════════════════════════════════

    def cleanup_checkpoints(self, *, before_days: int | None = None) -> int:
        """归档旧 checkpoint。返回归档数量。"""
        if self._checkpoint_manager is None:
            return 0
        return self._checkpoint_manager.archive_old_checkpoints(before_days=before_days)

    def delete_task_checkpoints(self, task_id: str) -> int:
        """删除指定任务的所有 checkpoint。"""
        if self._checkpoint_manager is None:
            return 0
        return self._checkpoint_manager.delete_task_checkpoints(task_id)

    # ═══════════════════════════════════════════════════════════
    # 8. CronService 桥接：把任务调度到定时任务里
    # ═══════════════════════════════════════════════════════════

    def schedule_task_reminder(
        self,
        task_id: str,
        *,
        schedule: str = "",
        run_at: str = "",
        repeat: str = "none",
        reminder_text: str = "",
        max_retries: int = 0,
        retry_backoff: int = 60,
    ) -> dict[str, Any]:
        """为任务注册一个 CronService 定时提醒/触发。

        实际会创建一个 action_id 为 ``task.execute`` 的 cron job，
        payload 中携带 task_id，到点由 CronService 触发任务执行。
        """
        if self._cron_service is None:
            return {"success": False, "reason": "cron_service_unavailable"}
        if not task_id:
            return {"success": False, "reason": "task_id_required"}

        try:
            job = self._cron_service.add_job(
                name=f"任务提醒: {task_id}",
                action_id="task.execute",
                payload={
                    "task_id": task_id,
                    "reminder_text": reminder_text or "",
                },
                schedule=schedule,
                run_at=run_at,
                repeat=repeat,
                max_retries=max_retries,
                retry_backoff=retry_backoff,
            )
            return {"success": True, "job": job}
        except Exception as exc:
            logger.warning("schedule_task_reminder failed: %s", exc, exc_info=True)
            return {"success": False, "reason": str(exc)}

    def unschedule_task(self, task_id: str) -> dict[str, Any]:
        """移除所有关联到指定 task_id 的 cron job。"""
        if self._cron_service is None:
            return {"success": False, "reason": "cron_service_unavailable"}
        removed = []
        try:
            for job in list(self._cron_service._jobs):
                payload = job.payload or {}
                if str(payload.get("task_id") or "") == str(task_id):
                    if self._cron_service.remove_job(job.job_id):
                        removed.append(job.job_id)
            return {"success": True, "removed": removed, "count": len(removed)}
        except Exception as exc:
            logger.warning("unschedule_task failed: %s", exc, exc_info=True)
            return {"success": False, "reason": str(exc)}

    def get_task_cron_jobs(self, task_id: str) -> list[dict[str, Any]]:
        """获取关联到指定 task_id 的所有 cron job。"""
        if self._cron_service is None:
            return []
        try:
            return [job.to_dict() for job in self._cron_service._jobs if str((job.payload or {}).get("task_id") or "") == str(task_id)]
        except Exception:
            return []
