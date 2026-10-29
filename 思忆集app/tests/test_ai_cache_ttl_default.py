"""P2-16 回归：AiResponseCache 默认 TTL 从 24h 调短到 1h，可由环境变量覆盖。

之前默认 ``cache_ttl_seconds=86400``，主备全部不可用时用户会在 24 小时内
反复拿到一份"陈旧"答案。调整为 ``SIYI_AI_CACHE_TTL=3600``，并允许通过
环境变量按部署场景覆盖。
"""
from __future__ import annotations

import pytest

from ai.client import fallback as _fallback_mod
from ai.client.fallback import (
    FallbackOpenAIClient,
    _resolve_default_cache_ttl,
    _resolve_default_cache_max_entries,
)


class _Stub:
    """最小可用 OpenAICompatibleClient 替身。"""

    def __init__(self, model: str = "stub") -> None:
        self.model = model
        self.api_key = ""
        self.base_url = ""
        self.api_base = ""
        self.timeout = 30

    def is_configured(self) -> bool:
        return False


def test_default_ttl_is_one_hour(monkeypatch) -> None:
    monkeypatch.delenv("SIYI_AI_CACHE_TTL", raising=False)
    assert _resolve_default_cache_ttl() == 3600


def test_env_override_ttl(monkeypatch) -> None:
    monkeypatch.setenv("SIYI_AI_CACHE_TTL", "120")
    assert _resolve_default_cache_ttl() == 120


def test_env_override_invalid_ttl_falls_back(monkeypatch) -> None:
    monkeypatch.setenv("SIYI_AI_CACHE_TTL", "not-a-number")
    assert _resolve_default_cache_ttl() == 3600


def test_env_zero_ttl_disables(monkeypatch) -> None:
    monkeypatch.setenv("SIYI_AI_CACHE_TTL", "0")
    assert _resolve_default_cache_ttl() == 0


def test_env_negative_ttl_clamped_to_zero(monkeypatch) -> None:
    monkeypatch.setenv("SIYI_AI_CACHE_TTL", "-50")
    assert _resolve_default_cache_ttl() == 0


def test_default_max_entries(monkeypatch) -> None:
    monkeypatch.delenv("SIYI_AI_CACHE_MAX", raising=False)
    assert _resolve_default_cache_max_entries() == 500


def test_env_override_max_entries(monkeypatch) -> None:
    monkeypatch.setenv("SIYI_AI_CACHE_MAX", "42")
    assert _resolve_default_cache_max_entries() == 42


def test_fallback_client_uses_one_hour_ttl_by_default(
    monkeypatch, tmp_path
) -> None:
    monkeypatch.delenv("SIYI_AI_CACHE_TTL", raising=False)
    monkeypatch.delenv("SIYI_AI_CACHE_MAX", raising=False)
    fb = FallbackOpenAIClient(
        primary=_Stub(),
        cache_path=str(tmp_path / "ai_cache.json"),
    )
    assert fb.cache_ttl_seconds == 3600
    assert fb.cache_max_entries == 500


def test_explicit_arg_still_wins(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("SIYI_AI_CACHE_TTL", "60")
    fb = FallbackOpenAIClient(
        primary=_Stub(),
        cache_ttl_seconds=7200,
        cache_max_entries=999,
        cache_path=str(tmp_path / "ai_cache.json"),
    )
    # 显式参数优先级高于 env
    assert fb.cache_ttl_seconds == 7200
    assert fb.cache_max_entries == 999


def test_env_override_threads_into_fallback(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("SIYI_AI_CACHE_TTL", "300")
    monkeypatch.setenv("SIYI_AI_CACHE_MAX", "10")
    fb = FallbackOpenAIClient(
        primary=_Stub(),
        cache_path=str(tmp_path / "ai_cache.json"),
    )
    assert fb.cache_ttl_seconds == 300
    assert fb.cache_max_entries == 10
    # AiResponseCache 也拿到了
    assert fb._cache_obj.cache_ttl_seconds == 300
