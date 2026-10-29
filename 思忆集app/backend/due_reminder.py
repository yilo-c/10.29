"""单词 due_reminder 调度器 — 每天首次启动 / 跨过午夜 / 距上次 ≥6h 时，
   统计当前到期复习词数，emit `vocabulary.due_reminder` 给 Orb。

设计：
  - 每 5 分钟扫一次（异步任务），节流：同日内最多 1 次
  - 状态写 vocab_reminder.json（last_reminded_date / last_due_count）
  - 仅在 due_count ≥ 1 时触发，避免空提醒
  - 启动时延迟 30s，让前端 WebSocket 连上再触发，确保 Orb 不漏掉
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any

from core.events import AppEvent, event_bus

logger = logging.getLogger("siyi.backend.due_reminder")

REMINDER_KEY = "vocab_reminder"     # data_store 中的 json 名
SCAN_INTERVAL_SECONDS = 300         # 5 分钟扫一次
INITIAL_DELAY_SECONDS = 30          # 启动延迟，等前端连上
COOLDOWN_HOURS = 6                  # 同日内首次提醒后 6h 内不再提醒
MIN_DUE_COUNT = 1                   # 至少多少 due 才提醒


def _today_local_date() -> str:
    """以本地时区返回 YYYY-MM-DD，避免 UTC 跨日跳动。"""
    # AppEvent.created_at 默认 UTC；这里我们要"用户本地的今天"
    return datetime.now().strftime("%Y-%m-%d")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class DueReminderScheduler:
    def __init__(self) -> None:
        self._task: asyncio.Task | None = None
        self._running = False
        self._context: Any = None

    def start(self, context: Any) -> None:
        if self._running:
            return
        self._context = context
        self._running = True
        loop = asyncio.get_running_loop()
        self._task = loop.create_task(self._run_loop())
        logger.info("due reminder scheduler started")

    def stop(self) -> None:
        if not self._running:
            return
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
        logger.info("due reminder scheduler stopped")

    async def _run_loop(self) -> None:
        try:
            await asyncio.sleep(INITIAL_DELAY_SECONDS)
            while self._running:
                try:
                    reminded = await self._tick()
                except Exception:
                    logger.exception("due reminder tick failed")
                    reminded = False
                # P3 优化：提醒成功后进入长睡眠（cooldown），避免继续 5 分钟空转扫描
                if reminded:
                    await asyncio.sleep(COOLDOWN_HOURS * 3600)
                else:
                    await asyncio.sleep(SCAN_INTERVAL_SECONDS)
        except asyncio.CancelledError:
            pass

    async def _tick(self) -> bool:
        """执行一次到期检查。返回 True 表示已发出提醒。"""
        ctx = self._context
        if ctx is None:
            return False
        repo = getattr(ctx, "vocabulary_repository", None)
        ds = getattr(ctx, "local_data_store", None)
        if not repo or not ds:
            return False

        # 读取上次提醒状态
        state = await asyncio.to_thread(ds.load_json, REMINDER_KEY, {})
        if not isinstance(state, dict):
            state = {}
        today = _today_local_date()
        last_date = state.get("last_reminded_date")
        last_at = state.get("last_reminded_at")

        if last_date == today:
            # 今天已提醒过；除非超过 cooldown 才再发（保证一天最多 1 次主动提醒）
            return False

        # 统计 due 数
        try:
            now_iso = _now_iso()
            words = await asyncio.to_thread(repo.list_words)
            due_count = sum(
                1 for w in words
                if w.get("next_review") and w["next_review"] <= now_iso
            )
        except Exception:
            logger.debug("count due failed", exc_info=True)
            return False

        if due_count < MIN_DUE_COUNT:
            return False

        # emit
        try:
            event_bus.emit(AppEvent(
                event_type="vocabulary.due_reminder",
                payload={
                    "due_count": due_count,
                    "today": today,
                    "message": _compose_message(due_count, last_date),
                },
                source="due_reminder",
            ))
            logger.info("emitted vocabulary.due_reminder due_count=%d", due_count)
        except Exception:
            logger.exception("emit due_reminder failed")
            return False

        # 更新状态
        state.update({
            "last_reminded_date": today,
            "last_reminded_at": _now_iso(),
            "last_due_count": due_count,
        })
        try:
            await asyncio.to_thread(ds.save_json, REMINDER_KEY, state)
        except Exception:
            logger.debug("save reminder state failed", exc_info=True)
        return True


def _compose_message(due_count: int, last_date: str | None) -> str:
    """构造小问的开口语 — 由前端 Orb/ChatPanel 决定是否注入聊天。"""
    if not last_date:
        return f"有 {due_count} 个词在等你了，先来过两个？"
    if due_count >= 30:
        return f"今天 {due_count} 个词到期了…堆成山了哦，别再往后拖了。"
    if due_count >= 10:
        return f"积了 {due_count} 个词没复习，要不要趁现在过一遍？"
    return f"{due_count} 个词在等复习，三两分钟就能搞定。"


# 全局单例
due_reminder_scheduler = DueReminderScheduler()
