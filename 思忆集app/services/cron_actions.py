"""services/cron_actions.py — 稳定区 cron action 注册表（A1 D8 / 契约 §1.1-3）

调度层与执行内核的解耦接缝：cron action 执行入口收敛于此，
不再经 assistant/capability_router。新增 action = register_cron_action(...)。
"""

from __future__ import annotations
import logging
import os
import re
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import TYPE_CHECKING, Any, Callable

# 直接取定义源（core/settings_keys），不经 settings_schema 再导出（mypy 显式导出契约）
from core.settings_keys import KEY_PROACTIVE_ENABLED

if TYPE_CHECKING:
    from app_runtime.app_context import AppContext

logger = logging.getLogger("siyi.cron_actions")


@dataclass(frozen=True)
class CronActionSpec:
    action_id: str
    handler: Callable[["AppContext", dict], dict]
    description: str = ""


_REGISTRY: dict[str, CronActionSpec] = {}


def register_cron_action(spec: CronActionSpec) -> None:
    _REGISTRY[spec.action_id] = spec
    logger.info("cron action registered: %s", spec.action_id)


def list_cron_actions() -> list[dict]:
    return [{"action_id": s.action_id, "description": s.description} for s in _REGISTRY.values()]


def _run_coroutine_sync(coro: Any) -> Any:
    """在同步上下文里执行协程，兼容"当前线程已有事件循环"与"无循环"两种调用点。

    cron action 的 handler 是同步签名，但消费的服务多为 async。直接使用
    ``asyncio.run`` 只在"当前线程无运行中的事件循环"时成立——典型路径（task_queue
    的 to_thread 工作线程）满足，但一旦 handler 经 kernel.submit 等路径在事件循环
    内被触发，asyncio.run 会抛 RuntimeError（M16，2026-08-29）。
    事件循环内时改由独立线程驱动，保证两条路径都可用。
    """
    import asyncio
    from concurrent.futures import ThreadPoolExecutor

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)

    with ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(asyncio.run, coro).result()


def execute_cron_action(context: "AppContext", action_id: str, payload: dict) -> dict | None:
    """返回 None = 未注册（调用方决定是否落旧 router，过渡期白名单）。"""
    spec = _REGISTRY.get(action_id)
    if spec is None:
        return None
    try:
        return spec.handler(context, payload or {})
    except Exception as exc:  # noqa: BLE001
        logger.exception("cron action %s failed", action_id)
        return {"success": False, "error": str(exc)}


def _register_proactive_tick() -> None:
    def _handler(context, payload):
        inbox_svc = getattr(context, "assistant_inbox_service", None)
        if context.settings_repository is None:
            return {"status": "skipped", "reason": "settings_repository not configured"}
        enabled = bool(context.settings_repository.get_setting(KEY_PROACTIVE_ENABLED, True))
        if not enabled:
            return {"status": "skipped", "reason": "proactive_disabled_by_user"}
        try:
            try:
                from services.habit_learner import HabitLearner

                hl = HabitLearner(context.local_data_store, context.user_profile_service)
                learn_result = hl.scan_and_learn()
                discoveries = learn_result.get("discoveries", {})
                if discoveries:
                    logger.info("画像习惯学习发现: %s", discoveries)
                    if inbox_svc is not None:
                        parts = []
                        if discoveries.get("hour_patterns"):
                            parts.append(f"活跃时段：{'、'.join(discoveries['hour_patterns'][:3])}")
                        if discoveries.get("top_tools"):
                            parts.append(f"常用功能：{'、'.join(discoveries['top_tools'][:3])}")
                        if parts:
                            inbox_svc.deliver_suggestion(
                                text="小问开始了解你的使用习惯了",
                                action="habit.status",
                                reason="、".join(parts),
                            )
            except Exception as exc:
                # M17（2026-08-29）：习惯学习是主动建议的养分来源，失败必须留痕。
                # 原为 logger.debug，长期失效无人知晓（与主动管家 H1 同被发现）。
                logger.warning("画像习惯学习失败（主动建议将缺少习惯养分）: %s", exc, exc_info=True)
            from services.proactive_triggers import ProactiveTriggerEngine

            engine = ProactiveTriggerEngine(
                inbox_service=inbox_svc,
                desktop_doctor=getattr(context, "desktop_doctor_service", None),
                workbench_service=getattr(context, "workbench_service", None),
                # 注意：AppContext 注入名是 personal_*（app_runtime/_context_builder.py）。
                # 此前此处误用其他构造点的关键字参数名（无 personal_ 前缀）→ AttributeError
                # → 每 30 分钟 tick 全失败并进 dead_letter，主动建议从未投递（2026-08-29 修复）。
                personal_task_repository=context.personal_task_repository,
                personal_note_repository=context.personal_note_repository,
                weather_service=getattr(context, "weather_service", None),
            )
            trigger_setting = context.settings_repository.get_setting("proactive_enabled_triggers", None)
            if isinstance(trigger_setting, list):
                engine.set_enabled_triggers(set(trigger_setting))
            # 第二档 #5：订阅 EventBus 事件，把"定时"扩成"听事件"。幂等：每事件类型总线上
            # 只保留最新引擎实例的 handler，避免每 30 分钟 tick 新建引擎导致 handler 堆积。
            engine.subscribe_events()
            # L9（2026-08-29）：environment_probe 只存在于 assistant/capability_router，
            # AppContext 从不注入 → 此处恒为 None，trigger_context["diagnostics"] 永远
            # 不填充（死接线）。保留 getattr 兜底以免部署差异崩溃，但显式留痕待决策。
            _env = getattr(context, "environment_probe", None)
            trigger_context: dict[str, Any] = {}
            if _env is None:
                logger.debug("主动触发缺少环境诊断：AppContext 未注入 environment_probe（L9 死接线）")
            else:
                try:
                    trigger_context["diagnostics"] = _env.get_diagnostics()
                except Exception:
                    logger.warning("获取环境诊断信息失败（主动触发将缺少环境上下文）", exc_info=True)
            items = engine.tick(context=trigger_context)
            delivered = engine.deliver_all(items)
            return {
                "status": "completed",
                "trigger_count": len(items),
                "delivered_count": len(delivered),
            }
        except Exception as exc:
            logger.warning("主动代理触发器执行失败: %s", exc)
            return {"status": "failed", "error": str(exc)}

    register_cron_action(CronActionSpec("proactive.tick", _handler, "主动建议触发器 tick（原 assistant._router_schedule）"))


_register_proactive_tick()


def _register_evolution_daily_scan() -> None:
    def _handler(context, payload):
        # 修 AttributeError（2026-09-16 批3，死信实证）：EvolutionOrchestrator
        # 没有 daily_scan()，真方法名是 scan_and_report()（evolution/orchestrator.py:94）。
        # 旧 assistant/capability_router.py:759 _evolution_daily_scan 即调此方法；
        # 迁移时按 cron 任务名臆造了方法名，导致每日扫描 100% 进死信。
        orch = getattr(context, "evolution_orchestrator", None)
        if orch is None:
            return {"status": "skipped", "reason": "evolution_orchestrator not configured"}
        try:
            briefing = orch.scan_and_report()
            return {
                "status": "completed",
                "findings": briefing.to_dict() if hasattr(briefing, "to_dict") else str(briefing),
            }
        except Exception as exc:
            logger.error("进化每日扫描失败: %s", exc)
            return {"status": "failed", "error": str(exc)}

    register_cron_action(CronActionSpec("evolution.daily_scan", _handler, "进化系统每日扫描（原 assistant.capability_router）"))


_register_evolution_daily_scan()


def _register_tech_watch_judge() -> None:
    def _handler(context, payload):
        # 配套修复（2026-09-16 批3）：context.tech_watch_judge 曾在
        # _tooling_provider 被错绑为路由用 JudgeScheduler（无 .judge()），
        # 本任务 100% AttributeError 进死信。现绑定已改为
        # services.tech_watch.TechWatchJudge，.judge() 契约成立。
        judge = getattr(context, "tech_watch_judge", None)
        if judge is None or not hasattr(judge, "judge"):
            return {"status": "skipped", "reason": "tech_watch_judge not configured"}
        try:
            # M-cron 收敛：优先经 agent_gateway（成熟内核）评估；
            # 修复既有 bug：此前把 payload 字符串误作 deepseek_client 传入
            gw = getattr(context, "agent_gateway", None)
            deepseek = getattr(context, "ai_client", None)
            result = judge.judge(
                deepseek_client=deepseek,
                agent_gateway=gw,
            )
            return {"status": "completed", "result": result}
        except Exception as exc:
            logger.error("技术雷达评估失败: %s", exc)
            return {"status": "failed", "error": str(exc)}

    register_cron_action(CronActionSpec("tech_watch.judge", _handler, "技术雷达评估（原 assistant._router_content）"))


_register_tech_watch_judge()


def _register_suggestion_daily() -> None:
    def _handler(context, payload):
        from services.suggestion_engine import SuggestionEngine

        # 修复既有 bug：原 handler 使用不存在的 context 属性（inbox_service /
        # note_repository / task_repository），实际为 personal_*_repository
        engine = SuggestionEngine(
            user_profile_service=getattr(context, "user_profile_service", None),
            note_repository=getattr(context, "personal_note_repository", None),
            task_repository=getattr(context, "personal_task_repository", None),
            evolution_orchestrator=getattr(context, "evolution_orchestrator", None),
            ai_client=getattr(context, "ai_client", None),
            event_bus=getattr(context, "event_bus", None),
            agent_gateway=getattr(context, "agent_gateway", None),
        )
        try:
            # 修复既有 bug：原调用不存在的 generate_daily_suggestions；实际入口是 analyze()
            suggestions = engine.analyze()
            serialized = [s.to_dict() if hasattr(s, "to_dict") else s for s in suggestions]
            return {"status": "completed", "count": len(serialized), "suggestions": serialized}
        except Exception as exc:
            logger.error("每日建议生成失败: %s", exc)
            return {"status": "failed", "error": str(exc)}

    register_cron_action(CronActionSpec("suggestion.daily", _handler, "每日主动建议生成（原 assistant._router_schedule）"))


_register_suggestion_daily()


def _register_info_collect() -> None:
    def _handler(context, payload):
        from services.info_collector_service import InfoCollectorService

        svc = InfoCollectorService(
            ai_client=context.ai_client,
            event_bus=getattr(context, "event_bus", None),
            settings_repository=getattr(context, "settings_repository", None),
            source_registry=getattr(context, "info_source_registry", None),
            agent_gateway=getattr(context, "agent_gateway", None),
        )
        try:
            # 修复既有 bug：原调用不存在的 collect_and_store；实际入口是 async collect
            topic = payload.get("topic")
            topics = [topic] if isinstance(topic, str) and topic.strip() else None
            result = _run_coroutine_sync(svc.collect(topics=topics))
            result = result if isinstance(result, dict) else {}
            items = result.get("items") or []
            brief = result.get("brief") or ""
            memory_id = _persist_info_memory(context, items, brief)
            return {"status": "completed", "result": result, "memory_id": memory_id}
        except Exception as exc:
            logger.error("资讯采集失败: %s", exc)
            return {"status": "failed", "error": str(exc)}

    register_cron_action(CronActionSpec("info.collect", _handler, "资讯采集（原 assistant._router_content）"))


_register_info_collect()


def _register_job_hunter_scrape() -> None:
    """P2 任务化：job_hunter.scrape.run RPC 改走任务队列后由 worker 执行。

    RPC 侧只入队并立即返回 job_id（见 backend/handlers/_job_hunter.py），
    本 action 是 worker 侧的真正执行体：调 JobHunterService.search（async）
    并发出 job_hunter.scrape.completed 事件（与旧同步 handler 行为一致）。
    """

    def _handler(context, payload):
        svc = getattr(context, "job_hunter_service", None)
        if svc is None:
            return {
                "success": False,
                "status": "failed",
                "error": "job_hunter_service not initialized",
            }
        try:
            result = _run_coroutine_sync(
                svc.search(
                    payload.get("platforms"),
                    payload.get("keywords"),
                    str(payload.get("city") or ""),
                    page=int(payload.get("page") or 1),
                )
            )
            try:
                from core.events import event_bus, AppEvent

                event_bus.emit(AppEvent("job_hunter.scrape.completed", payload=result))
            except Exception:
                logger.debug("job_hunter.scrape.completed 事件发送失败", exc_info=True)
            return {
                "success": bool(result.get("success")),
                "status": "completed" if result.get("success") else "failed",
                # D-PG-2（2026-09-17）：失败时透传原因（如 all_platforms_failed），
                # 让 worker 的 last_error 与 scrape.status 轮询都看得到真实失败。
                "error": "" if result.get("success") else str(result.get("error") or ""),
                "result": result,
            }
        except Exception as exc:
            logger.error("job_hunter.scrape 执行失败: %s", exc)
            return {"success": False, "status": "failed", "error": str(exc)}

    register_cron_action(CronActionSpec("job_hunter.scrape", _handler, "求职猎手职位抓取（scrape.run 任务队列异步执行）"))


_register_job_hunter_scrape()


def _persist_info_memory(context, items: list, brief: str) -> str | None:
    """采集后由内核（大脑）把资讯整理成要点，写入结构化记忆（手）。

    - 大脑：kernel_generate 优先成熟内核（dsh），失败回退 ai_client
    - 手  ：整理结果追加到 structured_memory_entries（与 mcp siyi_memory_store 同键同形）
    - 幂等：同一天同一 topic 已写入则不重复追加
    """
    import hashlib
    import secrets

    store = getattr(context, "local_data_store", None)
    if store is None:
        logger.warning("info.collect: local_data_store not configured, skip memory")
        return None

    if not items:
        return None

    # 取 top 条目（标题 + 来源），控制 prompt 长度（避免 Windows 命令行过长）
    top = items[:8]
    digest = hashlib.md5(
        "|".join(str(it.get("url", "") or it.get("title", "")) for it in top).encode("utf-8"),
        usedforsecurity=False,
    ).hexdigest()[:12]

    # 幂等：检查今天是否已写入同批次的记忆
    today = datetime.now().strftime("%Y-%m-%d")
    entries = store.load_json("structured_memory_entries", []) or []
    for e in entries:
        if str(e.get("content", "")).startswith(f"[资讯要点 {today}") and e.get("_digest") == digest:
            logger.info("info.collect: 今日资讯要点已存在, skip (digest=%s)", digest)
            return e.get("id")

    lines = []
    for idx, it in enumerate(top, 1):
        title = (it.get("title_zh") or it.get("title", "") or "").strip()
        # 截断过长标题，控制总长度 ≤ 1200 字符（Windows cmdline 上限）
        if len(title) > 60:
            title = title[:60] + "…"
        lines.append(f"[{idx}] {title}")
    prompt = "请把下面这批今日资讯标题整理成一段简洁的中文要点总结（200字内），按重要程度排序，涵盖主要主题，不要逐条罗列：\n\n" + "\n".join(lines)
    system = "你是资讯梳理助手，输出精炼的中文要点总结，不要多余解释。"

    from services.kernel_ai import call_ai_text, kernel_generate

    text = kernel_generate(
        getattr(context, "agent_gateway", None),
        prompt,
        system=system,
        timeout_s=240,
        fallback=lambda: call_ai_text(getattr(context, "ai_client", None), prompt, system=system),
    )
    text = (text or "").strip()
    if not text:
        logger.warning("info.collect: 内核/回退均未产出要点，跳过写记忆")
        return None

    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    entry_id: str = secrets.token_hex(8)
    entry = {
        "id": entry_id,
        "supertag_type": "@知识",
        "entity_type": "info.digest",
        "relations": [],
        "source": "info.collect",
        "created_at": now,
        "updated_at": now,
        "tags": ["资讯", "要点"],
        "content": f"[资讯要点 {today}] {text}",
        "_digest": digest,
    }
    entries.append(entry)
    store.save_json("structured_memory_entries", entries)
    logger.info("info.collect: 已写入资讯要点记忆 id=%s digest=%s", entry["id"], digest)

    # 落库后同步提取实体写入知识图谱（手：图谱与结构化记忆一致反映知识体系）
    kg = getattr(context, "knowledge_graph_extractor", None)
    if kg is not None and text:
        try:
            kg.extract_from_memory(entry, use_llm=False, source_kind="info")
            logger.info("info.collect: 已同步写入知识图谱实体 (记忆 id=%s)", entry["id"])
        except Exception as exc:
            logger.warning("info.collect: 写知识图谱失败（非阻塞）: %s", exc)

    return entry_id


def _register_system_auto_backup() -> None:
    def _handler(context, payload):
        # 修 D-1 真身（2026-09-16 批3，死信 last_error 实证）：旧迁移实现调用
        # ``LocalDataStore.create_backup`` —— 该方法不存在，任务 100% 抛
        # AttributeError 进死信，"自动备份从未成功执行"由此而来（并非只有
        # 调度问题）。现按 assistant/capability_router.py:540 原实现移植：
        # 走 services.backup_service.BackupService（备份 notes/tasks/settings/
        # memories 到 data/backups/，保留最近 8 份，压缩、不含 AI 缓存）。
        import asyncio
        from pathlib import Path as _Path

        try:
            store = getattr(context, "local_data_store", None)
            if store is None:
                return {"status": "skipped", "reason": "data_store not configured"}
            data_dir = getattr(store, "base_dir", None) or getattr(store, "data_dir", None)
            data_dir = _Path(data_dir) if data_dir else _Path(__file__).resolve().parent.parent / "data"

            from services.backup_service import BackupService, BackupConfig

            config = BackupConfig(
                backup_dir=data_dir / "backups",
                max_backups=8,
                compress=True,
                include_ai_cache=False,  # AI 缓存体积大且可重建
            )
            service = BackupService(data_dir=data_dir, config=config, data_store=store)

            # create_backup 是 async；cron handler 在线程池执行，通常无运行中事件循环
            try:
                result = asyncio.run(service.create_backup())
            except RuntimeError:
                import concurrent.futures

                with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                    result = pool.submit(asyncio.run, service.create_backup()).result(timeout=300)

            if getattr(result, "success", False):
                logger.info(
                    "cron 自动备份成功: id=%s size=%s",
                    getattr(result, "backup_id", "?"),
                    getattr(result, "size_bytes", "?"),
                )
                return {
                    "status": "completed",
                    "backup_id": getattr(result, "backup_id", None),
                    "file_path": getattr(result, "file_path", None),
                    "size_bytes": getattr(result, "size_bytes", 0),
                    "duration_seconds": getattr(result, "duration_seconds", 0),
                }
            return {"status": "failed", "error": str(getattr(result, "error", "backup failed"))}
        except Exception as exc:
            logger.exception("cron 自动备份异常: %s", exc)
            return {"status": "failed", "error": str(exc)}

    register_cron_action(CronActionSpec("system.auto_backup", _handler, "系统自动备份（原 assistant.capability_router）"))


_register_system_auto_backup()


def _register_system_memory_health() -> None:
    def _handler(context, payload):
        try:
            store = getattr(context, "local_data_store", None)
            if store is None:
                return {"status": "skipped", "reason": "data_store not configured"}
            health = store.health_check()
            mem_svc = getattr(context, "memory_index_service", None)
            memory_result = None
            if mem_svc is not None:
                try:
                    memory_result = mem_svc.health_check()
                except Exception as exc:
                    logger.debug("记忆索引健康检查失败: %s", exc)
                    memory_result = {"status": "failed", "error": str(exc)}
            else:
                memory_result = {"status": "skipped", "reason": "memory_index_service unavailable"}
            return {"status": "completed", "db_health": health, "memory_health": memory_result}
        except Exception as exc:
            logger.error("记忆健康检测失败: %s", exc)
            return {"status": "failed", "error": str(exc)}

    register_cron_action(CronActionSpec("system.memory_health", _handler, "记忆与数据库健康检测（原 assistant.capability_router）"))


_register_system_memory_health()


def _register_kernel_submit() -> None:
    """M-cron: kernel.submit(action_id, payload) —— 内核执行入口（§5.3）。

    把任意已注册 action 经统一内核入口执行，并写 trace 步（kernel:*）。
    区别于旧 router._execute_cron_action：本入口走 services.cron_actions 注册表，
    action_id 命名空间以 kernel: 显式标注内核执行路径。
    """

    def _handler(context, payload):
        try:
            target = payload.get("action_id") or payload.get("target")
            sub_payload = payload.get("payload") or {}
            if not target:
                return {"success": False, "reason": "missing action_id", "status": "failed"}
            from services.cron_actions import execute_cron_action

            result = execute_cron_action(context, target, sub_payload)
            if result is None:
                return {"success": False, "reason": "unknown_action", "action_id": target, "status": "failed"}
            result = dict(result)
            result.setdefault("success", True)
            return result
        except Exception as exc:
            logger.error("内核提交执行失败: %s", exc)
            return {"success": False, "error": str(exc), "status": "failed"}

    register_cron_action(CronActionSpec("kernel.submit", _handler, "M-cron 内核执行入口：经稳定区注册表提交任意已注册 action（§5.3）"))


_register_kernel_submit()


def _register_kernel_cron_demo() -> None:
    """M-cron：cron 任务经 ``gateway.submit`` 直走内核（而非旧 subprocess）。

    演示项：验证 cron 调度层 → 稳定区注册表 → 执行内核网关 的整条新路径。
    优先复用 ``context.agent_gateway``（headless 注入的网关实例），缺失时按默认
    配置重建（与生产一致）。无真实内核（type=none / dsh 不在 PATH）时返回
    unavailable，仍证明路径已接通而不触发旧 router。
    """

    def _handler(context, payload):
        try:
            gw = getattr(context, "agent_gateway", None)
            if gw is None:
                from services.agent_gateway import build_gateway

                gw = build_gateway(None)
            prompt = payload.get("prompt") or "reply with exactly: cron-demo-ok"
            timeout_s = payload.get("timeout_s") or 30
            run = gw.submit(prompt, timeout_s=timeout_s)
            return {
                "success": run.ok,
                "status": run.status,
                "result": run.result,
                "error": run.error,
                "error_kind": (run.raw or {}).get("error_kind"),
                "kernel": getattr(gw, "name", "unknown"),
                "demo": True,
            }
        except Exception as exc:
            logger.error("kernel.cron_demo 失败: %s", exc)
            return {"success": False, "error": str(exc), "status": "failed"}

    register_cron_action(CronActionSpec("kernel.cron_demo", _handler, "M-cron 演示：cron 任务经 gateway.submit 直走内核（§M-cron）"))


_register_kernel_cron_demo()


def _register_skill_cards() -> None:
    """M4 技能卡：cron 经内核提交执行 4 张预置技能卡（计划 §8）。

    每张技能卡是一份 ``data/skills/<id>.md``（front-matter + 正文，对齐 DSH 技能形态）。
    cron 触发时读取技能卡 → 组装 prompt → 经 ``gateway.submit`` 直走 dsh 内核执行。
    仅依赖已挂的 mcp-siyi 工具，不依赖 MCP P1（OQ2 待决）。
    """
    SKILL_CARDS = ("daily-news-digest", "knowledge-digest", "inbox-triage", "memory-anchor")

    def _make_handler(skill_id: str):
        def _handler(context, payload):
            from services.skill_cards import load_skill_card, render_skill_prompt

            card = load_skill_card(skill_id)
            if card is None:
                return {"status": "skipped", "reason": "skill_card_not_found", "skill_id": skill_id}
            prompt = render_skill_prompt(card, payload)
            gw = getattr(context, "agent_gateway", None)
            if gw is None:
                from services.agent_gateway import build_gateway

                gw = build_gateway(None)
            timeout_s = payload.get("timeout_s") or 300
            profile = card.resolve_profile()
            try:
                import inspect

                sig = inspect.signature(gw.submit)
                if "profile" in sig.parameters:
                    run = gw.submit(prompt, timeout_s=timeout_s, profile=profile)
                else:
                    run = gw.submit(prompt, timeout_s=timeout_s)
            except Exception as exc:
                logger.error("技能卡 %s 内核执行失败: %s", skill_id, exc)
                return {"success": False, "error": str(exc), "status": "failed", "skill_id": skill_id}
            return {
                "success": run.ok,
                "status": run.status,
                "result": run.result,
                "error": run.error,
                "error_kind": (run.raw or {}).get("error_kind"),
                "skill_id": skill_id,
                "kernel": card.kernel,
                "profile": profile,
            }

        return _handler

    for sid in SKILL_CARDS:
        register_cron_action(CronActionSpec(sid, _make_handler(sid), f"M4 技能卡：{sid}（cron→kernel.submit 直走 dsh）"))


_register_skill_cards()


def _register_ai_chat_collect() -> None:
    """AI 平台账户聊天定期同步（先跑通 DeepSeek）。

    直接调用 chat.deepseek.com 内部 API 抓取账户会话并导入思忆集，
    落库(raw→记忆→知识图谱)。默认仅同步最近 3 天的新对话，控制耗时与噪音。
    """

    def _handler(context, payload):
        from services.ai_chat_sync import get_account_token, refresh_deepseek

        if not get_account_token(context, "deepseek"):
            return {"status": "skipped", "reason": "token_not_configured", "success": True}
        max_sessions = int(payload.get("max_sessions") or 30)
        since_hours = float(payload.get("since_hours") or 0)
        return refresh_deepseek(context, max_sessions=max_sessions, since_hours=since_hours)

    register_cron_action(CronActionSpec("ai_chat.collect", _handler, "AI 平台账户聊天同步（DeepSeek）：抓取并写入知识体系"))


_register_ai_chat_collect()


def _register_knowledge_synthesize() -> None:
    """知识归纳：把已抓取对话的原子知识卡片聚类成主题 MOC 并回写图谱主题。

    由 ai_chat.collect（抓取后归纳卡片）配合使用：卡片由 ai_chat_sync 生成，
    本 action 负责定期把零散卡片归纳为「主题地图」，让知识可浏览、可导航。
    """

    def _handler(context, payload):
        try:
            from services.knowledge_synthesis import build_topic_mocs

            result = build_topic_mocs(context, max_topics=int(payload.get("max_topics") or 12))
            return {"status": "completed", "result": result}
        except Exception as exc:
            logger.error("知识归纳(MOC)失败: %s", exc)
            return {"status": "failed", "error": str(exc)}

    register_cron_action(CronActionSpec("knowledge.synthesize", _handler, "知识归纳：生成主题 MOC 并回写图谱主题"))


_register_knowledge_synthesize()


def _register_knowledge_brief() -> None:
    """知识简报：把最近 N 天新增知识卡片整理成可读 Markdown 简报。

    S3-2（2026-09-16）：既服务 cron 周期派发，也服务 RPC `knowledge.brief`
    异步化后的队列执行（RPC 秒回 job_id，真活在此）。改用 async_rpc_worker
    统一「执行 + 发 knowledge.brief.completed 事件 + 记结果供轮询」。
    """
    from backend.handlers._async_rpc import async_rpc_worker

    def _runner(context, payload):
        from backend.handlers._inbox_knowledge import run_knowledge_brief

        return _run_coroutine_sync(run_knowledge_brief(context, days=int(payload.get("days") or 7)))

    register_cron_action(
        CronActionSpec(
            "knowledge.brief",
            async_rpc_worker("knowledge.brief", runner=_runner),
            "知识简报：最近新增知识卡片整理成可读简报（RPC 异步化 + cron 派发共用）",
        )
    )


_register_knowledge_brief()


def _register_knowledge_md_daily() -> None:
    """每日知识 MD 导出：把当日资讯+对话+记忆写入当日 MD 并生成当日索引。

    阶段二：调用 ``md_exporter.export_daily``（增量），只写最近 N 天时间轴文件，
    不覆盖用户改写的历史 MD；不重写主题轴与图谱快照（由全量导出负责）。
    """

    def _handler(context, payload):
        try:
            from knowledge.md_exporter import export_daily

            days = int(payload.get("days") or 1)
            result = export_daily(context, days=days)
            return {"status": "completed", "result": result}
        except Exception as exc:
            logger.error("每日知识MD导出失败: %s", exc)
            return {"status": "failed", "error": str(exc)}

    register_cron_action(CronActionSpec("knowledge.md_daily", _handler, "每日知识MD导出：当日资讯+对话写入当日 MD 并生成当日索引"))


_register_knowledge_md_daily()
# ── V3-08 错误 digest 周报 ──────────────────────────────────────

_LOG_TS_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})\b")
_LOG_LEVEL_RE = re.compile(r"^\[(ERROR|CRITICAL)\]\s*")


def _default_log_root() -> str:
    """仓库根（后端 dev 启动器把 stdout 重定向到根目录 backend.dev.log）。"""
    here = os.path.dirname(os.path.abspath(__file__))  # .../services
    return os.path.dirname(os.path.dirname(here))  # .../思忆集app -> 根


def _line_date(line: str) -> date | None:
    m = _LOG_TS_RE.match(line)
    if not m:
        return None
    try:
        return datetime.strptime(m.group(1), "%Y-%m-%d").date()
    except ValueError:
        return None


def _traceback_exception_type(line: str) -> str | None:
    m = re.match(r"^([A-Za-z_][\w.]*(?:\([^)]*\))?)\s*:", line)
    return m.group(1) if m else None


def _level_error_sig(line: str) -> str | None:
    if "[ERROR]" not in line and "[CRITICAL]" not in line:
        return None
    body = line
    body = _LOG_TS_RE.sub("", body)
    body = _LOG_LEVEL_RE.sub("", body)
    if ":" in body:
        body = body.split(":", 1)[1]
    norm = " ".join(body.split())
    return norm[:200] or None


def _scan_log_file(counts: Counter, path: str, cutoff: date) -> None:
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            in_tb = False
            for raw in fh:
                line = raw.strip()
                if not line:
                    in_tb = False
                    continue
                d = _line_date(line)
                if d is not None and d < cutoff:
                    in_tb = False
                    continue
                if "traceback (most recent call last)" in line.lower():
                    in_tb = True
                    continue
                if in_tb:
                    sig = _traceback_exception_type(line)
                    if sig is not None:
                        counts[sig] += 1
                        in_tb = False
                    continue
                sig = _level_error_sig(line)
                if sig is not None:
                    counts[sig] += 1
    except OSError:
        logger.debug("error digest: 无法读取日志 %s", path)


def _scan_error_signatures(log_dir: str, *, within_days: int) -> Counter:
    counts: Counter = Counter()
    cutoff = datetime.now().date() - timedelta(days=within_days)
    for filename in ("backend.dev.log", "backend.dev.err.log"):
        _scan_log_file(counts, os.path.join(log_dir, filename), cutoff)
    return counts


def _register_error_digest() -> None:
    def _handler(context, payload):
        days = int(payload.get("days") or 7) if isinstance(payload, dict) else 7
        log_dir = (payload.get("log_dir") or "") if isinstance(payload, dict) else ""
        if not log_dir:
            settings_repo = getattr(context, "settings_repository", None)
            if settings_repo is not None:
                try:
                    log_dir = settings_repo.get_setting("error_digest_log_dir", "") or ""
                except Exception as exc:
                    logger.debug("error digest: 读取设置失败: %s", exc)
            if not log_dir:
                log_dir = _default_log_root()
        counts = _scan_error_signatures(log_dir, within_days=days)
        categories = len(counts)
        top = counts.most_common(20)
        top3 = [sig for sig, _ in top[:3]]
        text = "每周错误摘要: {} 类异常 Top3: {}".format(categories, "、".join(top3) if top3 else "（无）")
        delivered = False
        inbox_svc = getattr(context, "assistant_inbox_service", None)
        if inbox_svc is not None and categories:
            try:
                result = inbox_svc.deliver_suggestion(
                    text=text,
                    action="trace.list",
                    reason="近 7 天日志按异常签名聚合的错误排行",
                    metadata={"error_categories": categories, "top_errors": top},
                )
                delivered = result is not None
            except Exception as exc:
                logger.debug("error digest: 投递失败: %s", exc)
        logger.info("error digest: %s 类异常 top=%s", categories, top[:3])
        return {
            "status": "completed",
            "log_dir": log_dir,
            "error_categories": categories,
            "top_20": [{"signature": sig, "count": count} for sig, count in top],
            "digest_text": text,
            "delivered": delivered,
        }

    register_cron_action(
        CronActionSpec("system.error_digest", _handler, "错误 digest 周报：每周一解析近 7 天 ERROR/Traceback，按异常签名聚合并投递 Top20")
    )


_register_error_digest()


def _register_capability_run() -> None:
    """B1/B2 落地（2026-09-11）：capability.run —— cron 通用能力执行入口。

    让定时任务可以调度任意已注册能力（如 ``app.launch`` 启动 WorkBuddy），
    打通「每天自动执行」这类无人值守自动化的最后一环。

    权限链与 chat 内执行**同源**，不因 cron 场景放宽：
    ① action_guard.validate：白名单（默认∪追加−禁用）+ sandbox_level 档位
    ② 审批判定：无人值守场景 **fail-fast 而非阻塞等待**——
       - 能力在「始终允许」名单 / approval_policy=never → 直接执行
       - 否则（需人工确认）→ 返回 skipped 并给出处置提示，进死信可查
       这与对话场景「弹卡等人」不同：cron 没有人可等，等待只会无限挂起。

    payload：``{"action_id": "<目标能力>", "args": {...}}``
    """

    def _handler(context, payload):
        data = dict(payload or {})
        target = str(data.get("action_id") or "").strip()
        args = dict(data.get("args") or {})
        if not target:
            return {"success": False, "status": "failed", "error": "missing action_id"}
        if target == "capability.run":
            return {"success": False, "status": "failed", "error": "recursive capability.run is not allowed"}

        cap = getattr(context, "capability_router", None)
        if cap is None:
            manager = getattr(context, "autonomous_assistant_manager", None)
            cap = getattr(manager, "router", None)
        if cap is None:
            return {"success": False, "status": "failed", "error": "capability_router 不可用", "action_id": target}

        guard = getattr(cap, "action_guard", None)
        if guard is None or not hasattr(guard, "validate"):
            return {"success": False, "status": "failed", "error": "action_guard 不可用", "action_id": target}

        # ① 准入：白名单（默认 ∪ 追加 − 禁用）+ sandbox_level 档位
        try:
            check = guard.validate(target, payload=args, source="cron")
        except Exception as exc:
            logger.error("capability.run 准入校验异常: %s", exc, exc_info=True)
            return {"success": False, "status": "failed", "error": str(exc), "action_id": target}
        if not check.allowed:
            return {
                "success": False,
                "status": "failed",
                "error": f"action_rejected: {check.reason}",
                "action_id": target,
            }

        # ② 审批：无人值守 fail-fast（需要人工确认时跳过，不阻塞任务队列）
        if guard.should_require_confirmation(target):
            hint = (
                "该能力需要人工确认。无人值守场景请二选一："
                "① 在对话审批时选择「始终允许」（写入 assistant_confirm_always_allow）；"
                "② 设置 assistant_approval_policy=never（全局免询问，慎用）。"
            )
            # 反馈闭环（2026-09-11）：skipped 的 hint 原本只躺在死信记录里，
            # 用户无从知晓 → 任务静默卡死。投递到收件箱让指引可见
            # （deliver_suggestion 为 proactive.tick 同款现成机制）。
            inbox_svc = getattr(context, "assistant_inbox_service", None)
            if inbox_svc is not None and hasattr(inbox_svc, "deliver_suggestion"):
                try:
                    inbox_svc.deliver_suggestion(
                        text=(
                            f"定时任务调用的能力「{target}」尚未获得「始终允许」，本次已跳过。请在对话中让它执行一次，并在审批时选择「始终允许」。"
                        ),
                        action="settings",
                        reason=f"capability.run skipped: {target}",
                    )
                except Exception:
                    logger.warning("capability.run skipped 投递收件箱失败: %s", target, exc_info=True)
            return {
                "success": False,
                "status": "skipped",
                "reason": "confirmation_required_unattended",
                "action_id": target,
                "hint": hint,
            }

        # ③ 执行（execute_direct 为 async，经 _run_coroutine_sync 兼容两种调用点）
        try:
            result = _run_coroutine_sync(cap.execute_direct(target, args))
            return {"success": True, "status": "completed", "action_id": target, "result": result}
        except Exception as exc:
            logger.error("capability.run 执行 %s 失败: %s", target, exc, exc_info=True)
            return {"success": False, "status": "failed", "error": str(exc), "action_id": target}

    register_cron_action(
        CronActionSpec(
            "capability.run",
            _handler,
            "通用能力执行入口：经完整权限链（白名单+sandbox+审批判定）调度任意已注册能力；无人值守需审批时 fail-fast 跳过",
        )
    )


_register_capability_run()


# ── D-10 修复（2026-09-16）：cron 族孤儿任务补注册 ─────────────────
# ensure_default_jobs/_ensure_* 播种了这三个 action_id，但 Wave-1 迁移时
# 未把它们迁入本注册表（M5 清零旧 router 回退后）→ 每次触发都走
# _context_cron.py 的 unknown_action 分支，重试耗尽进 dead_letter。
# handler 逻辑逐行对照 assistant/_router_content.py / _router_notes.py /
# capability/_knowledge.py 的旧实现，仅把依赖源从 router 属性换为
# AppContext 属性（命名差异见 proactive.tick 注释中的同类教训）。


def _register_tech_watch_collect() -> None:
    """技术瞭望采集：按关注话题抓取 RSS/博客（原 assistant._router_content）。"""

    def _handler(context, payload):
        collector = getattr(context, "tech_watch_collector", None)
        if collector is None:
            return {"status": "skipped", "reason": "collector not configured", "success": True}
        topics = payload.get("topics")
        if not topics:
            topics = ["AI", "自动化", "软件工程"]
            try:
                if context.settings_repository is not None:
                    setting = context.settings_repository.get_setting("assistant_focus_topics", [])
                    if isinstance(setting, list) and setting:
                        topics = setting
            except Exception:  # noqa: BLE001
                logger.debug("获取关注话题设置失败", exc_info=True)
        local_ai = getattr(getattr(context, "ai_client", None), "local", None)
        try:
            result = collector.collect(topics, local_ai_client=local_ai)
            return {"status": "completed", "result": result}
        except Exception as exc:  # noqa: BLE001
            logger.error("技术瞭望采集失败: %s", exc)
            return {"status": "failed", "error": str(exc)}

    register_cron_action(CronActionSpec("tech_watch.collect", _handler, "技术瞭望采集（原 assistant._router_content）"))


_register_tech_watch_collect()


def _register_subagent_spawn() -> None:
    """启动后台子代理工作流（原 assistant.capability_router → knowledge_domain）。

    每日早报（workflow=weekly_tech_digest）的正式入口；spawn 本身只入队，
    工作流在线程池执行，失败由 SubagentManager 落 run 状态，不阻塞 cron。
    """

    def _handler(context, payload):
        manager = getattr(context, "subagent_manager", None)
        if manager is None:
            return {"success": False, "error": "subagent_manager unavailable"}
        workflow = str(payload.get("workflow") or "execute_steps").strip() or "execute_steps"
        try:
            run = manager.spawn(workflow, payload=dict(payload or {}), label=str(payload.get("label") or workflow))
            return {"success": True, "status": "completed", "run": run}
        except Exception as exc:  # noqa: BLE001
            logger.error("子代理启动失败: workflow=%s %s", workflow, exc)
            return {"success": False, "status": "failed", "error": str(exc)}

    register_cron_action(CronActionSpec("subagent.spawn", _handler, "启动后台子代理工作流（每日早报等，原 capability_router）"))


_register_subagent_spawn()


def _register_journal_daily_draft() -> None:
    """生成每日日志草稿（原 assistant._router_notes）。

    逻辑对齐旧实现：汇总待办/笔记计数生成草稿笔记 + 收件箱投递；
    写操作尽量经 write_guard 留审计（context.write_guard 由 headless
    启动时挂载，缺失时降级直写并告警，与旧 router 行为一致）。
    """

    def _handler(context, payload):
        from datetime import timezone

        title = str(payload.get("title") or "每日日志草稿").strip()
        task_repo = getattr(context, "personal_task_repository", None)
        note_repo = getattr(context, "personal_note_repository", None)
        inbox_repo = getattr(context, "personal_inbox_repository", None)
        if note_repo is None or task_repo is None:
            return {"success": False, "error": "journal draft unavailable: repos missing"}
        guard = getattr(context, "write_guard", None)

        def _pending_count() -> int:
            try:
                tasks = task_repo.list_tasks(include_deleted=False)
                return sum(1 for t in tasks if not bool(t.get("is_completed", t.get("completed", False))))
            except Exception:  # noqa: BLE001
                logger.debug("journal 统计待办失败", exc_info=True)
                return -1

        def _note_count() -> int:
            try:
                return len(note_repo.list_notes(include_deleted=False))
            except Exception:  # noqa: BLE001
                logger.debug("journal 统计笔记失败", exc_info=True)
                return -1

        now = datetime.now().strftime("%Y-%m-%d %H:%M")
        pending = _pending_count()
        notes = _note_count()
        content_lines = [
            f"# {title}",
            "",
            f"生成时间: {now}",
            "",
            "## 今日进展",
            "- 自动汇总待补充",
            "",
            "## 待办摘要",
            f"- 未完成任务: {pending}",
            "",
            "## 笔记摘要",
            f"- 当前笔记总量: {notes}",
            "",
            "## 明日计划",
            "- 补充关键目标与优先级",
        ]
        new_note_data = {
            "title": title,
            "content": "\n".join(content_lines),
            "folder_path": "/助手日志",
            "tags": ["assistant", "daily"],
            "category": "assistant",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "favorite": False,
        }
        try:
            if guard is not None:
                with guard.protect(
                    "note",
                    "",
                    actor="assistant",
                    action_id="journal.daily_draft",
                    operation="create",
                    before_snapshot=None,
                ) as g:
                    note_id = note_repo.add_note(new_note_data)
                    g.set_after({"note_id": note_id, **new_note_data})
            else:
                logger.warning("journal.daily_draft 未经 write_guard 直写（context.write_guard 未挂载）")
                note_id = note_repo.add_note(new_note_data)
        except Exception as exc:  # noqa: BLE001
            logger.error("每日日志草稿生成失败: %s", exc)
            return {"success": False, "status": "failed", "error": str(exc)}
        if inbox_repo is not None:
            try:
                inbox_data = {
                    "title": f"日志已生成: {title}",
                    "content": f"note_id={note_id}",
                    "tags": ["assistant", "journal"],
                    "status": "new",
                    "created_at": datetime.now(timezone.utc).isoformat(),
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                }
                if guard is not None:
                    with guard.protect(
                        "inbox",
                        "",
                        actor="assistant",
                        action_id="journal.daily_draft",
                        operation="create",
                        before_snapshot=None,
                    ) as g:
                        inbox_repo.add_entry(inbox_data)
                        g.set_after(inbox_data)
                else:
                    inbox_repo.add_entry(inbox_data)
            except Exception:  # noqa: BLE001
                logger.warning("journal inbox entry creation failed", exc_info=True)
        return {
            "success": True,
            "status": "completed",
            "note_id": note_id,
            "pending_tasks": pending,
            "note_count": notes,
        }

    register_cron_action(CronActionSpec("journal.daily_draft", _handler, "生成每日日志草稿（原 assistant._router_notes）"))


_register_journal_daily_draft()


# ══════════════════════════════════════════════════════════════════════
# S3-2（2026-09-16）：慢同步 RPC 异步化 —— 以下 worker 侧执行体与
# RPC 侧 `*_status` 轮询配套（backend/handlers/_async_rpc.async_rpc_worker
# 统一处理「执行 + completed 事件 + 记结果」）。capability_id 与 RPC handler
# enqueue 时使用的字符串严格一致。业务失败（runner 正常返回 dict）落
# completed 不污染任务终态，只有 runner 抛异常才进 fail/重试。
# ══════════════════════════════════════════════════════════════════════


def _register_personal_review_async() -> None:
    from backend.handlers._async_rpc import async_rpc_worker

    def _runner(context, payload):
        from backend.handlers._personal import run_personal_review

        return _run_coroutine_sync(
            run_personal_review(
                context,
                period=str(payload.get("period") or "week"),
                days=int(payload.get("days") or 0),
            )
        )

    register_cron_action(
        CronActionSpec(
            "personal.review",
            async_rpc_worker("personal.review", runner=_runner),
            "个人回顾（RPC 异步化执行体，秒回 job_id + completed 事件）",
        )
    )


_register_personal_review_async()


def _register_report_daily_summary_async() -> None:
    from backend.handlers._async_rpc import async_rpc_worker

    def _runner(context, payload):
        from backend.handlers._info_proxy import run_report_daily_summary

        return _run_coroutine_sync(run_report_daily_summary(context, target_date=str(payload.get("target_date") or "")))

    register_cron_action(
        CronActionSpec(
            "report.daily_summary",
            async_rpc_worker("report.daily_summary", runner=_runner),
            "日报生成（RPC 异步化执行体，秒回 job_id + completed 事件）",
        )
    )


_register_report_daily_summary_async()


def _register_evolution_scan_and_report_async() -> None:
    from backend.handlers._async_rpc import async_rpc_worker

    def _runner(context, payload):
        from backend.handlers._evolution import run_evolution_scan_and_report

        return run_evolution_scan_and_report(context)

    register_cron_action(
        CronActionSpec(
            "evolution.scan_and_report",
            async_rpc_worker("evolution.scan_and_report", runner=_runner),
            "技术雷达扫描+早报（RPC 异步化执行体，秒回 job_id + completed 事件）",
        )
    )


_register_evolution_scan_and_report_async()


def _register_translate_text_async() -> None:
    from backend.handlers._async_rpc import async_rpc_worker

    def _runner(context, payload):
        from backend.handlers._ai_translate import run_translate_text

        return _run_coroutine_sync(
            run_translate_text(
                context,
                text=str(payload.get("text") or ""),
                source=str(payload.get("source") or "auto"),
                target=str(payload.get("target") or "zh"),
            )
        )

    register_cron_action(
        CronActionSpec(
            "translate.text",
            async_rpc_worker("translate.text", runner=_runner),
            "快速翻译（RPC 异步化执行体，秒回 job_id + completed 事件）",
        )
    )


_register_translate_text_async()


def _register_translate_full_async() -> None:
    from backend.handlers._async_rpc import async_rpc_worker

    def _runner(context, payload):
        from backend.handlers._ai_translate import run_translate_full

        return _run_coroutine_sync(
            run_translate_full(
                context,
                text=str(payload.get("text") or ""),
                source=str(payload.get("source") or "auto"),
                target=str(payload.get("target") or "zh"),
                mode=str(payload.get("mode") or "learn"),
            )
        )

    register_cron_action(
        CronActionSpec(
            "translate.full",
            async_rpc_worker("translate.full", runner=_runner),
            "深度翻译（RPC 异步化执行体，秒回 job_id + completed 事件）",
        )
    )


_register_translate_full_async()
