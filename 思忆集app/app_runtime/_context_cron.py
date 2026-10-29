"""CronContextMixin — cron job lifecycle and initialization.

Extracted from AppContext to reduce file size.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Protocol, cast

from core import cron_expr

if TYPE_CHECKING:
    from app_runtime.app_context import AppContext
    from evolution.orchestrator import EvolutionOrchestrator
    from repositories._protocols import SettingsRepository
    from services.cron_service import CronService

logger = logging.getLogger("siyi.app_context.cron")


class CronHost(Protocol):
    """CronContextMixin 依赖的宿主契约 — AppContext 由 builder 注入以下属性。"""

    cron_service: Any  # CronService
    evolution_orchestrator: Any  # EvolutionOrchestrator
    settings_repository: Any  # SettingsRepository
    tech_watch_collector: Any
    tech_watch_judge: Any


class CronContextMixin:
    """Cron job lifecycle and initialization — extracted from AppContext."""

    # 宿主契约声明：由 AppContextBuilder 在运行时注入（见 _context_builder.py）
    if TYPE_CHECKING:
        cron_service: CronService
        evolution_orchestrator: EvolutionOrchestrator
        settings_repository: SettingsRepository
        tech_watch_collector: Any
        tech_watch_judge: Any
        local_data_store: Any

    def start_cron_service(self):
        if hasattr(self, "cron_service") and self.cron_service is not None:
            return self.cron_service.start()
        return {"running": False, "error": "cron_service not initialized"}

    def stop_cron_service(self):
        if hasattr(self, "cron_service") and self.cron_service is not None:
            self.cron_service.stop()

    def get_cron_service_status(self):
        service = self.cron_service
        if service is None:
            return {"running": False, "engine": "unavailable"}
        return service.get_status_snapshot()

    def _execute_cron_action(self, action_id: str, payload: dict) -> dict:
        # P2-7：自维护巡检在 AppContext 层直处理（需要 task_queue/autonomy
        # 等全域依赖，capability_router 无法提供）；只生成提案不执行。
        if action_id == "system.self_maintenance":
            try:
                from services.self_maintenance import get_self_maintenance_service

                service = get_self_maintenance_service(self.local_data_store)
                proposals = service.patrol(self)
                return {"success": True, "new_proposals": len(proposals)}
            except Exception as exc:  # noqa: BLE001
                logger.warning("self-maintenance patrol failed: %s", exc)
                return {"success": False, "error": str(exc)}
        # Wave-1 内核迁移（§5.1）：已迁移 action 走 services.cron_actions 注册表
        from services.cron_actions import execute_cron_action

        # self 运行时即 AppContext（AppContext 继承本 mixin），显式提升类型
        result = execute_cron_action(cast("AppContext", self), action_id, payload)
        if result is not None:
            return result
        # Wave-1 迁移完成：所有 cron action 均已迁入 services.cron_actions 注册表，
        # 不再走旧 assistant router（M5 已清零过渡期白名单）。未知 action 显式报错。
        # D-2 修复（2026-09-16）：必须带 error 键——task_queue 死信与通知只读 error，
        # 此前只有 reason，致"每日早报/每日复盘"12 条死信 last_error 全空、无法排障。
        return {
            "success": False,
            "error": f"unknown cron action: {action_id}",
            "reason": "unknown_action",
            "action_id": action_id,
        }

    # ── Cron 作业初始化 ──

    def _init_cron_jobs(self):
        if not hasattr(self, "cron_service") or self.cron_service is None:
            return
        try:
            self.cron_service.ensure_default_jobs()
            self._ensure_evolution_scan_job()
            self._ensure_tech_watch_jobs()
            self._ensure_proactive_job()
            self._ensure_suggestion_job()
            self._ensure_info_collection_job()
            # S3: 每周日凌晨 3:00 自动备份
            self._ensure_auto_backup_job()
            # P0-4: 每日记忆健康巡检（备份后执行，异常走连续失败告警）
            self._ensure_memory_health_job()
            # P2-7: 每日自维护巡检（只生成提案，执行需用户审批）
            self._ensure_self_maintenance_job()
            # AI 平台账户聊天同步：每 3 天抓取并写入知识体系（需先配置账户 token）
            self._ensure_ai_chat_job()
            # 知识归纳：把零散卡片聚类成主题 MOC 并回写图谱主题（每日 08:30 整理）
            self._ensure_knowledge_synthesize_job()
            # 阶段二：每日把当日资讯+对话写入当日 MD 并生成当日索引（每日 09:00）
            self._ensure_knowledge_daily_job()
            # V3-08: 每周一 08:00 错误 digest 周报（A-2 类错误周期汇总）
            self._ensure_error_digest_job()
        except Exception as exc:
            logger.warning("CronService 作业初始化失败: %s", exc)

    def _ensure_memory_health_job(self):
        """P0-4: 注册每日 04:00 记忆健康巡检任务。"""
        try:
            existing = [j for j in self.cron_service._jobs if j.action_id == "system.memory_health"]
            if not existing:
                self.cron_service.add_job(
                    name="每日记忆健康巡检",
                    action_id="system.memory_health",
                    payload={},
                    schedule="0 4 * * *",  # 每日 04:00（周日在 03:00 自动备份之后）
                    repeat="cron",
                )
                logger.info("已注册每日记忆健康巡检任务 (04:00)")
        except Exception:
            logger.warning("注册记忆健康巡检任务失败", exc_info=True)

    def _ensure_self_maintenance_job(self):
        """P2-7: 注册每日 05:00 自维护巡检任务（只生成提案）。"""
        try:
            existing = [j for j in self.cron_service._jobs if j.action_id == "system.self_maintenance"]
            if not existing:
                self.cron_service.add_job(
                    name="每日自维护巡检",
                    action_id="system.self_maintenance",
                    payload={},
                    schedule="0 5 * * *",  # 每日 05:00（记忆巡检之后）
                    repeat="cron",
                )
                logger.info("已注册每日自维护巡检任务 (05:00)")
        except Exception:
            logger.warning("注册自维护巡检任务失败", exc_info=True)

    def _ensure_auto_backup_job(self):
        """S3: 注册每周日凌晨 3:00 的自动备份任务。"""
        try:
            existing = [j for j in self.cron_service._jobs if j.action_id == "system.auto_backup"]
            if not existing:
                self.cron_service.add_job(
                    name="每周自动备份",
                    action_id="system.auto_backup",
                    payload={},
                    schedule="0 3 * * sun",  # 周日 03:00（批3：星期写名字，避免数字歧义）
                    repeat="cron",
                )
                logger.info("已注册每周自动备份任务 (周日 03:00)")
        except Exception:
            logger.warning("注册自动备份任务失败", exc_info=True)

    def _ensure_evolution_scan_job(self):
        if self.evolution_orchestrator is None:
            return
        try:
            existing = [j for j in self.cron_service._jobs if j.action_id == "evolution.daily_scan"]
            if not existing:
                self.cron_service.add_job(
                    name="技术动态扫描",
                    action_id="evolution.daily_scan",
                    payload={},
                    schedule="0 7 * * *",
                    repeat="cron",
                )
                logger.info("已注册每日技术动态扫描任务 (07:00)")
        except Exception:
            logger.warning("注册技术动态扫描任务失败", exc_info=True)

    def _ensure_tech_watch_jobs(self):
        if self.tech_watch_collector is None and self.tech_watch_judge is None:
            return
        try:
            existing_collect = [j for j in self.cron_service._jobs if j.action_id == "tech_watch.collect"]
            if not existing_collect:
                self.cron_service.add_job(
                    name="技术瞭望采集",
                    action_id="tech_watch.collect",
                    payload={},
                    schedule="30 7 * * *",
                    repeat="cron",
                )
                logger.info("已注册每日技术瞭望采集任务 (07:30)")
            judge_day = self._get_tech_watch_judge_day()
            # 批3 · P2-1：星期一律以**名字**写进 schedule。旧实现经 `_WEEKDAY_MAP`
            # 把 "sun" 转成 6（Python 语义），而 6 在标准 cron 里是**周六**——
            # 数字一旦跨出转换点就带上两套读法，是本次缺陷的载体。
            day_name = cron_expr.coerce_weekday_name(judge_day)
            existing_judge = [j for j in self.cron_service._jobs if j.action_id == "tech_watch.judge"]
            if not existing_judge:
                self.cron_service.add_job(
                    name="技术瞭望评估",
                    action_id="tech_watch.judge",
                    payload={},
                    schedule=f"0 9 * * {day_name}",
                    repeat="cron",
                )
                logger.info("已注册每周技术瞭望评估任务 (%s 09:00)", day_name)
        except Exception:
            logger.warning("注册技术瞭望定时任务失败", exc_info=True)

    def _ensure_proactive_job(self):
        try:
            existing = [j for j in self.cron_service._jobs if j.action_id == "proactive.tick"]
            if not existing:
                self.cron_service.add_job(
                    name="主动代理触发器",
                    action_id="proactive.tick",
                    payload={},
                    schedule="*/30 7-23 * * *",
                    repeat="cron",
                )
                logger.info("已注册主动代理触发器 (每 30 分钟 07:00-23:00)")
        except Exception:
            logger.warning("注册主动代理触发器失败", exc_info=True)

    def _ensure_suggestion_job(self):
        try:
            existing = [j for j in self.cron_service._jobs if j.action_id == "suggestion.daily"]
            if not existing:
                self.cron_service.add_job(
                    name="每日建议分析",
                    action_id="suggestion.daily",
                    payload={},
                    schedule="0 8 * * *",
                    repeat="cron",
                )
                logger.info("已注册每日建议分析任务 (08:00)")
        except Exception:
            logger.warning("注册每日建议分析任务失败", exc_info=True)

    def _ensure_info_collection_job(self):
        try:
            existing = [j for j in self.cron_service._jobs if j.action_id == "info.collect"]
            if not existing:
                self.cron_service.add_job(
                    name="每日资讯自动采集",
                    action_id="info.collect",
                    payload={"generate_brief": True},
                    schedule="0 8 * * *",
                    repeat="cron",
                )
                logger.info("已注册每日资讯自动采集任务 (08:00)")
        except Exception:
            logger.warning("注册每日资讯自动采集任务失败", exc_info=True)

    def _ensure_ai_chat_job(self):
        """AI 平台账户聊天同步：每 3 天抓取一次并整理入知识体系。

        动作 ai_chat.collect（见 services/cron_actions）。未配置账户 token 时
        任务正常跳过（success=True），配置后即自动抓取最近 3 天的新对话。
        """
        try:
            existing = [j for j in self.cron_service._jobs if j.action_id == "ai_chat.collect"]
            if not existing:
                self.cron_service.add_job(
                    name="AI 聊天每3天同步",
                    action_id="ai_chat.collect",
                    payload={"max_sessions": 30, "since_hours": 72},
                    run_at="08:00",
                    repeat="interval",
                    run_every_days=3,
                )
                logger.info("已注册每 3 天 AI 聊天同步任务 (08:00)")
        except Exception as exc:
            logger.warning("注册 AI 聊天同步任务失败: %s", exc)

    def _ensure_knowledge_synthesize_job(self):
        """知识归纳任务：每日把知识卡片聚类成主题 MOC 并回写图谱主题。

        动作 knowledge.synthesize（见 services/cron_actions）。卡片由
        ai_chat.collect / 手动触发生成，本任务负责把它们整理成可浏览的知识地图。
        """
        try:
            existing = [j for j in self.cron_service._jobs if j.action_id == "knowledge.synthesize"]
            if not existing:
                self.cron_service.add_job(
                    name="知识归纳整理",
                    action_id="knowledge.synthesize",
                    payload={"max_topics": 12},
                    schedule="30 8 * * *",
                    repeat="cron",
                )
                logger.info("已注册每日知识归纳整理任务 (08:30)")
        except Exception as exc:
            logger.warning("注册知识归纳整理任务失败: %s", exc)

    def _ensure_knowledge_daily_job(self):
        """阶段二：每日知识 MD 导出任务（09:00 把当日资讯+对话写入当日 MD + 当日索引）。

        动作 knowledge.md_daily（见 services/cron_actions → md_exporter.export_daily）。
        每天运行一次，按天增量追加时间轴文件，不覆盖用户改写的历史 MD。
        """
        try:
            existing = [j for j in self.cron_service._jobs if j.action_id == "knowledge.md_daily"]
            if not existing:
                self.cron_service.add_job(
                    name="每日知识MD导出",
                    action_id="knowledge.md_daily",
                    payload={"days": 1},
                    run_at="09:00",
                    repeat="interval",
                    run_every_days=1,
                )
                logger.info("已注册每日知识MD导出任务 (09:00)")
        except Exception as exc:
            logger.warning("注册每日知识MD导出任务失败: %s", exc)

    def _get_tech_watch_judge_day(self) -> str:
        if self.settings_repository is None:
            return "sun"
        try:
            return str(self.settings_repository.get_setting("tech_watch_judge_day", "sun"))
        except Exception:
            logger.warning("_get_tech_watch_judge_day failed, using default", exc_info=True)
            return "sun"

    def _ensure_error_digest_job(self):
        """V3-08: 注册每周一 08:00 错误 digest 周报任务。"""
        try:
            existing = [j for j in self.cron_service._jobs if j.action_id == "system.error_digest"]
            if not existing:
                self.cron_service.add_job(
                    name="每周错误摘要",
                    action_id="system.error_digest",
                    payload={},
                    schedule="0 8 * * mon",  # 每周一 08:00（批3：星期写名字，避免数字歧义）
                    repeat="cron",
                )
                logger.info("已注册每周错误摘要任务 (周一 08:00)")
        except Exception:
            logger.warning("注册错误摘要任务失败", exc_info=True)
