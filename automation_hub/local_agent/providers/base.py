"""local_agent.providers.base — LLM 提供者抽象基类。"""

from __future__ import annotations

import abc
from dataclasses import dataclass, field
from typing import Any, AsyncIterator


@dataclass
class ChatMessage:
    role: str          # system | user | assistant | tool
    content: str
    name: str | None = None
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    tool_call_id: str | None = None


@dataclass
class ChatResponse:
    content: str
    finish_reason: str = "stop"
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    usage: dict[str, int] = field(default_factory=dict)


class ProviderBase(abc.ABC):
    """LLM 提供者接口。"""

    name: str = "base"

    @abc.abstractmethod
    async def chat(self, messages: list[ChatMessage], **kwargs: Any) -> ChatResponse:
        """同步聊天补全。"""

    async def chat_stream(self, messages: list[ChatMessage], **kwargs: Any) -> AsyncIterator[str]:
        """流式聊天补全（默认回退到非流）。"""
        resp = await self.chat(messages, **kwargs)
        yield resp.content

    @abc.abstractmethod
    async def close(self) -> None:
        """释放资源。"""
