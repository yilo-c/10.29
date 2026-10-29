from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from urllib.parse import urlparse

from services.ai_service import (
    resolve_local_openai_compatible_config,
    resolve_openai_compatible_config,
    resolve_secondary_openai_compatible_config,
)


@dataclass(frozen=True)
class LLMProviderStage:
    stage_id: str
    display_name: str
    base_url: str | None
    model: str | None
    configured: bool
    has_api_key: bool
    provider_label: str
    source: str


def _resolve_source(*, env_key: str | None, env_base: str | None, env_model: str | None, local_key: bool, local_base: bool, local_model: bool) -> str:
    if env_key or env_base or env_model:
        if local_key or local_base or local_model:
            return "环境变量优先，本地设置存在回退值"
        return "环境变量"
    if local_key or local_base or local_model:
        return "本地 settings.json"
    return "未配置"


def _derive_provider_label(base_url: str | None) -> str:
    if not base_url:
        return "未配置"
    parsed = urlparse(str(base_url).strip())
    host = (parsed.hostname or "").lower()
    path = (parsed.path or "").lower()
    text = f"{host}{path}"
    if "openrouter" in text:
        return "OpenRouter"
    if "openai" in text:
        return "OpenAI Compatible"
    if "anthropic" in text:
        return "Anthropic Gateway"
    if "azure" in text:
        return "Azure OpenAI"
    if host in {"127.0.0.1", "localhost"}:
        if "ollama" in text:
            return "Ollama"
        return "本地 OpenAI 兼容服务"
    return host or "自定义 Provider"


def build_llm_provider_topology(settings_repository=None, environ=None) -> list[LLMProviderStage]:
    environ = environ or {}
    primary = resolve_openai_compatible_config(settings_repository=settings_repository, environ=environ)
    secondary = resolve_secondary_openai_compatible_config(settings_repository=settings_repository, environ=environ)
    local = resolve_local_openai_compatible_config(settings_repository=settings_repository, environ=environ)

    def _local_has(key: str, default: str = "") -> bool:
        return bool(settings_repository is not None and str(settings_repository.get_setting(key, default) or "").strip())

    return [
        LLMProviderStage(
            stage_id="primary",
            display_name="主 Provider",
            base_url=primary.base_url,
            model=primary.model,
            configured=primary.is_configured,
            has_api_key=bool(primary.api_key),
            provider_label=_derive_provider_label(primary.base_url),
            source=_resolve_source(
                env_key=environ.get("SIYI_LLM_API_KEY") or environ.get("OPENAI_API_KEY"),
                env_base=environ.get("SIYI_LLM_BASE_URL") or environ.get("OPENAI_BASE_URL"),
                env_model=environ.get("SIYI_LLM_MODEL") or environ.get("OPENAI_MODEL"),
                local_key=_local_has("llm_api_key"),
                local_base=_local_has("llm_api_base"),
                local_model=_local_has("llm_model"),
            ),
        ),
        LLMProviderStage(
            stage_id="fallback",
            display_name="备用 Provider",
            base_url=secondary.base_url,
            model=secondary.model,
            configured=secondary.is_configured,
            has_api_key=bool(secondary.api_key),
            provider_label=_derive_provider_label(secondary.base_url),
            source=_resolve_source(
                env_key=environ.get("SIYI_LLM_FALLBACK_API_KEY") or environ.get("OPENAI_FALLBACK_API_KEY"),
                env_base=environ.get("SIYI_LLM_FALLBACK_BASE_URL") or environ.get("OPENAI_FALLBACK_BASE_URL"),
                env_model=environ.get("SIYI_LLM_FALLBACK_MODEL") or environ.get("OPENAI_FALLBACK_MODEL"),
                local_key=_local_has("llm_fallback_api_key"),
                local_base=_local_has("llm_fallback_api_base"),
                local_model=_local_has("llm_fallback_model"),
            ),
        ),
        LLMProviderStage(
            stage_id="local",
            display_name="本地回退 Provider",
            base_url=local.base_url,
            model=local.model,
            configured=local.is_configured,
            has_api_key=bool(local.api_key),
            provider_label=_derive_provider_label(local.base_url),
            source=_resolve_source(
                env_key=environ.get("SIYI_LLM_LOCAL_API_KEY") or environ.get("OPENAI_LOCAL_API_KEY"),
                env_base=environ.get("SIYI_LLM_LOCAL_BASE_URL") or environ.get("OPENAI_LOCAL_BASE_URL"),
                env_model=environ.get("SIYI_LLM_LOCAL_MODEL") or environ.get("OPENAI_LOCAL_MODEL"),
                local_key=_local_has("llm_local_api_key"),
                local_base=_local_has("llm_local_api_base"),
                local_model=_local_has("llm_local_model"),
            ),
        ),
    ]


def summarize_llm_provider_topology(stages: Iterable[LLMProviderStage]) -> str:
    lines: list[str] = []
    for stage in stages:
        state = "已配置" if stage.configured else "未配置"
        lines.append(
            f"{stage.display_name}: {state} | Provider={stage.provider_label} | Model={stage.model or '未配置'} | 来源={stage.source}"
        )
    return "\n".join(lines)
