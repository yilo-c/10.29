"""Schedule/cron/proactive capability mixin — extracted from capability_router.py."""

from __future__ import annotations

import uuid
import logging
from typing import TYPE_CHECKING, Any, Protocol

from core import cron_expr
from core.settings_schema import KEY_PROACTIVE_ENABLED

if TYPE_CHECKING:
    from .models import PlannedStep

logger = logging.getLogger("siyi.assistant.autonomy.router.schedule")


class ScheduleRouterHost(Protocol):
    """ScheduleRouterMixin 依赖的宿主契约 — AssistantCapabilityRouter 由 __init__ 注入。"""

    data_store: Any
    observability_service: Any
    settings_repository: Any
    user_profile_service: Any
    inbox_service: Any
    task_repository: Any
    note_repository: Any
    ai_client: Any
    environment_probe: Any
    evolution_orchestrator: Any
    tech_watch_collector: Any
    tech_watch_judge: Any

    def execute(self, step: PlannedStep, context: dict[str, Any] | None = None) -> dict[str, Any]: ...
    def _get_tech_watch_judge_day(self) -> str: ...


class ScheduleRouterMixin:
    """Cron, scheduling, proactive, and suggestion capability handlers."""

    if TYPE_CHECKING:
        data_store: Any
        observability_service: Any
        settings_repository: Any
        user_profile_service: Any
        inbox_service: Any
        task_repository: Any
        note_repository: Any
        ai_client: Any
        environment_probe: Any
        evolution_orchestrator: Any
        tech_watch_collector: Any
        tech_watch_judge: Any

        def execute(self, step: PlannedStep, context: dict[str, Any] | None = None) -> dict[str, Any]: ...
        def _get_tech_watch_judge_day(self) -> str: ...

    def _get_cron_service(self):
        if hasattr(self, "_injected_cron_service") and self._injected_cron_service is not None:
            return self._injected_cron_service
        try:
            from services.cron_service import CronService

            if not hasattr(self, "_cron_service_cached"):
                self._cron_service_cached = CronService(
                    data_store=self.data_store,
                    observability_service=self.observability_service,
                    executor=self._execute_cron_action,
                    settings_repository=self.settings_repository,
                    event_bus=getattr(self, "_event_bus", None),
                )
                self._cron_service_cached.ensure_default_jobs()
                self._ensure_evolution_scan_job()
                self._ensure_tech_watch_jobs()
                self._ensure_proactive_job()
                self._cron_service_cached.start()
            return self._cron_service_cached
        except Exception as exc:
            logger.warning("CronService 初始化失败: %s", exc)
            return None

    def _ensure_evolution_scan_job(self):
        if self.evolution_orchestrator is None:
            return
        try:
            existing = [j for j in self._cron_service_cached._jobs if j.action_id == "evolution.daily_scan"]
            if not existing:
                self._cron_service_cached.add_job(
                    name="技术动态扫描",
                    action_id="evolution.daily_scan",
                    payload={},
                    schedule="0 7 * * *",
                    repeat="cron",
                )
                logger.info("已注册每日技术动态扫描任务(07:00)")
        except Exception as exc:
            logger.warning("注册技术动态扫描任务失败: %s", exc, exc_info=True)

    def _ensure_tech_watch_jobs(self):
        if self.tech_watch_collector is None and self.tech_watch_judge is None:
            return
        try:
            existing_collect = [j for j in self._cron_service_cached._jobs if j.action_id == "tech_watch.collect"]
            if not existing_collect:
                self._cron_service_cached.add_job(
                    name="技术瞭望采集",
                    action_id="tech_watch.collect",
                    payload={},
                    schedule="30 7 * * *",
                    repeat="cron",
                )
                logger.info("已注册每日技术瞭望采集任务(07:30)")
            judge_day = self._get_tech_watch_judge_day()
            # 批3 · P2-1：与 _context_cron 同源——星期写**名字**，不经数字中转。
            day_name = cron_expr.coerce_weekday_name(judge_day)
            existing_judge = [j for j in self._cron_service_cached._jobs if j.action_id == "tech_watch.judge"]
            if not existing_judge:
                self._cron_service_cached.add_job(
                    name="技术瞭望评估",
                    action_id="tech_watch.judge",
                    payload={},
                    schedule=f"0 9 * * {day_name}",
                    repeat="cron",
                )
                logger.info("已注册每周技术瞭望评估任务(%s 09:00)", day_name)
        except Exception as exc:
            logger.warning("注册技术瞭望定时任务失败: %s", exc, exc_info=True)

    def _ensure_proactive_job(self):
        try:
            existing = [j for j in self._cron_service_cached._jobs if j.action_id == "proactive.tick"]
            if not existing:
                self._cron_service_cached.add_job(
                    name="主动代理触发器",
                    action_id="proactive.tick",
                    payload={},
                    schedule="*/30 7-23 * * *",
                    repeat="cron",
                )
                logger.info("已注册主动代理触发器 (每 30 分钟 07:00-23:00)")
        except Exception as exc:
            logger.warning("注册主动代理触发器失败: %s", exc, exc_info=True)

    def _proactive_tick(self, payload: dict[str, Any]) -> dict[str, Any]:
        if self.settings_repository is None:
            return {"status": "skipped", "reason": "settings_repository not configured"}
        enabled = bool(self.settings_repository.get_setting(KEY_PROACTIVE_ENABLED, True))
        if not enabled:
            return {"status": "skipped", "reason": "proactive_disabled_by_user"}
        try:
            try:
                from services.habit_learner import HabitLearner

                hl = HabitLearner(self.data_store, self.user_profile_service)
                learn_result = hl.scan_and_learn()
                discoveries = learn_result.get("discoveries", {})
                if discoveries:
                    logger.info("画像习惯学习发现: %s", discoveries)
                    if self.inbox_service is not None:
                        parts = []
                        if discoveries.get("hour_patterns"):
                            parts.append(f"活跃时段：{'、'.join(discoveries['hour_patterns'][:3])}")
                        if discoveries.get("top_tools"):
                            parts.append(f"常用功能：{'、'.join(discoveries['top_tools'][:3])}")
                        if parts:
                            self.inbox_service.deliver_suggestion(
                                text="小问开始了解你的使用习惯了",
                                action="habit.status",
                                reason="、".join(parts),
                            )
            except Exception as exc:
                logger.debug("画像习惯学习跳过: %s", exc)
            from services.proactive_triggers import ProactiveTriggerEngine

            engine = ProactiveTriggerEngine(
                inbox_service=self.inbox_service,
                desktop_doctor=getattr(self, "desktop_doctor_service", None),
                workbench_service=getattr(self, "workbench_service", None),
                personal_task_repository=self.task_repository,
                personal_note_repository=self.note_repository,
                weather_service=getattr(self, "weather_service", None),
                observability=self.observability_service,
            )
            trigger_setting = self.settings_repository.get_setting("proactive_enabled_triggers", None)
            if isinstance(trigger_setting, list):
                engine.set_enabled_triggers(set(trigger_setting))
            # 第二档 #5：订阅 EventBus 事件，把"定时"扩成"听事件"。幂等：每事件类型总线上
            # 只保留最新引擎实例的 handler，避免每 30 分钟 tick 新建引擎导致 handler 堆积。
            engine.subscribe_events()
            context: dict[str, Any] = {}
            if self.environment_probe is not None:
                try:
                    context["diagnostics"] = self.environment_probe.get_diagnostics()
                except Exception:
                    logger.debug("获取环境诊断信息失败", exc_info=True)
            items = engine.tick(context=context)
            delivered = engine.deliver_all(items)
            return {
                "status": "completed",
                "trigger_count": len(items),
                "delivered_count": len(delivered),
            }
        except Exception as exc:
            logger.warning("主动代理触发器执行失败: %s", exc)
            return {"status": "failed", "error": str(exc)}

    def _suggestion_daily(self, payload: dict[str, Any]) -> dict[str, Any]:
        """每日建议分析：运行 SuggestionEngine + InfoRadar，交付到收件箱。"""
        if self.inbox_service is None:
            return {"status": "skipped", "reason": "inbox_service not available"}
        try:
            from services.suggestion_engine import SuggestionEngine
            from services.info_radar import InfoRadar

            engine = SuggestionEngine(
                user_profile_service=self.user_profile_service,
                note_repository=self.note_repository,
                task_repository=self.task_repository,
                evolution_orchestrator=getattr(self, "evolution_orchestrator", None),
                ai_client=self.ai_client,
                event_bus=getattr(self, "_event_bus", None),
            )
            suggestions = engine.analyze()
            radar = InfoRadar(user_profile_service=self.user_profile_service)
            findings = radar.scan()
            for f in findings[:3]:
                suggestions.append(
                    SuggestionEngine.build_suggestion_plain(
                        text=f"发现：{f.name}",
                        reason=f"来源：{f.topic}",
                        evidence=f.description[:200],
                        action_label="查看详情",
                        priority=50,
                    )
                )
            ranked = SuggestionEngine.rank_and_deduplicate(suggestions)
            delivered = 0
            for s in ranked[:6]:
                self.inbox_service.deliver_suggestion(
                    text=s.text,
                    action=s.action_key,
                    reason=s.reason,
                    surface="inbox_card",
                )
                delivered += 1
            logger.info("每日建议分析完成: %d 条建议 %d 已投递", len(ranked), delivered)
            return {"status": "completed", "suggestions": len(ranked), "delivered": delivered}
        except Exception as exc:
            logger.warning("每日建议分析失败: %s", exc)
            return {"status": "failed", "error": str(exc)}

    def _execute_cron_action(self, action_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        from .models import PlannedStep

        step = PlannedStep(
            step_id=f"cron_{uuid.uuid4().hex[:8]}", action_id=str(action_id or "").strip(), payload=dict(payload or {}), risk_level="low"
        )
        return self.execute(step)

    def _workflow_execute(self, payload: dict[str, Any]) -> dict[str, Any]:
        wf_id = payload.get("workflow_id", "")
        if not wf_id:
            return {"status": "error", "error": "missing workflow_id"}
        try:
            # P1.6: 改用 WorkflowRepository 统一访问层，避免直接读 data_store
            from workflow.execution_engine import WorkflowExecutionEngine
            from workflow.repository import WorkflowRepository

            repo = WorkflowRepository(self.data_store)
            wf = repo.get_workflow(wf_id)
            if wf is None:
                return {"status": "error", "error": f"workflow not found: {wf_id}"}
            engine = WorkflowExecutionEngine(capability_router=self)
            return engine.run_workflow(wf)
        except Exception as exc:
            logger.exception("workflow execute failed")
            return {"status": "error", "error": str(exc)}

    def _cron_list(self, payload: dict[str, Any]) -> dict[str, Any]:
        svc = self._get_cron_service()
        if svc is None:
            return {"success": False, "reason": "cron_service_unavailable"}
        jobs = svc.list_jobs(include_disabled=True)
        return {"success": True, "jobs": jobs, "count": len(jobs)}

    def _cron_add(self, payload: dict[str, Any]) -> dict[str, Any]:
        svc = self._get_cron_service()
        if svc is None:
            return {"success": False, "reason": "cron_service_unavailable"}
        name = str(payload.get("name") or "").strip()
        action_id = str(payload.get("action_id") or "").strip()
        if not name or not action_id:
            return {"success": False, "reason": "missing_name_or_action_id"}
        job = svc.add_job(
            name=name,
            action_id=action_id,
            payload=payload.get("payload") or {},
            schedule=str(payload.get("schedule") or ""),
            run_at=str(payload.get("run_at") or ""),
            repeat=str(payload.get("repeat") or "none"),
        )
        return {"success": True, "job": job}

    def _cron_remove(self, payload: dict[str, Any]) -> dict[str, Any]:
        svc = self._get_cron_service()
        if svc is None:
            return {"success": False, "reason": "cron_service_unavailable"}
        job_id = str(payload.get("job_id") or "").strip()
        if not job_id:
            return {"success": False, "reason": "missing_job_id"}
        removed = svc.remove_job(job_id)
        return {"success": removed, "job_id": job_id}

    def _task_execute(self, payload: dict[str, Any]) -> dict[str, Any]:
        """CronService 触发的任务执行入口。

        由 TaskManager.schedule_task_reminder 注册的 cron job 调用，
        加载对应 AgentTask 并执行其步骤。
        """
        task_id = str(payload.get("task_id") or "").strip()
        if not task_id:
            return {"success": False, "reason": "missing_task_id"}

        task_manager = getattr(self, "_injected_task_manager", None)
        if task_manager is None:
            return {"success": False, "reason": "task_manager_unavailable"}

        lifecycle = getattr(task_manager, "_lifecycle", None)
        if lifecycle is None:
            return {"success": False, "reason": "task_lifecycle_unavailable"}

        task = lifecycle.load_agent_task(task_id)
        if task is None:
            return {"success": False, "reason": "task_not_found", "task_id": task_id}

        reminder_text = str(payload.get("reminder_text") or "").strip()
        if reminder_text and self.inbox_service is not None:
            try:
                self.inbox_service.deliver_suggestion(
                    text=reminder_text,
                    action="task.execute",
                    reason=f"定时任务触发: {task.goal}",
                    surface="inbox_card",
                )
            except Exception as exc:
                logger.debug("task reminder delivery skipped: %s", exc)

        # 若任务尚未完成，尝试执行（仅支持已有 steps 的任务）
        if task.status in ("completed", "cancelled", "failed"):
            return {"success": True, "status": "skipped", "task_id": task_id, "reason": f"task_status_{task.status}"}

        if not task.steps:
            return {"success": True, "status": "reminded", "task_id": task_id}

        try:
            from .models import PlannedStep

            planned_steps = [
                PlannedStep(
                    step_id=str(s.get("step_id", f"step_{idx}")),
                    action_id=str(s.get("action_id", "")),
                    payload=dict(s.get("payload") or {}),
                    risk_level=str(s.get("risk_level", "medium")),
                    timeout_seconds=int(s.get("timeout_seconds") or 60),
                    retries=int(s.get("retries") or 0),
                )
                for idx, s in enumerate(task.steps)
            ]
            # V2.1 批次2（11.1 矩阵第 3 行）：cron 路径进程内调用不再默认放行，
            # 必须经 security.authorize_step（审批闸门独立于传输层）。
            # 之前 `lambda s: {"allowed": True}` 使 cron 触发的任务步骤天然绕过审批。
            _security = getattr(task_manager, "_security", None)

            def _cron_auth(step):
                if _security is None:
                    return {"allowed": False, "blocked": True, "reason": "cron_security_unavailable"}
                return _security.authorize_step(step, source="assistant.cron")

            result = task_manager.execute_with_plan(
                task,
                task_manager.generate_plan(task.goal, planned_steps),
                authorize_callback=_cron_auth,
                user_profile=task.profile_snapshot,
            )
            return {"success": bool(result.get("success")), "task_id": task_id, "status": task.status}
        except Exception as exc:
            logger.warning("_task_execute failed: %s", exc, exc_info=True)
            return {"success": False, "reason": str(exc), "task_id": task_id}
