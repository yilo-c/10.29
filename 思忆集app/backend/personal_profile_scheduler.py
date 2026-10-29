"""Personal profile auto-extraction scheduler — 个人知识体系 ①.

每天定时扫描未处理的 raw items，自动提取画像（联系人/兴趣/焦点）写回
UserProfileService。幂等：extract 只处理 processed=0 的条目，执行后标记
processed，重启不重复。

配置（settings 键）：
- personal_profile_scheduler_enabled : 总开关（默认 True）
- personal_profile_scheduler_time    : 每日执行时间 HH:MM（默认 02:00）

依赖 context.raw_item_repo 与 context.user_profile_service（headless_main 注入）。
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime

logger = logging.getLogger("siyi.backend.personal_profile_scheduler")

_scheduler_task: asyncio.Task | None = None
_enabled = True
_schedule_time = "02:00"
_last_run_date: str | None = None
_context_ref: object | None = None

_PERSIST_KEY = "personal_profile_last_run_date"


def _persist_last_run_date(date_str: str) -> None:
    ctx = _context_ref
    if ctx is None:
        return
    try:
        ds = getattr(ctx, "local_data_store", None)
        if ds is not None and hasattr(ds, "save_json"):
            ds.save_json(_PERSIST_KEY, {"date": date_str})
    except Exception as exc:
        logger.debug("personal profile persist failed: %s", exc)


def _load_last_run_date() -> str | None:
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
        logger.debug("personal profile load failed: %s", exc)
    return None


def get_config() -> dict:
    return {
        "enabled": _enabled,
        "schedule_time": _schedule_time,
        "last_run_date": _last_run_date,
    }


def _parse_hhmm(time_str: str) -> tuple[int, int]:
    try:
        parts = str(time_str or "02:00").split(":")
        hour = int(parts[0])
        minute = int(parts[1]) if len(parts) > 1 else 0
        if 0 <= hour <= 23 and 0 <= minute <= 59:
            return hour, minute
    except (ValueError, IndexError):
        pass
    return 2, 0


def _should_run() -> bool:
    """今日尚未执行 且 已过设定时间 → 执行。"""
    today_str = datetime.now().strftime("%Y-%m-%d")
    if _last_run_date == today_str:
        return False
    now = datetime.now()
    hour, minute = _parse_hhmm(_schedule_time)
    if now.hour < hour:
        return False
    if now.hour == hour and now.minute < minute:
        return False
    return True


async def run_once() -> dict:
    """One-shot: 扫描未处理 raw items 并提取画像。"""
    global _last_run_date
    context = _context_ref
    if context is None:
        return {"success": False, "error": "context not set"}

    repo = getattr(context, "raw_item_repo", None)
    profile_svc = getattr(context, "user_profile_service", None)
    if repo is None or profile_svc is None:
        return {"success": False, "error": "raw_item_repo 或 user_profile_service 未初始化"}

    try:
        from services.personal_profile_extractor import PersonalProfileExtractor

        extractor = PersonalProfileExtractor(
            raw_item_repo=repo,
            user_profile_service=profile_svc,
            ai_client=getattr(context, "ai_client", None),
        )
        result = await asyncio.to_thread(extractor.extract, 500)
        _last_run_date = datetime.now().strftime("%Y-%m-%d")
        _persist_last_run_date(_last_run_date)
        logger.info("personal profile auto-extract done: %s", result)
        return {"success": True, **result}
    except Exception as exc:
        logger.error("personal profile auto-extract failed: %s", exc, exc_info=True)
        return {"success": False, "error": str(exc)}


async def _loop() -> None:
    global _enabled
    while _enabled:
        try:
            await asyncio.sleep(1800)  # 每半小时检查一次
            if not _enabled:
                break
            if _should_run():
                await run_once()
        except asyncio.CancelledError:
            break
        except Exception as exc:
            logger.error("personal profile loop error: %s", exc)


def set_context(context) -> None:
    global _context_ref, _last_run_date
    _context_ref = context
    if _last_run_date is None:
        loaded = _load_last_run_date()
        if loaded:
            _last_run_date = loaded


def restart() -> None:
    global _scheduler_task
    cancel()
    if not _enabled:
        return
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return  # 事件循环未就绪（如测试同步阶段）
    _scheduler_task = asyncio.create_task(_loop())


def cancel() -> None:
    global _scheduler_task
    if _scheduler_task is not None:
        _scheduler_task.cancel()
        _scheduler_task = None


def configure(enabled: bool = True, time: str = "02:00") -> dict:
    """启用/停用自动提取，可调整每日执行时间。"""
    global _enabled, _schedule_time
    _enabled = enabled
    _schedule_time = time or "02:00"
    restart()
    logger.info("personal profile scheduler %s (time=%s)", "enabled" if enabled else "disabled", _schedule_time)
    return {"success": True, "enabled": _enabled, "time": _schedule_time}
