"""行为日志归档调度器 — 每日执行 180 天滚动归档 + 归档目录上限清理（T5.3）。

背景：``core/behavior_logger.py`` 的 ``archive_old_events()`` 实现了按保留期归档，
但此前**全库无调用方**（180 天滚动从未执行，仅 50MB 大小轮转兜底），
导致 behavior_events.jsonl 无限增长、隐私保留期形同虚设。

本调度器遵循 backend 兄弟调度器（weekly_report_scheduler / narrative_scheduler）的
``set_context`` 注入约定：启动时由 headless_main 注入 AppContext，
每日执行一次归档，并把归档目录约束到最近 N 个文件（防归档目录自身无界）。

保留语义：
- 事件文件只保留近 ``retention_days``（默认 180 天）事件，更早移入归档；
- 归档目录只保留最近 ``MAX_ARCHIVE_FILES``（默认 30）个归档文件，更早删除。
"""
from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timezone

logger = logging.getLogger("siyi.backend.behavior_archive")

_scheduler_task: asyncio.Task | None = None
_enabled = False
_context_ref: object | None = None
_last_run_date: str | None = None  # YYYY-MM-DD of last successful run

_PERSIST_KEY = "behavior_archive_last_run_date"

# 归档目录保留最近 30 个归档文件（30 天粒度 × 30 份 = 约 3 年回溯，足够审计需求）
MAX_ARCHIVE_FILES = 30


def _persist_last_run_date(date_str: str) -> None:
    """Best-effort persist last-run date through context.local_data_store."""
    ctx = _context_ref
    if ctx is None:
        return
    try:
        ds = getattr(ctx, "local_data_store", None)
        if ds is not None and hasattr(ds, "save_json"):
            ds.save_json(_PERSIST_KEY, {"date": date_str})
    except Exception as exc:
        logger.debug("behavior archive persist failed: %s", exc)


def _load_last_run_date() -> str | None:
    """Best-effort load last-run date from context.local_data_store."""
    ctx = _context_ref
    if ctx is None:
        return None
    try:
        ds = getattr(ctx, "local_data_store", None)
        if ds is not None and hasattr(ds, "load_json"):
            data = ds.load_json(_PERSIST_KEY, {})
            if isinstance(data, dict):
                value = data.get("date")
                if isinstance(value, str) and value:
                    return value
    except Exception as exc:
        logger.debug("behavior archive load failed: %s", exc)
    return None


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _clean_archive_dir(archive_dir: str) -> int:
    """清理归档目录，只保留最近 MAX_ARCHIVE_FILES 个文件。返回删除的文件数。

    归档文件名带日期戳（behavior_events_YYYYMMDD.jsonl / _overflow_ 等），
    按文件名倒序（最新日期在前）保留上限个，其余删除。
    """
    if not archive_dir or not os.path.isdir(archive_dir):
        return 0
    try:
        files = [
            os.path.join(archive_dir, f)
            for f in os.listdir(archive_dir)
            if f.endswith(".jsonl")
        ]
    except OSError:
        return 0
    files.sort(reverse=True)  # 文件名倒序 ≈ 日期倒序
    removed = 0
    for old in files[MAX_ARCHIVE_FILES:]:
        try:
            os.unlink(old)
            removed += 1
        except OSError as exc:
            logger.warning("behavior archive cleanup failed to remove %s: %s", old, exc)
    if removed:
        logger.info("behavior archive cleanup removed %d old files (keep %d)", removed, MAX_ARCHIVE_FILES)
    return removed


async def _archive_and_cleanup() -> dict:
    """执行一次归档 + 归档目录清理。返回统计。

    按日去重：当日已成功归档过则跳过（幂等，防多实例/重复触发）。
    """
    global _last_run_date
    today = _today()
    if _load_last_run_date() == today:
        return {"archived": 0, "cleaned": 0, "deduped": True}
    ctx = _context_ref
    if ctx is None:
        return {"archived": 0, "cleaned": 0, "error": "no context"}
    try:
        logger = logging.getLogger("siyi.backend.behavior_archive")
        archived = 0
        cleaned = 0
        behavior_logger = getattr(ctx, "behavior_logger", None)
        if behavior_logger is None:
            # 兜底：从 providers 重建（异常路径，尽力而为）
            from core.behavior_logger import BehaviorLogger
            data_dir = getattr(getattr(ctx, "local_data_store", None), "base_dir", None)
            if data_dir:
                behavior_logger = BehaviorLogger(data_dir=str(data_dir))
        if behavior_logger is not None and hasattr(behavior_logger, "archive_old_events"):
            # archive_old_events 是同步 + 内部持锁，放到线程避免阻塞事件循环
            archived = await asyncio.to_thread(behavior_logger.archive_old_events)
            archive_dir = getattr(behavior_logger, "_archive_dir", None)
            if archive_dir:
                cleaned = await asyncio.to_thread(_clean_archive_dir, archive_dir)
        _persist_last_run_date(_today())
        logger.info("behavior archive done: archived=%d cleaned=%d", archived, cleaned)
        return {"archived": archived, "cleaned": cleaned}
    except Exception as exc:
        logger.exception("behavior archive failed: %s", exc)
        return {"archived": 0, "cleaned": 0, "error": str(exc)}


async def _daily_loop() -> None:
    """每日循环：启动即执行一次（补历史缺口），随后每 24h 一次。

    去重由 _archive_and_cleanup 内部按日持久化状态保证。
    """
    while True:
        await _archive_and_cleanup()
        await asyncio.sleep(24 * 3600)


def set_context(context) -> None:
    """headless_main 启动时注入 AppContext。"""
    global _context_ref
    _context_ref = context


def configure(enabled: bool = True) -> None:
    """启用/停用归档调度。"""
    global _enabled, _scheduler_task
    if enabled and not _enabled:
        # L1（2026-08-29）：asyncio.get_event_loop() 在 3.10+ 已弃用，无运行中循环时
        # 行为不确定（3.12+ 直接抛 RuntimeError）。与兄弟调度器 narrative_scheduler
        # 对齐，改用 get_running_loop()，并要求调用方处于 async 上下文中。
        _scheduler_task = asyncio.get_running_loop().create_task(_daily_loop())
        logger.info("behavior archive scheduler started (daily, retention=180d)")
    elif not enabled and _enabled:
        if _scheduler_task is not None:
            _scheduler_task.cancel()
            _scheduler_task = None
        logger.info("behavior archive scheduler stopped")
    _enabled = enabled


async def run_once(context=None) -> dict:
    """手动执行一次归档（调试/运维）。返回统计。"""
    global _context_ref
    if context is not None:
        _context_ref = context
    return await _archive_and_cleanup()
