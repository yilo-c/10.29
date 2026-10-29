from __future__ import annotations

import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from datetime import datetime, timezone, timedelta
from typing import Any

import logging

from assistant.models import AgentTask, PlannedStep, StepEvent
from core.events import AppEvent, event_bus
from core.observability import SEVERITY_DEGRADED, SEVERITY_INFO
from core.settings_schema import KEY_WRITE_GUARD_DEGRADE_TO_READONLY

logger = logging.getLogger("siyi.assistant.execution")


class AssistantExecutionLayer:
    """执行?层：重试、超鏃躲?佺啍鏂?+ 连续失败降级到只璇汇??

    鏂板?锛?
    - Checkpoint 持久化：姣忔?执行?后保瀛?checkpoint
    - 事务性执行：全部成功则提交，失败则回婊?
    """

    def __init__(
        self, data_store, capability_router, observability_service=None, settings_repository=None, inbox_service=None, checkpoint_manager=None
    ):
        self.data_store = data_store
        self.capability_router = capability_router
        self.observability_service = observability_service
        self.settings_repository = settings_repository
        self.inbox_service = inbox_service
        self.checkpoint_manager = checkpoint_manager

    @staticmethod
    def _emit_step_event(
        event_type: str,
        step_index: int,
        action_id: str,
        *,
        status: str = "",
        error: str = "",
        description: str = "",
        payload: dict[str, Any] | None = None,
    ) -> None:
        """广播执行?姝ラ?事件鍒?EventBus。"""
        event = StepEvent(
            type=event_type,
            step_index=step_index,
            action_id=action_id,
            status=status,
            error=error,
            description=description,
            payload=payload or {},
        )
        event_bus.emit(
            AppEvent(
                event_type=f"execution.step.{event_type}",
                source="assistant.execution",
                payload={
                    "step_index": step_index,
                    "action_id": action_id,
                    "status": status,
                    "error": error,
                    "description": description,
                    "timestamp": event.timestamp,
                },
            )
        )

    def execute_plan(
        self,
        steps: list[PlannedStep],
        authorize_callback: Callable[[PlannedStep], dict[str, Any]],
        task: AgentTask | None = None,
        delivery_channel: str | None = None,
        user_profile: dict[str, Any] | None = None,
        cancellation_event: threading.Event | None = None,
    ) -> dict[str, Any]:
        outcomes = []
        consecutive_failures = 0
        degrade_to_readonly = self._get_setting(KEY_WRITE_GUARD_DEGRADE_TO_READONLY, True)
        is_checkpoint_task = task is not None and self.checkpoint_manager is not None

        for step_index, step in enumerate(steps):
            if cancellation_event and cancellation_event.is_set():
                self._emit_step_event(
                    "step_blocked",
                    step_index,
                    step.action_id,
                    status="cancelled",
                    description=step.description or step.action_id,
                    payload={"reason": "user_cancelled"},
                )
                outcomes.append(
                    {
                        "step_id": step.step_id,
                        "action_id": step.action_id,
                        "status": "cancelled",
                        "reason": "user_cancelled",
                    }
                )
                break

            if degrade_to_readonly and consecutive_failures >= 3 and step.risk_level in ("medium", "high"):
                self._emit_step_event(
                    "step_blocked",
                    step_index,
                    step.action_id,
                    status="degraded_readonly",
                    description=step.description or step.action_id,
                    payload={"reason": "consecutive_failures_exceeded"},
                )
                outcomes.append(
                    {
                        "step_id": step.step_id,
                        "action_id": step.action_id,
                        "status": "degraded_readonly",
                        "reason": "consecutive_failures_exceeded",
                        "suggestion": "连续失败已触发降级，仅允许只读操浣?",
                    }
                )
                continue

            auth = authorize_callback(step)
            if not auth.get("allowed"):
                reason = auth.get("reason", "not_allowed")
                self._emit_step_event(
                    "step_blocked",
                    step_index,
                    step.action_id,
                    status="blocked",
                    description=step.description or step.action_id,
                    payload={"reason": reason},
                )
                outcomes.append(
                    {
                        "step_id": step.step_id,
                        "action_id": step.action_id,
                        "status": "blocked",
                        "reason": reason,
                        "warnings": auth.get("warnings", []),
                    }
                )
                continue

            state = self._load_circuit_state(step.action_id)
            if state.get("open_until"):
                try:
                    open_until = datetime.fromisoformat(state["open_until"])
                    if datetime.now(timezone.utc) < open_until:
                        self._emit_step_event(
                            "step_blocked",
                            step_index,
                            step.action_id,
                            status="open_circuit",
                            description=step.description or step.action_id,
                            payload={"open_until": state["open_until"]},
                        )
                        outcomes.append(
                            {
                                "step_id": step.step_id,
                                "action_id": step.action_id,
                                "status": "open_circuit",
                                "reason": "circuit_breaker_open",
                            }
                        )
                        continue
                except Exception:
                    logger.warning("解析熔断器状态失璐?(action_id=%s)", step.action_id)

            self._emit_step_event(
                "step_start",
                step_index,
                step.action_id,
                description=step.description or step.action_id,
                payload={"risk_level": step.risk_level, "timeout_seconds": step.timeout_seconds},
            )

            result = self._execute_with_retry(step)
            outcomes.append(result)
            self._update_circuit(step.action_id, result.get("status") == "ok")

            step_status = result.get("status", "failed")
            step_error = result.get("error", "")
            self._emit_step_event(
                "step_end",
                step_index,
                step.action_id,
                status=step_status,
                description=step.description or step.action_id,
                error=step_error,
                payload={"attempt": result.get("attempt")},
            )

            if step_status == "ok":
                consecutive_failures = 0
            else:
                consecutive_failures += 1

            # 鈹?鈹? Checkpoint 持久化（姣忔?执行?后） 鈹?鈹?鈹?鈹?鈹?鈹?鈹?鈹?鈹?鈹?
            if is_checkpoint_task:
                self._save_step_checkpoint(task, step_index, result, outcomes, user_profile)

        summary = {
            "executed_at": datetime.now(timezone.utc).isoformat(),
            "total": len(outcomes),
            "ok": len([item for item in outcomes if item.get("status") == "ok"]),
            "failed": len([item for item in outcomes if item.get("status") in {"failed", "timeout"}]),
            "blocked": len([item for item in outcomes if item.get("status") in {"blocked", "open_circuit"}]),
            "cancelled": len([item for item in outcomes if item.get("status") == "cancelled"]),
            "degraded": len([item for item in outcomes if item.get("status") == "degraded_readonly"]),
            "outcomes": outcomes,
        }
        if task is not None:
            summary["task_id"] = task.task_id
        summary["delivery"] = self._build_delivery(
            summary,
            delivery_channel=delivery_channel or getattr(task, "delivery_channel", None),
            user_profile=user_profile,
            task=task,
        )
        self._deliver_summary(summary, task=task)
        try:
            event_bus.emit(
                AppEvent(
                    event_type="execution.complete",
                    source="assistant.execution",
                    payload={
                        "total": summary["total"],
                        "ok": summary["ok"],
                        "failed": summary["failed"],
                        "message": summary.get("delivery", {}).get("content", ""),
                    },
                )
            )
        except Exception:
            logger.debug("Failed to emit execution.complete event")
        self.data_store.update_json(
            "assistant_execution_runs",
            [],
            lambda runs: (list(runs) if isinstance(runs, list) else [])[-499:] + [summary],
        )
        return summary

    def _build_delivery(
        self,
        summary: dict[str, Any],
        *,
        delivery_channel: str | None,
        user_profile: dict[str, Any] | None,
        task: AgentTask | None,
    ) -> dict[str, Any]:
        channel = str(delivery_channel or (user_profile or {}).get("preferred_delivery") or "chat").strip() or "chat"
        return {
            "channel": channel,
            "title": self._build_delivery_title(task),
            "content": self._build_delivery_content(summary, task=task),
        }

    @staticmethod
    def _build_delivery_title(task: AgentTask | None) -> str:
        if task is None:
            return "助手任务结果"
        return f"任务完成锛{task.goal}"

    def _build_delivery_content(self, summary: dict[str, Any], *, task: AgentTask | None) -> str:
        for outcome in list(summary.get("outcomes") or []):
            if outcome.get("status") != "ok":
                continue
            payload = outcome.get("payload") or {}
            if isinstance(payload, dict):
                for key in ("briefing", "content", "message", "summary"):
                    value = str(payload.get(key) or "").strip()
                    if value:
                        return value
        goal = getattr(task, "goal", "当前任务") if task is not None else "当前任务"
        return (
            f"{goal} 已完鎴愩?"
            f"共执琛?{int(summary.get('total') or 0)} 步，"
            f"成功 {int(summary.get('ok') or 0)} 步，"
            f"失败 {int(summary.get('failed') or 0)} 姝ャ?"
        )

    def _deliver_summary(self, summary: dict[str, Any], *, task: AgentTask | None) -> None:
        if self.inbox_service is None:
            return
        delivery = dict(summary.get("delivery") or {})
        channel = str(delivery.get("channel") or "").strip()
        if not channel:
            return

        # chat 通道直接鐢卞?灞傚?璇濋?昏緫处理，不进入统一交付
        if channel == "chat":
            return

        goal = getattr(task, "goal", "助手任务") if task is not None else "助手任务"
        total = int(summary.get("total") or 0)
        ok_count = int(summary.get("ok") or 0)
        failed_count = int(summary.get("failed") or 0)
        blocked_count = int(summary.get("blocked") or 0)
        success = failed_count == 0 and blocked_count == 0

        result_payload = {
            "summary": str(delivery.get("content") or "").strip(),
            "reason": self._first_non_ok_reason(summary),
            "delivery": delivery,
        }

        # 为字骞曢?氶亾构建绠?鐭?繘度摘瑕?
        subtitle_text = self._build_subtitle_progress(
            goal=goal,
            total=total,
            ok=ok_count,
            failed=failed_count,
            blocked=blocked_count,
        )
        result_payload["subtitle"] = subtitle_text

        try:
            # 灏嗛?氶亾显式浼犻?掔粰 deliver_task_result，确保结果路由到正确通道
            delivered = self.inbox_service.deliver_task_result(
                goal=goal,
                result=result_payload,
                success=success,
                action_id=getattr(task, "task_id", ""),
                channel=channel,
            )
            # 记录实际交付结果
            summary["delivery"]["inbox_item"] = delivered
            summary["delivery"]["subtitle_text"] = subtitle_text
        except Exception:
            logger.warning("交付任务结果鍒?inbox 失败")

    @staticmethod
    def _build_subtitle_progress(
        *,
        goal: str,
        total: int,
        ok: int,
        failed: int,
        blocked: int,
    ) -> str:
        """Build task progress subtitle for display."""
        goal_text = str(goal or "").strip() or "任务"

        if total == 0 or (failed == 0 and blocked == 0 and ok == total):
            return f"✅ {goal_text}：{ok}/{total}"

        parts = [f"{goal_text}：{ok}/{total} 步成功"]
        if failed:
            parts.append(f"{failed} 步失败")
        if blocked:
            parts.append(f"{blocked} 步被拦截")
        return "⚠️ " + "，".join(parts) + "。"

    @staticmethod
    def _first_non_ok_reason(summary: dict[str, Any]) -> str:
        for outcome in list(summary.get("outcomes") or []):
            if outcome.get("status") == "ok":
                continue
            reason = str(outcome.get("reason") or outcome.get("error") or "").strip()
            if reason:
                return reason
        return ""

    def _get_setting(self, key: str, default: Any) -> Any:
        if self.settings_repository is None:
            return default
        return self.settings_repository.get_setting(key, default)

    def _execute_with_retry(self, step: PlannedStep) -> dict[str, Any]:
        attempts = max(1, int(step.retries) + 1)
        last_error = ""
        for attempt in range(1, attempts + 1):
            try:
                with ThreadPoolExecutor(max_workers=1) as pool:
                    future = pool.submit(self.capability_router.execute, step)
                    payload = future.result(timeout=max(1, int(step.timeout_seconds)))
                self._track("assistant.execution.ok", SEVERITY_INFO, {"action_id": step.action_id, "attempt": attempt})
                return {
                    "step_id": step.step_id,
                    "action_id": step.action_id,
                    "status": "ok",
                    "attempt": attempt,
                    "payload": payload,
                }
            except FutureTimeoutError:
                last_error = f"timeout>{step.timeout_seconds}s"
                self._track("assistant.execution.timeout", SEVERITY_DEGRADED, {"action_id": step.action_id, "attempt": attempt})
            except Exception as exc:
                last_error = str(exc)
                self._track("assistant.execution.failed", SEVERITY_DEGRADED, {"action_id": step.action_id, "attempt": attempt, "error": str(exc)})
        return {
            "step_id": step.step_id,
            "action_id": step.action_id,
            "status": "timeout" if "timeout" in last_error else "failed",
            "error": last_error,
        }

    def _load_circuit_state(self, action_id: str) -> dict[str, Any]:
        state_map = self.data_store.load_json("assistant_rules", {})
        if not isinstance(state_map, dict):
            state_map = {}
        value = state_map.get(f"circuit::{action_id}")
        if isinstance(value, dict):
            return value
        return {"failure_count": 0, "open_until": None}

    def _update_circuit(self, action_id: str, success: bool) -> None:
        key = f"circuit::{action_id}"

        def _updater(state_map: dict[str, Any]) -> dict[str, Any]:
            normalized = dict(state_map) if isinstance(state_map, dict) else {}
            state = normalized.get(key)
            if not isinstance(state, dict):
                state = {"failure_count": 0, "open_until": None}

            if success:
                state = {"failure_count": 0, "open_until": None}
            else:
                failures = int(state.get("failure_count") or 0) + 1
                state["failure_count"] = failures
                if failures >= 3:
                    state["open_until"] = (datetime.now(timezone.utc) + timedelta(minutes=5)).replace(microsecond=0).isoformat()
            normalized[key] = state
            return normalized

        self.data_store.update_json("assistant_rules", {}, _updater)

    # 鈹?鈹? Checkpoint 持久鍖?鈹?鈹?鈹?鈹?鈹?鈹?鈹?鈹?鈹?鈹?鈹?鈹?鈹?鈹?鈹?鈹?鈹?鈹?鈹?鈹?鈹?鈹?鈹?鈹?鈹?鈹?鈹?鈹?鈹?鈹?鈹?鈹?鈹?
    def _save_step_checkpoint(
        self,
        task: AgentTask,
        step_index: int,
        step_result: dict[str, Any],
        all_outcomes: list[dict[str, Any]],
        user_profile: dict[str, Any] | None,
    ) -> None:
        """姣忔?执行?后保瀛?checkpoint。"""
        if self.checkpoint_manager is None:
            return
        try:
            from assistant.checkpoint import DataStoreCheckpointAdapter

            adapter = DataStoreCheckpointAdapter(self.checkpoint_manager)
            adapter.save_on_step_complete(
                task_id=task.task_id,
                state=task.status,
                step_index=step_index,
                step_result=step_result,
                context_snapshot=user_profile,
                previous_results=all_outcomes[:-1] if len(all_outcomes) > 1 else None,
                # S1-3：传入总步数与 goal，让 checkpoint 写侧做收尾判定
                # （步满落 completed）与内部事件标记（event:* → internal）。
                total_steps=len(task.steps or []),
                goal=task.goal,
            )
        except Exception as exc:
            logger = logging.getLogger("siyi.assistant.execution")
            logger.warning("Checkpoint 保存失败锛堟?楠?%d锛? %s", step_index, exc)

    def execute_step(
        self,
        step: PlannedStep,
        authorize_callback: Callable[[PlannedStep], dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """执行?单个姝ラ?，返鍥炶?姝ラ?的执行结鏋溿??

        供事鍔℃?褎墽行使鐢ㄢ?斺?擳askManager 鐨?execute_transactional()
        通过 FileTransaction 鍖呰?此方法以实现原子鎬褋??

        Args:
            step: 要执行的姝ラ?
            authorize_callback: 鍙??夌殑授权妫?查，返回 {"allowed": bool, ...}

        Returns:
            姝ラ?执行?结果字典
        """
        # 授权妫?鏌?
        if authorize_callback is not None:
            auth = authorize_callback(step)
            if not auth.get("allowed"):
                return {
                    "step_id": step.step_id,
                    "action_id": step.action_id,
                    "status": "blocked",
                    "reason": auth.get("reason", "not_allowed"),
                    "warnings": auth.get("warnings", []),
                }

        # 熔断妫?鏌?
        state = self._load_circuit_state(step.action_id)
        if state.get("open_until"):
            try:
                open_until = datetime.fromisoformat(state["open_until"])
                if datetime.now(timezone.utc) < open_until:
                    return {
                        "step_id": step.step_id,
                        "action_id": step.action_id,
                        "status": "open_circuit",
                        "reason": "circuit_breaker_open",
                    }
            except Exception:
                logger.warning("解析熔断器状态失璐?(action_id=%s)", step.action_id)

        # 执行?（带重试锛?
        result = self._execute_with_retry(step)
        self._update_circuit(step.action_id, result.get("status") == "ok")
        return result

    def _track(self, event_name: str, severity: str, payload: dict[str, Any]) -> None:
        if self.observability_service is None:
            return
        try:
            self.observability_service.track_event(event_name, severity=severity, payload=payload)
        except Exception:
            logger.warning("跟踪鍙??娴嬫?褌簨件失璐? %s", event_name)
