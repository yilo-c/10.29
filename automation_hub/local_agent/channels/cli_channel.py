"""local_agent.channels.cli_channel — 终端交互通道。"""

from __future__ import annotations

import asyncio
import logging
import sys
import uuid
from typing import Any

from local_agent.bus import InboundMessage, LocalBus
from local_agent.channels.base import ChannelBase

logger = logging.getLogger("local_agent.channels.cli")

PROMPT = "\033[36magent>\033[0m "


class CLIChannel(ChannelBase):
    """终端 stdin/stdout 交互通道。"""

    name = "cli"

    def __init__(self, bus: LocalBus, session_id: str | None = None) -> None:
        self._bus = bus
        self._session_id = session_id or str(uuid.uuid4())
        self._running = False

    async def run(self) -> None:
        self._running = True
        # 启动输出监听
        out_task = asyncio.create_task(self._output_loop())
        try:
            await self._input_loop()
        finally:
            self._running = False
            out_task.cancel()
            try:
                await out_task
            except asyncio.CancelledError:
                pass

    async def _input_loop(self) -> None:
        loop = asyncio.get_event_loop()
        while self._running:
            try:
                line = await loop.run_in_executor(None, lambda: input(PROMPT))
            except (EOFError, KeyboardInterrupt):
                break
            line = line.strip()
            if not line:
                continue
            if line.lower() in ("exit", "quit", "q"):
                break

            # 解析简单的工具调用语法: /tool_name arg1=val1 arg2=val2
            if line.startswith("/"):
                parts = line[1:].split(maxsplit=1)
                tool_name = parts[0]
                raw_args = parts[1] if len(parts) > 1 else ""
                args = self._parse_kv(raw_args)
                await self._bus.send_inbound(InboundMessage(
                    session_id=self._session_id,
                    content=line,
                    metadata={"tool": tool_name, "args": args},
                ))
            else:
                await self._bus.send_inbound(InboundMessage(
                    session_id=self._session_id,
                    content=line,
                ))

    async def _output_loop(self) -> None:
        while self._running:
            msg = await self._bus.recv_outbound()
            print(f"\033[33m{msg.content}\033[0m")

    async def send(self, content: str, **kwargs: Any) -> None:
        print(f"\033[33m{content}\033[0m")

    @staticmethod
    def _parse_kv(raw: str) -> dict[str, str]:
        """解析 key=value 对。"""
        result: dict[str, str] = {}
        if not raw:
            return result
        for token in raw.split():
            if "=" in token:
                k, v = token.split("=", 1)
                result[k] = v
            else:
                result.setdefault("command", "")
                result["command"] = (result["command"] + " " + token).strip()
        return result
