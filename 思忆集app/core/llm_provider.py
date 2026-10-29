"""LLM Provider Protocol — 思忆集统一 LLM 提供商协议。

设计参考：
- nanobot 的 Provider 抽象（按 provider_id 切换）
- openclaw 的 Tool Use 注册表（schema 校验在执行前）

用途：
    所有 LLM 调用统一经过此协议，支持按场景选择模型/Provider、
    输出 schema 校验、系统提示与用户输入隔离。

使用方式：
    from core.llm_provider import LLMProvider, LLMRequest, LLMResponse

    class OpenAIProvider(LLMProvider):
        def complete(self, request: LLMRequest) -> LLMResponse: ...
        def stream(self, request: LLMRequest) -> Iterator[str]: ...
"""
from __future__ import annotations

import json
import logging
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

logger = logging.getLogger("siyi.llm_provider")


@dataclass(frozen=True)
class LLMRequest:
    """不可变的 LLM 请求，强制分离系统提示和用户输入。"""
    system_prompt: str
    user_message: str
    temperature: float = 0.2
    max_tokens: int = 900
    context_messages: list[dict[str, str]] = field(default_factory=list)
    output_schema: dict[str, Any] | None = None  # JSON Schema for structured output


@dataclass(frozen=True)
class LLMResponse:
    """不可变的 LLM 响应。"""
    content: str
    model: str = ""
    provider_id: str = ""
    usage: dict[str, int] = field(default_factory=dict)
    raw: dict[str, Any] = field(default_factory=dict)


class LLMResponseValidationError(ValueError):
    """LLM 输出未通过 schema 校验时抛出。"""
    pass


@runtime_checkable
class LLMProvider(Protocol):
    """LLM Provider 协议——所有 LLM 提供商必须实现。"""

    @property
    def provider_id(self) -> str: ...

    @property
    def is_available(self) -> bool: ...

    def complete(self, request: LLMRequest) -> LLMResponse: ...

    def stream(self, request: LLMRequest) -> Iterator[str]: ...


def validate_llm_output(content: str, schema: dict[str, Any] | None) -> dict[str, Any]:
    """校验 LLM 输出是否符合预期 JSON Schema（简化版）。

    如果 schema 为 None，直接返回解析后的 dict。
    如果 content 不是合法 JSON 或缺少必需字段，抛出 LLMResponseValidationError。
    """
    if schema is None:
        return {"content": content}
    try:
        parsed = json.loads(content)
    except (json.JSONDecodeError, TypeError) as exc:
        raise LLMResponseValidationError(f"LLM 输出不是合法 JSON: {exc}") from exc
    if not isinstance(parsed, dict):
        raise LLMResponseValidationError(f"LLM 输出应为 dict，实际为 {type(parsed).__name__}")
    required_fields = schema.get("required", [])
    for field_name in required_fields:
        if field_name not in parsed:
            raise LLMResponseValidationError(f"LLM 输出缺少必需字段: {field_name}")
    return parsed


def build_messages(request: LLMRequest) -> list[dict[str, str]]:
    """将 LLMRequest 构建为 OpenAI 格式 messages 列表。

    系统提示和用户输入始终分开，防止提示词注入。
    """
    messages: list[dict[str, str]] = []
    if request.system_prompt:
        messages.append({"role": "system", "content": request.system_prompt})
    messages.extend(request.context_messages)
    messages.append({"role": "user", "content": request.user_message})
    return messages
