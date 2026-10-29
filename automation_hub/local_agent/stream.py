"""local_agent.stream — 流式输出处理。"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Awaitable

logger = logging.getLogger("local_agent.stream")


@dataclass
class StreamChunk:
    """一段流式输出。"""
    content: str
    channel: str = "stdout"           # stdout | stderr | system
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    metadata: dict[str, Any] = field(default_factory=dict)


class StreamBuffer:
    """缓冲流式输出，定期发送到控制面。"""

    def __init__(
        self,
        flush_fn: Callable[[list[StreamChunk]], Awaitable[None]],
        flush_interval: float = 2.0,
        max_buffer_size: int = 200,
    ) -> None:
        self._flush_fn = flush_fn
        self._interval = flush_interval
        self._max_size = max_buffer_size
        self._buffer: list[StreamChunk] = []
        self._lock = asyncio.Lock()
        self._flusher_task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        if self._flusher_task is None:
            self._flusher_task = asyncio.create_task(self._periodic_flush())

    async def stop(self) -> None:
        if self._flusher_task:
            self._flusher_task.cancel()
            try:
                await self._flusher_task
            except asyncio.CancelledError:
                pass
            self._flusher_task = None
        await self.flush()

    async def write(self, content: str, channel: str = "stdout", **meta: Any) -> None:
        chunk = StreamChunk(content=content, channel=channel, metadata=meta)
        async with self._lock:
            self._buffer.append(chunk)
            if len(self._buffer) >= self._max_size:
                await self._do_flush()

    async def flush(self) -> None:
        async with self._lock:
            await self._do_flush()

    async def _do_flush(self) -> None:
        if not self._buffer:
            return
        batch, self._buffer = self._buffer[:], []
        try:
            await self._flush_fn(batch)
        except Exception:
            logger.exception("flush failed, %d chunks dropped", len(batch))

    async def _periodic_flush(self) -> None:
        while True:
            await asyncio.sleep(self._interval)
            await self.flush()
