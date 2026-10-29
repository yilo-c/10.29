"""local_agent.providers.openai_provider — OpenAI 兼容 LLM 提供者。"""

from __future__ import annotations

import json
import logging
from typing import Any, AsyncIterator

from local_agent.providers.base import ChatMessage, ChatResponse, ProviderBase

logger = logging.getLogger("local_agent.providers.openai")


class OpenAIProvider(ProviderBase):
    """OpenAI / Azure OpenAI / 兼容 API 提供者。"""

    name = "openai"

    def __init__(
        self,
        api_key: str,
        model: str = "gpt-4o-mini",
        base_url: str | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.3,
    ) -> None:
        try:
            from openai import AsyncOpenAI
        except ImportError as exc:
            raise ImportError("pip install openai") from exc

        kwargs: dict[str, Any] = {"api_key": api_key}
        if base_url:
            kwargs["base_url"] = base_url
        self._client = AsyncOpenAI(**kwargs)
        self._model = model
        self._max_tokens = max_tokens
        self._temperature = temperature

    def _to_api_messages(self, messages: list[ChatMessage]) -> list[dict[str, Any]]:
        api_msgs: list[dict[str, Any]] = []
        for m in messages:
            msg: dict[str, Any] = {"role": m.role, "content": m.content}
            if m.name:
                msg["name"] = m.name
            if m.tool_call_id:
                msg["tool_call_id"] = m.tool_call_id
            if m.tool_calls:
                msg["tool_calls"] = m.tool_calls
            api_msgs.append(msg)
        return api_msgs

    async def chat(self, messages: list[ChatMessage], **kwargs: Any) -> ChatResponse:
        resp = await self._client.chat.completions.create(
            model=kwargs.get("model", self._model),
            messages=self._to_api_messages(messages),
            max_tokens=kwargs.get("max_tokens", self._max_tokens),
            temperature=kwargs.get("temperature", self._temperature),
            tools=kwargs.get("tools"),
            tool_choice=kwargs.get("tool_choice"),
        )
        choice = resp.choices[0]
        tool_calls: list[dict[str, Any]] = []
        if choice.message.tool_calls:
            for tc in choice.message.tool_calls:
                tool_calls.append({
                    "id": tc.id,
                    "type": tc.type,
                    "function": {"name": tc.function.name, "arguments": tc.function.arguments},
                })
        return ChatResponse(
            content=choice.message.content or "",
            finish_reason=choice.finish_reason or "stop",
            tool_calls=tool_calls,
            usage={
                "prompt_tokens": resp.usage.prompt_tokens if resp.usage else 0,
                "completion_tokens": resp.usage.completion_tokens if resp.usage else 0,
            },
        )

    async def chat_stream(self, messages: list[ChatMessage], **kwargs: Any) -> AsyncIterator[str]:
        stream = await self._client.chat.completions.create(
            model=kwargs.get("model", self._model),
            messages=self._to_api_messages(messages),
            max_tokens=kwargs.get("max_tokens", self._max_tokens),
            temperature=kwargs.get("temperature", self._temperature),
            stream=True,
        )
        async for chunk in stream:
            delta = chunk.choices[0].delta if chunk.choices else None
            if delta and delta.content:
                yield delta.content

    async def close(self) -> None:
        await self._client.close()
