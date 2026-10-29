"""local_agent.bus — 本地事件总线。

参考 nanobot 的 MessageBus 思路，使用 asyncio.Queue 解耦
channel / CLI 与 runtime loop 之间的消息流转。
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any


@dataclass
class InboundMessage:
    """来自用户或渠道的入站消息。"""
    channel: str  # "cli", "tray", "webhook", ...
    chat_id: str
    content: str
    message_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class OutboundMessage:
    """本地代理向用户/渠道发出的出站消息。"""
    channel: str
    chat_id: str
    content: str
    metadata: dict[str, Any] = field(default_factory=dict)


class LocalBus:
    """本地 async 消息总线。"""

    def __init__(self) -> None:
        self.inbound: asyncio.Queue[InboundMessage] = asyncio.Queue()
        self.outbound: asyncio.Queue[OutboundMessage] = asyncio.Queue()

    async def publish_inbound(self, msg: InboundMessage) -> None:
        await self.inbound.put(msg)

    async def consume_inbound(self) -> InboundMessage:
        return await self.inbound.get()

    async def publish_outbound(self, msg: OutboundMessage) -> None:
        await self.outbound.put(msg)

    async def consume_outbound(self) -> OutboundMessage:
        return await self.outbound.get()

    @property
    def inbound_size(self) -> int:
        return self.inbound.qsize()

    @property
    def outbound_size(self) -> int:
        return self.outbound.qsize()
