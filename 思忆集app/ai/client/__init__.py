"""AI 客户端封装层 — OpenAI 兼容 API 客户端、回退链、配置工厂。"""
from __future__ import annotations

from ai.client.base import OpenAICompatibleClient, logger as _base_logger
from ai.client.fallback import FallbackOpenAIClient, logger as _fallback_logger
from ai.client._judge_aware_client import JudgeAwareClient
from ai.client.config import (
    create_fallback_client,
    create_judge_aware_client,
    create_openai_compatible_client,
    resolve_local_openai_compatible_config,
    resolve_openai_compatible_config,
    resolve_secondary_openai_compatible_config,
    logger as _config_logger,
)

__all__ = [
    "OpenAICompatibleClient",
    "FallbackOpenAIClient",
    "JudgeAwareClient",
    "create_fallback_client",
    "create_judge_aware_client",
    "create_openai_compatible_client",
    "resolve_local_openai_compatible_config",
    "resolve_openai_compatible_config",
    "resolve_secondary_openai_compatible_config",
]
