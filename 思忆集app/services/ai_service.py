"""AI 服务 — 统一导出入口。

保持向后兼容：所有旧的 from ai_service import ... 继续可用。
职责已拆分到以下子模块：
- ai_config:          配置解析 + 工厂函数
- ai_client_base:     OpenAICompatibleClient + JSON/文本辅助函数
- ai_client_fallback: FallbackOpenAIClient + JudgeAwareClient
- ai_provider_routing: ProviderRoutingTable + _PROVIDER_ROUTING_PRESETS
"""
from __future__ import annotations

from ai.client.config import (
    create_fallback_client,
    create_judge_aware_client,
    create_openai_compatible_client,
    resolve_local_openai_compatible_config,
    resolve_openai_compatible_config,
    resolve_secondary_openai_compatible_config,
)
from ai.client.base import (
    OpenAICompatibleClient,
    _normalize_text,
    _normalize_text_list,
    _parse_json_object,
)
from ai.client.fallback import FallbackOpenAIClient
from ai.client import JudgeAwareClient
from ai.provider_routing import _PROVIDER_ROUTING_PRESETS, ProviderRoutingTable
from ai.types import AIServiceError

__all__ = [
    "AIServiceError",
    "create_fallback_client",
    "create_judge_aware_client",
    "create_openai_compatible_client",
    "FallbackOpenAIClient",
    "JudgeAwareClient",
    "OpenAICompatibleClient",
    "ProviderRoutingTable",
    "resolve_local_openai_compatible_config",
    "resolve_openai_compatible_config",
    "resolve_secondary_openai_compatible_config",
    "_PROVIDER_ROUTING_PRESETS",
    "_normalize_text",
    "_normalize_text_list",
    "_parse_json_object",
]
