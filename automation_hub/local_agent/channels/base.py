"""local_agent.channels.base — 通道抽象基类。"""

from __future__ import annotations

import abc
from typing import Any


class ChannelBase(abc.ABC):
    """交互通道接口（CLI、WebSocket、Telegram 等）。"""

    name: str = "base"

    @abc.abstractmethod
    async def run(self) -> None:
        """启动通道主循环（阻塞）。"""

    @abc.abstractmethod
    async def send(self, content: str, **kwargs: Any) -> None:
        """发送消息到用户。"""

    async def close(self) -> None:
        """关闭通道（可选覆盖）。"""
