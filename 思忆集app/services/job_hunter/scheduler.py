# -*- coding: utf-8 -*-
"""求职猎手定时调度器。"""
from __future__ import annotations

import asyncio
import logging
import random
from typing import Any

from core.events import AppEvent, event_bus

logger = logging.getLogger("siyi.services.job_hunter.scheduler")

DEFAULT_INTERVAL = 21600
FIRST_RUN_JITTER_MAX = 300
BACKOFF_BASE = 30
BACKOFF_CAP = 1800
MAX_CONSECUTIVE_FAILURES = 10


class JobHunterScheduler:
    """定时抓取职位并推送新职位事件。"""

    def __init__(self) -> None:
        self._task: Any = None
        self._enabled = False
        self._interval = DEFAULT_INTERVAL
        self._service: Any = None
        self._keywords: list[str] | None = None
        self._city = ""
        self._platforms: list[str] | None = None

    def set_service(self, service: Any) -> None:
        self._service = service

    def configure(
        self,
        enabled: bool,
        interval: int = 21600,
        keywords: list[str] | None = None,
        city: str = "",
        platforms: list[str] | None = None,
    ) -> dict[str, Any]:
        self._enabled = enabled
        self._interval = max(1800, interval)
        self._keywords = keywords
        self._city = city
        self._platforms = platforms
        self.restart()
        logger.info(
            "job hunter scheduler %s (interval=%ds, platforms=%s)",
            "enabled" if enabled else "disabled",
            self._interval,
            self._platforms,
        )
        return {
            "success": True,
            "enabled": self._enabled,
            "interval": self._interval,
            "keywords": self._keywords,
            "city": self._city,
            "platforms": self._platforms,
        }

    def restart(self) -> None:
        self.cancel()
        if self._enabled:
            self._task = asyncio.create_task(self._loop())

    def cancel(self) -> None:
        if self._task is not None:
            self._task.cancel()
            self._task = None

    def get_config(self) -> dict[str, Any]:
        return {
            "enabled": self._enabled,
            "interval": self._interval,
            "keywords": self._keywords,
            "city": self._city,
            "platforms": self._platforms,
        }

    async def _loop(self) -> None:
        first_run = True
        consecutive_failures = 0
        while self._enabled:
            try:
                # 首次运行加入随机抖动，避免重启风暴
                if first_run:
                    jitter = random.uniform(0, FIRST_RUN_JITTER_MAX)
                    logger.info("job hunter scheduler first-run jitter: %.1fs", jitter)
                    await asyncio.sleep(jitter)
                    first_run = False
                    if not self._enabled:
                        return
                if self._service is None:
                    await asyncio.sleep(self._interval)
                    continue
                result = await self._service.search(
                    platforms=self._platforms,
                    keywords=self._keywords,
                    city=self._city,
                    page=1,
                )
                added_ids = result.get("added_ids", [])
                if added_ids:
                    match_result = await self._service.match_jobs(job_ids=added_ids)
                    event_bus.emit(
                        AppEvent(
                            "job_hunter.new_jobs",
                            payload={
                                "added": result.get("added", 0),
                                "platform_counts": result.get("platform_counts", {}),
                                "matched": match_result.get("matched", 0),
                            },
                        )
                    )
                consecutive_failures = 0
                await asyncio.sleep(self._interval)
            except asyncio.CancelledError:
                return
            except Exception as exc:
                # 指数退避重试，连续失败过多时自动禁用
                backoff = min(BACKOFF_BASE * 2 ** consecutive_failures, BACKOFF_CAP)
                consecutive_failures += 1
                logger.warning(
                    "job hunter scheduler loop failed (#%d): %s; retry in %ds",
                    consecutive_failures,
                    exc,
                    backoff,
                )
                if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                    logger.error(
                        "job hunter scheduler disabled after %d consecutive failures",
                        consecutive_failures,
                    )
                    self._enabled = False
                    try:
                        event_bus.emit(
                            AppEvent(
                                "job_hunter.scheduler_disabled",
                                payload={
                                    "reason": "max_consecutive_failures",
                                    "consecutive_failures": consecutive_failures,
                                },
                            )
                        )
                    except Exception:
                        pass
                    return
                try:
                    await asyncio.sleep(backoff)
                except asyncio.CancelledError:
                    return
