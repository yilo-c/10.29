"""LLM provider topology tests — deterministic with mock settings/env.

Refactored from ``dispatch_flow_check.py`` (Phase 7.4).  These tests cover
``build_llm_provider_topology`` and ``summarize_llm_provider_topology`` without
requiring real API keys or a running Ollama.
"""
from __future__ import annotations

import sys
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

import pytest

from ai.llm_provider_topology import (
    build_llm_provider_topology,
    summarize_llm_provider_topology,
    LLMProviderStage,
)


class _MockSettingsRepo:
    """In-memory settings repository for deterministic tests."""

    def __init__(self, data: dict | None = None):
        self._data = data or {}

    def get_setting(self, key: str, default=""):
        return self._data.get(key, default)


# ───────────────────────────────────────────────────────────────
# build_llm_provider_topology
# ───────────────────────────────────────────────────────────────


class TestBuildLLMProviderTopology:
    """Tests for build_llm_provider_topology."""

    def test_returns_three_stages(self):
        stages = build_llm_provider_topology(environ={})
        assert len(stages) == 3
        for stage in stages:
            assert isinstance(stage, LLMProviderStage)

    def test_stage_ids_are_primary_fallback_local(self):
        stages = build_llm_provider_topology(environ={})
        ids = [s.stage_id for s in stages]
        assert ids == ["primary", "fallback", "local"]

    def test_default_primary_from_hardcoded_defaults(self):
        stages = build_llm_provider_topology(environ={})
        primary = stages[0]
        assert primary.stage_id == "primary"
        assert primary.base_url == "https://api.deepseek.com/v1"
        # 2026-07 起 deepseek-chat 已停用，默认模型迁移到 deepseek-v4-flash
        assert primary.model == "deepseek-v4-flash"
        assert primary.configured is False  # no api key
        assert primary.has_api_key is False
        # DeepSeek base URL does not match any preset provider label marker,
        # so it falls back to the hostname.
        assert primary.provider_label == "api.deepseek.com"

    def test_fallback_defaults_unconfigured(self):
        stages = build_llm_provider_topology(environ={})
        fallback = stages[1]
        assert fallback.stage_id == "fallback"
        assert fallback.base_url is None
        assert fallback.model is None
        assert fallback.configured is False
        assert fallback.provider_label == "未配置"

    def test_local_defaults_unconfigured(self):
        stages = build_llm_provider_topology(environ={})
        local = stages[2]
        assert local.stage_id == "local"
        assert local.base_url is None
        assert local.model is None
        assert local.configured is False

    def test_primary_from_environment_variables(self):
        stages = build_llm_provider_topology(environ={
            "SIYI_LLM_BASE_URL": "https://env.example.com/v1",
            "SIYI_LLM_API_KEY": "sk-env",
            "SIYI_LLM_MODEL": "env-model",
        })
        primary = stages[0]
        assert primary.base_url == "https://env.example.com/v1"
        assert primary.model == "env-model"
        assert primary.has_api_key is True
        assert primary.configured is True
        assert primary.source == "环境变量"

    def test_local_from_environment(self):
        stages = build_llm_provider_topology(environ={
            "SIYI_LLM_LOCAL_BASE_URL": "http://127.0.0.1:11434/v1",
            "SIYI_LLM_LOCAL_MODEL": "qwen3-4b",
        })
        local = stages[2]
        assert local.base_url == "http://127.0.0.1:11434/v1"
        assert local.model == "qwen3-4b"
        assert local.provider_label == "本地 OpenAI 兼容服务"

    def test_settings_repository_used_when_no_env(self):
        settings = _MockSettingsRepo({
            "llm_api_base": "https://settings.example.com/v1",
            "llm_api_key": "sk-settings",
            "llm_model": "settings-model",
            "llm_fallback_api_base": "https://fallback.example.com/v1",
            "llm_fallback_api_key": "sk-fallback",
            "llm_fallback_model": "fallback-model",
            "llm_local_api_base": "http://localhost:11434/v1",
            "llm_local_model": "qwen3-4b",
        })
        stages = build_llm_provider_topology(settings_repository=settings, environ={})
        primary, fallback, local = stages
        assert primary.base_url == "https://settings.example.com/v1"
        assert primary.model == "settings-model"
        assert primary.has_api_key is True
        assert fallback.base_url == "https://fallback.example.com/v1"
        assert fallback.model == "fallback-model"
        assert local.base_url == "http://localhost:11434/v1"
        assert local.model == "qwen3-4b"

    def test_env_takes_precedence_over_settings(self):
        settings = _MockSettingsRepo({
            "llm_api_base": "https://settings.example.com/v1",
            "llm_api_key": "sk-settings",
            "llm_model": "settings-model",
        })
        stages = build_llm_provider_topology(
            settings_repository=settings,
            environ={"SIYI_LLM_MODEL": "env-override"},
        )
        primary = stages[0]
        assert primary.model == "env-override"
        assert primary.base_url == "https://settings.example.com/v1"
        assert "环境变量优先" in primary.source

    def test_ollama_provider_label(self):
        settings = _MockSettingsRepo({
            "llm_local_api_base": "http://localhost:11434/v1",
            "llm_local_model": "qwen3-4b",
        })
        stages = build_llm_provider_topology(settings_repository=settings, environ={})
        local = stages[2]
        # The provider label is based on host/path text; localhost alone maps to
        # "本地 OpenAI 兼容服务" unless "ollama" appears in the URL text.
        assert local.provider_label == "本地 OpenAI 兼容服务"

    def test_openrouter_provider_label(self):
        settings = _MockSettingsRepo({
            "llm_api_base": "https://openrouter.ai/api/v1",
            "llm_api_key": "sk-or",
            "llm_model": "openrouter-model",
        })
        stages = build_llm_provider_topology(settings_repository=settings, environ={})
        assert stages[0].provider_label == "OpenRouter"


# ───────────────────────────────────────────────────────────────
# summarize_llm_provider_topology
# ───────────────────────────────────────────────────────────────


class TestSummarizeLLMProviderTopology:
    """Tests for summarize_llm_provider_topology."""

    def test_summary_contains_all_stages(self):
        stages = build_llm_provider_topology(environ={})
        summary = summarize_llm_provider_topology(stages)
        assert "主 Provider" in summary
        assert "备用 Provider" in summary
        assert "本地回退 Provider" in summary

    def test_summary_shows_configured_state(self):
        settings = _MockSettingsRepo({
            "llm_api_base": "https://settings.example.com/v1",
            "llm_api_key": "sk-settings",
            "llm_model": "settings-model",
        })
        stages = build_llm_provider_topology(settings_repository=settings, environ={})
        summary = summarize_llm_provider_topology(stages)
        assert "已配置" in summary
        assert "未配置" in summary

    def test_summary_is_non_empty_string(self):
        stages = build_llm_provider_topology(environ={})
        summary = summarize_llm_provider_topology(stages)
        assert isinstance(summary, str)
        assert summary.strip() != ""


# ───────────────────────────────────────────────────────────────
# Optional integration tests
# ───────────────────────────────────────────────────────────────


@pytest.mark.skip(reason="integration test: reads real environment/settings")
def test_build_topology_from_real_environment():
    """Optional integration test preserved from dispatch_flow_check.py."""
    import os
    stages = build_llm_provider_topology(environ=os.environ)
    assert len(stages) == 3
    summary = summarize_llm_provider_topology(stages)
    assert len(summary) > 0
