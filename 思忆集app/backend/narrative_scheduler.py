"""周度叙事生成调度器 — 每周自动生成"你的轨迹"。

依赖 memory/narrative/weekly_narrative.py 与 memory/narrative/narrative_store.py。

设计遵循 backend 兄弟调度器（weekly_report_scheduler / personal_profile_scheduler）
的 ``set_context`` 注入约定：启动时由 headless_main 注入 AppContext，
运行时从 context 读取记忆/笔记、生成叙事、投递收件箱并持久化到 NarrativeStore。

与周报调度器的差异：叙事引擎走 WeeklyNarrativeGenerator 管线（里程碑/情绪/叙事正文），
且按 ISO 周（%Y-W%V）去重而非按自然日。
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

logger = logging.getLogger("siyi.backend.narrative_scheduler")

_scheduler_task: asyncio.Task | None = None
_enabled = False
_context_ref: object | None = None
_last_run_week: str | None = None  # ISO 周标签 YYYY-Wnn，上次成功生成

_PERSIST_KEY = "narrative_last_run_week"
_PERSIST_KEY_MONTHLY = "narrative_last_run_month"  # T7.1: 月度信去重标记 YYYY-MM


# ── 持久化（best-effort，复用 context.local_data_store）──────────────


def _persist_last_run_week(week_label: str) -> None:
    ctx = _context_ref
    if ctx is None:
        return
    try:
        ds = getattr(ctx, "local_data_store", None)
        if ds is not None and hasattr(ds, "save_json"):
            ds.save_json(_PERSIST_KEY, {"week": week_label})
    except Exception as exc:
        logger.debug("narrative persist failed: %s", exc)


def _load_last_run_week() -> str | None:
    ctx = _context_ref
    if ctx is None:
        return None
    try:
        ds = getattr(ctx, "local_data_store", None)
        if ds is not None and hasattr(ds, "load_json"):
            data = ds.load_json(_PERSIST_KEY, {})
            if isinstance(data, dict):
                value = data.get("week")
                if isinstance(value, str) and value:
                    return value
    except Exception as exc:
        logger.debug("narrative load failed: %s", exc)
    return None


def _current_week_label() -> str:
    return datetime.now(timezone.utc).strftime("%Y-W%V")


# ── 月度信去重持久化（T7.1）────────────────────────────────

_last_run_month: str | None = None  # YYYY-MM，上次成功生成的月份


def _persist_last_run_month(month_label: str) -> None:
    ctx = _context_ref
    if ctx is None:
        return
    try:
        ds = getattr(ctx, "local_data_store", None)
        if ds is not None and hasattr(ds, "save_json"):
            ds.save_json(_PERSIST_KEY_MONTHLY, {"month": month_label})
    except Exception as exc:
        logger.debug("monthly letter persist failed: %s", exc)


def _load_last_run_month() -> str | None:
    ctx = _context_ref
    if ctx is None:
        return None
    try:
        ds = getattr(ctx, "local_data_store", None)
        if ds is not None and hasattr(ds, "load_json"):
            data = ds.load_json(_PERSIST_KEY_MONTHLY, {})
            if isinstance(data, dict):
                value = data.get("month")
                if isinstance(value, str) and value:
                    return value
    except Exception as exc:
        logger.debug("monthly letter load failed: %s", exc)
    return None


def _current_month_label() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m")


def _resolve_data_dir(context) -> str | None:
    """从 context.local_data_store.base_dir 解析叙事存储目录。"""
    ds = getattr(context, "local_data_store", None)
    if ds is None:
        return None
    base_dir = getattr(ds, "base_dir", None)
    return base_dir or None


def _collect_source_items(context) -> tuple[list[dict], list[dict]]:
    """从 context 收集本周记忆与笔记。

    Returns:
        (memories, notes) 两个列表。
    """
    memories: list[dict] = []
    notes: list[dict] = []

    # 记忆：memory_index_service.recall 返回 {"items": [...], "count": N}
    try:
        mem_svc = getattr(context, "memory_index_service", None)
        if mem_svc is not None and hasattr(mem_svc, "recall"):
            result = mem_svc.recall("", top_k=200)
            if isinstance(result, dict):
                items = result.get("items") or []
            else:
                items = result or []
            memories = [m for m in items if isinstance(m, dict)]
    except Exception as exc:
        logger.debug("collect memories failed: %s", exc)

    # 笔记：personal_note_repository.list_notes()
    try:
        note_repo = getattr(context, "personal_note_repository", None)
        if note_repo is not None and hasattr(note_repo, "list_notes"):
            notes = note_repo.list_notes() or []
    except Exception as exc:
        logger.debug("collect notes failed: %s", exc)

    return memories, notes


async def _generate_and_post(context) -> dict | None:
    """一次性生成并投递周度叙事。

    无可用数据（记忆与笔记均为空）时返回 None 且不投递。

    Returns:
        生成的叙事字典；无数据时返回 None。
    """
    data_dir = _resolve_data_dir(context)
    if not data_dir:
        logger.warning("data dir unresolved, skipping narrative generation")
        return None

    memories, notes = _collect_source_items(context)
    if not memories and not notes:
        logger.info("no source items this week, skipping narrative generation")
        return None

    try:
        from memory.narrative.narrative_store import NarrativeStore
        from memory.narrative.weekly_narrative import WeeklyNarrativeGenerator

        store = NarrativeStore(data_dir)
        generator = WeeklyNarrativeGenerator(narrative_store=store, ai_text_service=getattr(context, "ai_text_service", None))
        # WeeklyNarrativeGenerator.generate 为 async 协程
        narrative = await generator.generate(memories, notes)
        if not narrative:
            return None

        # 投递到收件箱
        inbox_repo = getattr(context, "personal_inbox_repository", None)
        if inbox_repo is not None and hasattr(inbox_repo, "add_entry"):
            inbox_repo.add_entry(
                {
                    "title": f"📖 {narrative.get('title', '周度叙事')}",
                    "content": narrative.get("content", ""),
                    "kind": "narrative",
                    "tags": ["叙事", "自动生成", narrative.get("week_label", "")],
                    "status": "new",
                    "metadata": {
                        "narrative_id": narrative.get("narrative_id"),
                        "week_label": narrative.get("week_label"),
                        "item_count": narrative.get("item_count"),
                    },
                }
            )
            logger.info("weekly narrative posted to inbox")
        return narrative
    except Exception as exc:
        logger.error("narrative generation failed: %s", exc, exc_info=True)
        return None


async def _run_weekly() -> dict:
    """模块级周度执行入口（由 _loop 或外部手动触发）。"""
    global _last_run_week
    context = _context_ref
    if context is None:
        logger.warning("context not set, skipping narrative generation")
        return {"success": False, "error": "context not set"}

    narrative = await _generate_and_post(context)
    if narrative is None:
        return {"success": False, "error": "no source data"}
    _last_run_week = narrative.get("week_label") or _current_week_label()
    _persist_last_run_week(_last_run_week)
    logger.info("weekly narrative generated (%s)", _last_run_week)
    return {"success": True, "week_label": _last_run_week}


async def _generate_and_post_monthly(context) -> dict | None:
    """一次性生成并投递月度信（T7.1）。

    复用 _collect_source_items 采集，但聚合到月度粒度（整月数据生成一篇）。
    无可用数据时返回 None 且不投递。
    """
    data_dir = _resolve_data_dir(context)
    if not data_dir:
        logger.warning("data dir unresolved, skipping monthly letter generation")
        return None

    memories, notes = _collect_source_items(context)
    if not memories and not notes:
        logger.info("no source items this month, skipping monthly letter")
        return None

    try:
        from memory.narrative.narrative_store import NarrativeStore
        from memory.narrative.monthly_letter import MonthlyLetterGenerator

        store = NarrativeStore(data_dir)
        generator = MonthlyLetterGenerator(narrative_store=store, ai_text_service=getattr(context, "ai_text_service", None))
        narrative = await generator.generate(memories, notes)
        if not narrative:
            return None

        # 投递到收件箱
        inbox_repo = getattr(context, "personal_inbox_repository", None)
        if inbox_repo is not None and hasattr(inbox_repo, "add_entry"):
            inbox_repo.add_entry(
                {
                    "title": f"📮 {narrative.get('title', '月度信')}",
                    "content": narrative.get("content", ""),
                    "kind": "narrative",
                    "tags": ["月度信", "自动生成", narrative.get("month_label", "")],
                    "status": "new",
                    "metadata": {
                        "narrative_id": narrative.get("narrative_id"),
                        "month_label": narrative.get("month_label"),
                        "item_count": narrative.get("item_count"),
                    },
                }
            )
            logger.info("monthly letter posted to inbox")
        return narrative
    except Exception as exc:
        logger.error("monthly letter generation failed: %s", exc, exc_info=True)
        return None


async def _run_monthly() -> dict:
    """模块级月度执行入口（由 _loop 或外部手动触发）。"""
    global _last_run_month
    context = _context_ref
    if context is None:
        logger.warning("context not set, skipping monthly letter generation")
        return {"success": False, "error": "context not set"}

    narrative = await _generate_and_post_monthly(context)
    if narrative is None:
        return {"success": False, "error": "no source data"}
    _last_run_month = narrative.get("month_label") or _current_month_label()
    _persist_last_run_month(_last_run_month)
    logger.info("monthly letter generated (%s)", _last_run_month)
    return {"success": True, "month_label": _last_run_month}


async def generate_monthly_now(context=None) -> dict | None:
    """按需触发月度信生成（RPC memory.narrative.generate_monthly 用）。"""
    if context is None:
        context = _context_ref
    if context is None:
        logger.warning("context not set, cannot generate monthly letter")
        return None
    return await _generate_and_post_monthly(context)


async def generate_now(context) -> dict | None:
    """按需触发叙事生成，复用同一生成管线但不改周度去重标记。

    适用于手动 RPC（如 memory.narrative.generate_now）或调试，避免污染
    ``_last_run_week`` 导致周度调度漏跑/重跑。
    """
    if context is None:
        context = _context_ref
    if context is None:
        logger.warning("context not set, cannot generate narrative")
        return None
    return await _generate_and_post(context)


async def _loop() -> None:
    """后台循环：每 ISO 周至少生成一次周报叙事（含跨周补跑）。

    历史演进（M1，2026-08-29）：
    1. 初版仅在固定小时窗口触发 → 窗口内进程重启即错过整周；
    2. 改为"任意 UTC 周一 + 周标签不同"→ 仍要求进程**必须在周一在线**；
       实测日志显示整周启动都落在周二/三/五，周一仅在 06:11-06:12 短暂运行，
       导致 `data/data/narratives/` 始终为空、周报从未产出；
    3. 现版：只要"当前 ISO 周 ≠ 上次成功生成的周"就补跑（不限周一），
       保证每周至少一篇；周一仍是最自然的产出时机，但不再是硬性前提。

    生成失败（如无源数据）不写 `_last_run_week`，下一轮（1 小时后）继续尝试。
    """
    global _enabled
    while _enabled:
        try:
            # 先检查再休眠：错过窗口的进程启动后立刻补跑，不必空等一小时
            week_label = _current_week_label()
            if _last_run_week != week_label:
                logger.info(
                    "narrative catch-up: last_run_week=%s current=%s（周报补跑，不要求周一）",
                    _last_run_week,
                    week_label,
                )
                await _run_weekly()

            # T7.1: 月度信维持原语义——只在每月 1 日生成（跨月去重）。
            # 月度信是"整月回顾"，月中补跑没有意义，故不套用周报的补跑策略。
            if datetime.now(timezone.utc).day == 1:
                month_label = _current_month_label()
                if _last_run_month != month_label:
                    await _run_monthly()

            await asyncio.sleep(3600)  # 每小时检查一次
            if not _enabled:
                break
        except asyncio.CancelledError:
            break
        except Exception as exc:
            logger.error("narrative loop error: %s", exc)
            await asyncio.sleep(3600)


def set_context(context) -> None:
    """注入 AppContext（headless_main 启动时调用）。"""
    global _context_ref, _last_run_week, _last_run_month
    _context_ref = context
    if _last_run_week is None:
        loaded = _load_last_run_week()
        if loaded:
            _last_run_week = loaded
    if _last_run_month is None:
        loaded_month = _load_last_run_month()
        if loaded_month:
            _last_run_month = loaded_month


def cancel() -> None:
    """取消后台任务。"""
    global _scheduler_task
    if _scheduler_task is not None:
        _scheduler_task.cancel()
        _scheduler_task = None


async def shutdown() -> None:
    """关闭调度器（异步，可由调用方 await）。"""
    cancel()


def _apply_enabled_state() -> None:
    """根据 _enabled 启停后台任务；事件循环未就绪时静默跳过。"""
    global _scheduler_task
    if not _enabled:
        cancel()
        return
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return  # 事件循环未就绪（如测试同步阶段）
    if _scheduler_task is None or _scheduler_task.done():
        _scheduler_task = asyncio.create_task(_loop())


async def restart() -> None:
    """根据当前启用状态重启后台任务（异步，可由调用方 await）。"""
    cancel()
    _apply_enabled_state()


def configure(enabled: bool = True) -> dict:
    """启用/停用叙事调度。返回当前配置。"""
    global _enabled
    _enabled = enabled
    _apply_enabled_state()
    logger.info("narrative scheduler %s", "enabled" if enabled else "disabled")
    return {"success": True, "enabled": _enabled}


def is_enabled() -> bool:
    return _enabled


def get_config() -> dict:
    return {
        "enabled": _enabled,
        "last_run_week": _last_run_week,
    }
