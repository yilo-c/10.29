"""Fallback client full chain tests — deterministic, no real Ollama.

Refactored from ``dispatch_flow_check.py`` (Phase 7.4).  These tests cover
settings parsing, client factories, the fallback chain and runtime snapshots
using mocked settings repositories and HTTP responses.

迁移到 httpx 后，本文件不再 mock ``requests``，而是：
- 对 Kiro 健康探测 mock ``httpx.get``
- 对 OpenAICompatibleClient 的 chat completion mock ``httpx.Client``
"""
from __future__ import annotations

import json
import sys
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch, MagicMock

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

import httpx
import pytest

from services.ai_service import (
    resolve_openai_compatible_config,
    resolve_secondary_openai_compatible_config,
    resolve_local_openai_compatible_config,
    create_fallback_client,
    create_judge_aware_client,
    FallbackOpenAIClient,
)
from ai.types import AIServiceError
from core.settings_schema import KEY_PROVIDER_CONFIGS


class _MockSettingsRepo:
    """In-memory settings repository for deterministic tests."""

    def __init__(self, data: dict | None = None):
        self._data = data or {}

    def get_setting(self, key: str, default=""):
        return self._data.get(key, default)

    def set_setting(self, key: str, value):
        self._data[key] = value


# ───────────────────────────────────────────────────────────────
# HTTP 替身（httpx 风格）
# ───────────────────────────────────────────────────────────────


class _FakeHttpxResponse:
    def __init__(self, status_code: int = 200, json_data: dict | None = None, text: str = ""):
        self.status_code = status_code
        self._json_data = json_data or {}
        self.text = text or json.dumps(self._json_data)
        self.headers: dict[str, str] = {}

    def json(self) -> dict:
        return self._json_data


class _FakeHttpxClient:
    """Minimal httpx.Client 替身，支持同步 post / stream 生命周期。"""

    def __init__(self, post_side_effect=None, **kwargs: object):
        self._post_side_effect = post_side_effect
        self.closed = False

    def post(self, url: str, **kwargs: object) -> _FakeHttpxResponse:
        if self._post_side_effect is not None:
            return self._post_side_effect(url, **kwargs)
        return _FakeHttpxResponse(200, {"choices": [{"message": {"content": "OK"}}]})

    def stream(self, *args: object, **kwargs: object) -> object:
        raise NotImplementedError("stream not mocked in these tests")

    def close(self) -> None:
        self.closed = True

    @property
    def is_closed(self) -> bool:
        return self.closed


def _kiro_down() -> MagicMock:
    """返回 503 的 httpx.get 返回值，用于让 Kiro 自动发现快速失败。"""
    mock = MagicMock()
    mock.status_code = 503
    return mock


# ───────────────────────────────────────────────────────────────
# Fixtures
# ───────────────────────────────────────────────────────────────


@pytest.fixture
def flat_settings() -> _MockSettingsRepo:
    """Legacy flat-key settings matching the original dispatch script."""
    return _MockSettingsRepo({
        "llm_api_base": "http://localhost:11434/v1",
        "llm_api_key": "",
        "llm_model": "qwen2.5:3b",
        "llm_fallback_api_base": "https://api.deepseek.com/v1",
        "llm_fallback_api_key": "sk-fallback",
        "llm_fallback_model": "deepseek-chat",
        "llm_local_api_base": "http://localhost:11434/v1",
        "llm_local_api_key": "",
        "llm_local_model": "qwen3-4b",
    })


@pytest.fixture
def provider_settings() -> _MockSettingsRepo:
    """Provider-config style settings with explicit primary/fallback/local."""
    return _MockSettingsRepo({
        KEY_PROVIDER_CONFIGS: [
            {
                "name": "PrimaryCloud",
                "api_base": "https://primary.example.com/v1",
                "api_key": "sk-primary",
                "model": "primary-model",
                "enabled": True,
            },
            {
                "name": "FallbackCloud",
                "api_base": "https://fallback.example.com/v1",
                "api_key": "sk-fallback",
                "model": "fallback-model",
                "enabled": True,
                "is_fallback": True,
            },
            {
                "name": "LocalOllama",
                "api_base": "http://localhost:11434/v1",
                "api_key": "",
                "model": "qwen3-4b",
                "enabled": True,
                "is_local": True,
            },
        ]
    })


@pytest.fixture
def mock_ollama_response():
    """Standard OpenAI-compatible JSON response used by patched httpx.Client."""
    return {
        "id": "mock-id",
        "object": "chat.completion",
        "choices": [
            {"message": {"role": "assistant", "content": "OK"}, "finish_reason": "stop"}
        ],
        "usage": {"prompt_tokens": 5, "completion_tokens": 1},
    }


@pytest.fixture
def privacy_filter():
    """Shareable-by-default privacy filter to bypass local-only routing."""
    return lambda _: "shareable"


@pytest.fixture
def cache_path(tmp_path):
    """Isolated cache file so tests do not read the real ai_cache.json."""
    return str(tmp_path / "ai_cache.json")


# ───────────────────────────────────────────────────────────────
# Settings config parsing
# ───────────────────────────────────────────────────────────────


class TestResolveOpenAICompatibleConfig:
    """Tests for resolve_*_openai_compatible_config functions."""

    def test_primary_defaults_without_settings(self, monkeypatch):
        # 清空可能污染默认值的环境变量
        for key in ("SIYI_LLM_API_KEY", "SIYI_LLM_BASE_URL", "SIYI_LLM_MODEL",
                    "OPENAI_API_KEY", "OPENAI_BASE_URL", "OPENAI_MODEL",
                    "DEEPSEEK_API_KEY"):
            monkeypatch.delenv(key, raising=False)
        cfg = resolve_openai_compatible_config(environ={})
        assert cfg.base_url == "https://api.deepseek.com/v1"
        assert cfg.model == "deepseek-v4-flash"
        assert cfg.api_key is None
        assert cfg.timeout >= 15

    def test_primary_prefers_environment_variables(self, monkeypatch):
        monkeypatch.setenv("SIYI_LLM_BASE_URL", "https://env.example.com/v1")
        monkeypatch.setenv("SIYI_LLM_API_KEY", "sk-env")
        monkeypatch.setenv("SIYI_LLM_MODEL", "env-model")
        cfg = resolve_openai_compatible_config(environ=None)
        assert cfg.base_url == "https://env.example.com/v1"
        assert cfg.api_key == "sk-env"
        assert cfg.model == "env-model"

    def test_primary_falls_back_to_settings_repository(self, flat_settings):
        cfg = resolve_openai_compatible_config(settings_repository=flat_settings, environ={})
        assert cfg.base_url == "http://localhost:11434/v1"
        assert cfg.model == "qwen2.5:3b"

    def test_primary_env_overrides_settings(self, flat_settings, monkeypatch):
        monkeypatch.setenv("SIYI_LLM_MODEL", "env-override")
        cfg = resolve_openai_compatible_config(settings_repository=flat_settings, environ=None)
        assert cfg.model == "env-override"
        assert cfg.base_url == "http://localhost:11434/v1"  # from settings


class TestResolveSecondaryOpenAICompatibleConfig:
    """Tests for resolve_secondary_openai_compatible_config."""

    def test_secondary_defaults_to_none_without_config(self):
        cfg = resolve_secondary_openai_compatible_config(environ={})
        assert cfg.base_url is None
        assert cfg.model is None
        assert cfg.api_key is None

    def test_secondary_from_environment(self, monkeypatch):
        monkeypatch.setenv("SIYI_LLM_FALLBACK_BASE_URL", "https://fallback-env.example.com/v1")
        monkeypatch.setenv("SIYI_LLM_FALLBACK_API_KEY", "sk-fb-env")
        monkeypatch.setenv("SIYI_LLM_FALLBACK_MODEL", "fb-env-model")
        cfg = resolve_secondary_openai_compatible_config(environ=None)
        assert cfg.base_url == "https://fallback-env.example.com/v1"
        assert cfg.api_key == "sk-fb-env"
        assert cfg.model == "fb-env-model"

    def test_secondary_from_settings(self, flat_settings):
        cfg = resolve_secondary_openai_compatible_config(settings_repository=flat_settings, environ={})
        assert cfg.base_url == "https://api.deepseek.com/v1"
        assert cfg.api_key == "sk-fallback"
        assert cfg.model == "deepseek-chat"


class TestResolveLocalOpenAICompatibleConfig:
    """Tests for resolve_local_openai_compatible_config."""

    def test_local_defaults_to_none_without_config(self):
        cfg = resolve_local_openai_compatible_config(environ={})
        assert cfg.base_url is None
        assert cfg.model is None

    def test_local_from_settings(self, flat_settings):
        cfg = resolve_local_openai_compatible_config(settings_repository=flat_settings, environ={})
        assert cfg.base_url == "http://localhost:11434/v1"
        assert cfg.model == "qwen3-4b"

    def test_local_env_key_optional(self, monkeypatch):
        monkeypatch.setenv("SIYI_LLM_LOCAL_BASE_URL", "http://127.0.0.1:11434/v1")
        monkeypatch.setenv("SIYI_LLM_LOCAL_MODEL", "mistral")
        cfg = resolve_local_openai_compatible_config(environ=None)
        assert cfg.base_url == "http://127.0.0.1:11434/v1"
        assert cfg.model == "mistral"
        assert cfg.api_key is None


# ───────────────────────────────────────────────────────────────
# Client factories
# ───────────────────────────────────────────────────────────────


class TestCreateFallbackClient:
    """Tests for create_fallback_client factory."""

    def test_create_fallback_client_from_provider_configs(self, provider_settings, privacy_filter, cache_path):
        with patch("httpx.get") as mock_get:
            mock_get.return_value.status_code = 503  # Kiro discovery should fail fast
            client = create_fallback_client(
                settings_repository=provider_settings,
                privacy_filter=privacy_filter,
                cache_path=cache_path,
            )
        assert isinstance(client, FallbackOpenAIClient)
        assert client.primary.api_base == "https://primary.example.com/v1"
        assert client.primary.model == "primary-model"
        assert client.fallback is not None
        assert client.fallback.api_base == "https://fallback.example.com/v1"
        assert client.local is not None
        assert client.local.api_base == "http://localhost:11434/v1"

    def test_create_fallback_client_from_flat_keys(self, flat_settings, privacy_filter, cache_path):
        with patch("httpx.get") as mock_get:
            mock_get.return_value.status_code = 503
            client = create_fallback_client(
                settings_repository=flat_settings,
                privacy_filter=privacy_filter,
                cache_path=cache_path,
            )
        assert isinstance(client, FallbackOpenAIClient)
        assert client.primary.api_base == "http://localhost:11434/v1"
        assert client.fallback is not None
        assert "deepseek" in client.fallback.api_base
        assert client.local is not None

    def test_create_fallback_client_without_settings_uses_defaults(self, privacy_filter, cache_path):
        with patch("httpx.get") as mock_get:
            mock_get.return_value.status_code = 503
            client = create_fallback_client(privacy_filter=privacy_filter, cache_path=cache_path)
        assert isinstance(client, FallbackOpenAIClient)
        assert client.primary.api_base == "https://api.deepseek.com/v1"


class TestCreateJudgeAwareClient:
    """Tests for create_judge_aware_client factory."""

    def test_create_judge_aware_client(self, provider_settings, privacy_filter):
        with patch("httpx.get") as mock_get:
            mock_get.return_value.status_code = 503
            jac = create_judge_aware_client(
                settings_repository=provider_settings,
                privacy_filter=privacy_filter,
            )
        assert jac._inner is not None
        assert isinstance(jac._inner, FallbackOpenAIClient)


# ───────────────────────────────────────────────────────────────
# FallbackOpenAIClient.complete_text with mocked HTTP
# ───────────────────────────────────────────────────────────────


class TestFallbackClientCompleteText:
    """FallbackOpenAIClient.complete_text with mocked network responses."""

    def _make_post_side_effect(self, primary_status=200, fallback_status=200, local_status=200):
        def _post(url: str, **kwargs: object) -> _FakeHttpxResponse:
            if "primary" in url:
                if primary_status == 200:
                    return _FakeHttpxResponse(200, {
                        "choices": [{"message": {"content": "primary reply"}}],
                        "usage": {"prompt_tokens": 1, "completion_tokens": 1},
                    })
                return _FakeHttpxResponse(primary_status, {"error": {"message": "primary error"}})
            elif "fallback" in url:
                if fallback_status == 200:
                    return _FakeHttpxResponse(200, {
                        "choices": [{"message": {"content": "fallback reply"}}],
                        "usage": {"prompt_tokens": 1, "completion_tokens": 1},
                    })
                return _FakeHttpxResponse(fallback_status, {"error": {"message": "fallback error"}})
            elif "localhost" in url or "127.0.0.1" in url:
                if local_status == 200:
                    return _FakeHttpxResponse(200, {
                        "choices": [{"message": {"content": "local reply"}}],
                        "usage": {"prompt_tokens": 1, "completion_tokens": 1},
                    })
                return _FakeHttpxResponse(local_status, {"error": {"message": "local error"}})
            else:
                return _FakeHttpxResponse(200, {"choices": [{"message": {"content": "generic reply"}}]})
        return _post

    def _mocked_client(self, post_side_effect=None) -> ExitStack:
        """让 httpx.Client 构造返回我们的 fake client，httpx.get 返回 503。"""
        stack = ExitStack()
        stack.enter_context(patch("httpx.get", return_value=_kiro_down()))
        stack.enter_context(patch(
            "httpx.Client",
            side_effect=lambda *args, **kwargs: _FakeHttpxClient(post_side_effect=post_side_effect),
        ))
        return stack

    def test_complete_text_primary_success(self, provider_settings, privacy_filter, cache_path):
        post_effect = self._make_post_side_effect(primary_status=200)
        with self._mocked_client(post_effect):
            client = create_fallback_client(
                settings_repository=provider_settings,
                privacy_filter=privacy_filter,
                cache_path=cache_path,
            )
            reply = client.complete_text([{"role": "user", "content": "hello"}])
        assert reply == "primary reply"

    def test_complete_text_falls_back_when_primary_fails(self, provider_settings, privacy_filter, cache_path):
        post_effect = self._make_post_side_effect(primary_status=400, fallback_status=200)
        with self._mocked_client(post_effect):
            client = create_fallback_client(
                settings_repository=provider_settings,
                privacy_filter=privacy_filter,
                cache_path=cache_path,
            )
            reply = client.complete_text([{"role": "user", "content": "hello"}])
        assert reply == "fallback reply"

    def test_complete_text_falls_back_to_local(self, provider_settings, privacy_filter, cache_path):
        post_effect = self._make_post_side_effect(primary_status=400, fallback_status=400, local_status=200)
        with self._mocked_client(post_effect):
            client = create_fallback_client(
                settings_repository=provider_settings,
                privacy_filter=privacy_filter,
                cache_path=cache_path,
            )
            reply = client.complete_text([{"role": "user", "content": "hello"}])
        assert reply == "local reply"

    def test_complete_text_all_fail_raises(self, provider_settings, privacy_filter, cache_path):
        post_effect = self._make_post_side_effect(primary_status=400, fallback_status=400, local_status=400)
        with self._mocked_client(post_effect):
            client = create_fallback_client(
                settings_repository=provider_settings,
                privacy_filter=privacy_filter,
                cache_path=cache_path,
            )
            with pytest.raises(AIServiceError):
                client.complete_text([{"role": "user", "content": "hello"}])


# ───────────────────────────────────────────────────────────────
# Runtime snapshot and chain behavior
# ───────────────────────────────────────────────────────────────


class TestRuntimeSnapshot:
    """Tests for get_runtime_snapshot and fallback chain metadata."""

    def test_runtime_snapshot_structure(self, provider_settings, privacy_filter, cache_path):
        with patch("httpx.get") as mock_get:
            mock_get.return_value.status_code = 503
            client = create_fallback_client(
                settings_repository=provider_settings,
                privacy_filter=privacy_filter,
                cache_path=cache_path,
            )
        snap = client.get_runtime_snapshot()
        assert "last_stage" in snap
        assert "chain" in snap
        stages = [entry["stage"] for entry in snap["chain"]]
        assert "primary" in stages
        assert "fallback" in stages
        assert "local" in stages
        assert "cache" in stages

    def test_runtime_snapshot_updated_after_chat(self, provider_settings, privacy_filter, cache_path):
        post_effect = TestFallbackClientCompleteText()._make_post_side_effect(primary_status=200)
        with TestFallbackClientCompleteText()._mocked_client(post_effect):
            client = create_fallback_client(
                settings_repository=provider_settings,
                privacy_filter=privacy_filter,
                cache_path=cache_path,
            )
            client.complete_text([{"role": "user", "content": "hello"}])
        snap = client.get_runtime_snapshot()
        assert snap["last_stage"] == "primary"
        assert snap["degraded"] is False
        assert snap["cache_hit"] is False


# ───────────────────────────────────────────────────────────────
# Optional integration tests
# ───────────────────────────────────────────────────────────────


@pytest.mark.skip(reason="integration test: requires real Ollama")
def test_complete_text_against_real_ollama():
    """Optional integration test preserved from dispatch_flow_check.py."""
    settings = _MockSettingsRepo({
        "llm_api_base": "http://localhost:11434/v1",
        "llm_api_key": "",
        "llm_model": "qwen2.5:3b",
    })
    client = create_fallback_client(settings_repository=settings)
    reply = client.complete_text([{"role": "user", "content": "回复OK即可"}], max_tokens=50)
    assert reply
