"""P2-5 批次 C：ai/client/config.py 测试。

覆盖范围：
* ``_LayerSpec`` + ``_resolve_layer``：env / settings / default 三段式优先级
* ``warn_legacy``：仅主层在「settings 提供 key + env 缺失」时记录 WARNING
* ``timeout`` clamping（最低 15s）
* 4 个 ``resolve_*`` 公共函数仍然遵守历史契约
* ``ProviderLike`` Protocol 的 ``runtime_checkable`` isinstance 校验
* ``create_client_from_provider_config``：model fallback 链 + 接受任意 duck type
"""
from __future__ import annotations

import logging
from typing import Any, Iterable
from unittest.mock import MagicMock

import pytest

from ai.client.config import (
    ProviderLike,
    _LayerSpec,
    _PRIMARY_SPEC,
    _SECONDARY_SPEC,
    _LOCAL_SPEC,
    _LOCAL_CODER_SPEC,
    _resolve_layer,
    create_client_from_provider_config,
    resolve_local_coder_openai_compatible_config,
    resolve_local_openai_compatible_config,
    resolve_openai_compatible_config,
    resolve_secondary_openai_compatible_config,
)
from ai.types import OpenAICompatibleConfig
from core.settings_schema import (
    KEY_LLM_API_BASE,
    KEY_LLM_API_KEY,
    KEY_LLM_MODEL,
    KEY_LLM_TIMEOUT,
)


# ─── helpers ────────────────────────────────────────────────────────────────


class _FakeSettings:
    """最小 settings_repository stub：只实现 ``get_setting``。"""

    def __init__(self, mapping: dict[str, Any] | None = None) -> None:
        self._mapping = mapping or {}

    def get_setting(self, key: str, default: Any = None) -> Any:
        if key in self._mapping:
            return self._mapping[key]
        return default


# ─── _resolve_layer 优先级 ──────────────────────────────────────────────────


class TestResolveLayerPrecedence:
    def test_env_beats_settings_beats_default(self) -> None:
        spec = _PRIMARY_SPEC
        environ = {
            "SIYI_LLM_API_KEY": "env-key",
            "SIYI_LLM_BASE_URL": "https://env.example/v1",
            "SIYI_LLM_MODEL": "env-model",
        }
        settings = _FakeSettings({
            KEY_LLM_API_KEY: "settings-key",
            KEY_LLM_API_BASE: "https://settings.example",
            KEY_LLM_MODEL: "settings-model",
        })

        cfg = _resolve_layer(spec, settings_repository=settings, environ=environ)

        assert cfg.api_key == "env-key"
        assert cfg.base_url == "https://env.example/v1"
        assert cfg.model == "env-model"

    def test_settings_fills_when_env_missing(self) -> None:
        spec = _PRIMARY_SPEC
        environ: dict[str, str] = {}
        settings = _FakeSettings({
            KEY_LLM_API_KEY: "settings-key",
            KEY_LLM_API_BASE: "https://settings.example",
            KEY_LLM_MODEL: "settings-model",
        })

        cfg = _resolve_layer(spec, settings_repository=settings, environ=environ)

        assert cfg.api_key == "settings-key"
        # 注意：rstrip("/") 处理过
        assert cfg.base_url == "https://settings.example"
        assert cfg.model == "settings-model"

    def test_defaults_when_neither_env_nor_settings(self) -> None:
        cfg = _resolve_layer(_PRIMARY_SPEC, settings_repository=None, environ={})
        # 主层有 default_base_url / default_model
        assert cfg.api_key is None
        assert cfg.base_url == "https://api.deepseek.com/v1"
        assert cfg.model == "deepseek-v4-flash"
        assert cfg.timeout == 300

    def test_no_settings_no_env_secondary_returns_none_base_url(self) -> None:
        cfg = _resolve_layer(_SECONDARY_SPEC, settings_repository=None, environ={})
        # secondary / local / local_coder 的 default_base_url 都是 None
        assert cfg.api_key is None
        assert cfg.base_url is None
        assert cfg.model is None
        assert cfg.timeout == 60

    def test_blank_string_env_falls_through_to_settings(self) -> None:
        """env var 值为空字符串时视为未设置。"""
        environ = {"SIYI_LLM_API_KEY": ""}
        settings = _FakeSettings({KEY_LLM_API_KEY: "settings-key"})

        cfg = _resolve_layer(_PRIMARY_SPEC, settings_repository=settings, environ=environ)

        assert cfg.api_key == "settings-key"

    def test_whitespace_only_normalized_to_none(self) -> None:
        environ = {"SIYI_LLM_API_KEY": "   "}
        cfg = _resolve_layer(_PRIMARY_SPEC, settings_repository=None, environ=environ)
        # "   " 经 strip() 后变空 → api_key 归一化为 None
        assert cfg.api_key is None

    def test_secondary_env_keys_distinct_from_primary(self) -> None:
        """secondary 不应该被 SIYI_LLM_API_KEY（主层）覆盖。"""
        environ = {
            "SIYI_LLM_API_KEY": "PRIMARY",
            "SIYI_LLM_FALLBACK_API_KEY": "SECONDARY",
        }
        primary = _resolve_layer(_PRIMARY_SPEC, environ=environ)
        secondary = _resolve_layer(_SECONDARY_SPEC, environ=environ)

        assert primary.api_key == "PRIMARY"
        assert secondary.api_key == "SECONDARY"

    def test_alternate_env_name_recognized(self) -> None:
        """OPENAI_API_KEY 是 SIYI_LLM_API_KEY 的备选名。"""
        cfg = _resolve_layer(
            _PRIMARY_SPEC,
            environ={"OPENAI_API_KEY": "alt-key"},
        )
        assert cfg.api_key == "alt-key"

    def test_first_env_var_wins(self) -> None:
        environ = {
            "SIYI_LLM_API_KEY": "siyi",
            "OPENAI_API_KEY": "openai",
        }
        cfg = _resolve_layer(_PRIMARY_SPEC, environ=environ)
        # tuple 第一个非空命中
        assert cfg.api_key == "siyi"


# ─── timeout 处理 ───────────────────────────────────────────────────────────


class TestTimeout:
    def test_timeout_from_env(self) -> None:
        cfg = _resolve_layer(_PRIMARY_SPEC, environ={"SIYI_LLM_TIMEOUT": "120"})
        assert cfg.timeout == 120

    def test_timeout_below_15_clamped_to_15(self) -> None:
        cfg = _resolve_layer(_PRIMARY_SPEC, environ={"SIYI_LLM_TIMEOUT": "5"})
        assert cfg.timeout == 15

    def test_timeout_invalid_falls_back_to_default(self) -> None:
        cfg = _resolve_layer(_PRIMARY_SPEC, environ={"SIYI_LLM_TIMEOUT": "abc"})
        assert cfg.timeout == 300  # primary default

    def test_secondary_timeout_default_is_60(self) -> None:
        cfg = _resolve_layer(_SECONDARY_SPEC, environ={})
        assert cfg.timeout == 60

    def test_settings_timeout_used_when_env_missing(self) -> None:
        settings = _FakeSettings({KEY_LLM_TIMEOUT: 90})
        cfg = _resolve_layer(_PRIMARY_SPEC, settings_repository=settings, environ={})
        assert cfg.timeout == 90


# ─── warn_legacy ────────────────────────────────────────────────────────────


class TestWarnLegacy:
    def test_primary_warns_when_settings_used_without_env(self, caplog: pytest.LogCaptureFixture) -> None:
        settings = _FakeSettings({KEY_LLM_API_KEY: "settings-key"})
        with caplog.at_level(logging.WARNING, logger="siyi.ai.config"):
            _resolve_layer(_PRIMARY_SPEC, settings_repository=settings, environ={})
        assert any("扁平 LLM" in r.message for r in caplog.records)

    def test_primary_no_warn_when_env_supplies_key(self, caplog: pytest.LogCaptureFixture) -> None:
        environ = {"SIYI_LLM_API_KEY": "env-key"}
        settings = _FakeSettings({KEY_LLM_API_KEY: "settings-key"})
        with caplog.at_level(logging.WARNING, logger="siyi.ai.config"):
            _resolve_layer(_PRIMARY_SPEC, settings_repository=settings, environ=environ)
        assert not any("扁平 LLM" in r.message for r in caplog.records)

    def test_secondary_never_warns_even_via_settings(self, caplog: pytest.LogCaptureFixture) -> None:
        settings = _FakeSettings({"llm_fallback_api_key": "settings-key"})
        with caplog.at_level(logging.WARNING, logger="siyi.ai.config"):
            _resolve_layer(_SECONDARY_SPEC, settings_repository=settings, environ={})
        assert not any("扁平 LLM" in r.message for r in caplog.records)


# ─── 4 个公共函数仍走 _resolve_layer ─────────────────────────────────────────


class TestPublicResolversBackcompat:
    def test_primary_wrapper_matches_resolve_layer(self) -> None:
        environ = {"SIYI_LLM_API_KEY": "k"}
        a = resolve_openai_compatible_config(environ=environ)
        b = _resolve_layer(_PRIMARY_SPEC, environ=environ)
        assert a == b

    def test_secondary_wrapper_uses_secondary_spec(self) -> None:
        environ = {"SIYI_LLM_FALLBACK_API_KEY": "k", "SIYI_LLM_FALLBACK_MODEL": "m"}
        cfg = resolve_secondary_openai_compatible_config(environ=environ)
        assert cfg.api_key == "k"
        assert cfg.model == "m"
        assert cfg.base_url is None  # secondary 无 default

    def test_local_wrapper_uses_local_spec(self) -> None:
        environ = {"SIYI_LLM_LOCAL_API_KEY": "k", "SIYI_LLM_LOCAL_BASE_URL": "http://localhost:11434/v1"}
        cfg = resolve_local_openai_compatible_config(environ=environ)
        assert cfg.api_key == "k"
        assert cfg.base_url == "http://localhost:11434/v1"

    def test_local_coder_wrapper_uses_local_coder_spec(self) -> None:
        environ = {"SIYI_LLM_LOCAL_CODER_MODEL": "qwen2.5-coder"}
        cfg = resolve_local_coder_openai_compatible_config(environ=environ)
        assert cfg.model == "qwen2.5-coder"


# ─── ProviderLike Protocol ──────────────────────────────────────────────────


class TestProviderLikeProtocol:
    def test_provider_config_satisfies_protocol(self) -> None:
        """core.provider_config.ProviderConfig 必须能被 ProviderLike 识别。"""
        try:
            from core.provider_config import ProviderConfig
        except Exception:
            pytest.skip("ProviderConfig 不可用")

        provider = ProviderConfig(
            name="test",
            api_key="key",
            api_base="https://api.example",
            model="gpt-4o",
        )
        assert isinstance(provider, ProviderLike)

    def test_minimal_duck_object_satisfies_protocol(self) -> None:
        class Duck:
            api_key = "k"
            api_base = "https://b"
            model = "m"
            timeout = 30

            def get_all_models(self) -> list[str]:
                return ["m"]

        assert isinstance(Duck(), ProviderLike)

    def test_missing_method_fails_protocol_check(self) -> None:
        class NoMethod:
            api_key = "k"
            api_base = "b"
            model = "m"
            timeout = 30

        # runtime_checkable Protocol 校验：缺少 get_all_models 方法
        assert not isinstance(NoMethod(), ProviderLike)


# ─── create_client_from_provider_config ─────────────────────────────────────


class TestCreateClientFromProviderConfig:
    def _make_cfg(self, **overrides: Any) -> Any:
        cfg = MagicMock()
        cfg.api_key = overrides.get("api_key", "test-key")
        cfg.api_base = overrides.get("api_base", "https://api.example/v1")
        cfg.model = overrides.get("model", "gpt-4o")
        cfg.timeout = overrides.get("timeout", 60)
        cfg.get_all_models = MagicMock(return_value=overrides.get("models", ["gpt-4o"]))
        return cfg

    def test_uses_model_when_present(self) -> None:
        cfg = self._make_cfg(model="claude-3.5")
        client = create_client_from_provider_config(cfg)
        assert client.model == "claude-3.5"

    def test_falls_back_to_first_get_all_models(self) -> None:
        cfg = self._make_cfg(model="", models=["first-model", "second"])
        client = create_client_from_provider_config(cfg)
        assert client.model == "first-model"

    def test_falls_back_to_gpt_4o_when_no_models_anywhere(self) -> None:
        cfg = self._make_cfg(model="", models=[])
        client = create_client_from_provider_config(cfg)
        assert client.model == "gpt-4o"

    def test_get_all_models_missing_uses_gpt_4o(self) -> None:
        class CfgNoModels:
            api_key = "k"
            api_base = "b"
            model = ""
            timeout = 30

        client = create_client_from_provider_config(CfgNoModels())
        assert client.model == "gpt-4o"

    def test_accepts_completely_minimal_object(self) -> None:
        class Bare:
            pass

        # 即使所有字段都缺失，也应该返回一个客户端（用默认值）
        client = create_client_from_provider_config(Bare())
        assert client.model == "gpt-4o"
        assert client.api_key == ""
        assert client.api_base == ""
        assert client.timeout == 300

    def test_timeout_coerced_to_int(self) -> None:
        cfg = self._make_cfg(timeout="45")
        client = create_client_from_provider_config(cfg)
        assert client.timeout == 45

    def test_invalid_timeout_falls_back_to_default(self) -> None:
        cfg = self._make_cfg(timeout=None)
        client = create_client_from_provider_config(cfg)
        assert client.timeout == 300


# ─── _LayerSpec 不变量 ──────────────────────────────────────────────────────


class TestLayerSpecInvariants:
    def test_only_primary_has_warn_legacy(self) -> None:
        assert _PRIMARY_SPEC.warn_legacy is True
        assert _SECONDARY_SPEC.warn_legacy is False
        assert _LOCAL_SPEC.warn_legacy is False
        assert _LOCAL_CODER_SPEC.warn_legacy is False

    def test_only_primary_has_default_base_url(self) -> None:
        assert _PRIMARY_SPEC.default_base_url == "https://api.deepseek.com/v1"
        assert _SECONDARY_SPEC.default_base_url is None
        assert _LOCAL_SPEC.default_base_url is None
        assert _LOCAL_CODER_SPEC.default_base_url is None

    def test_specs_are_frozen(self) -> None:
        with pytest.raises(Exception):
            _PRIMARY_SPEC.default_timeout = 1  # type: ignore[misc]

    def test_resolve_returns_openai_compatible_config(self) -> None:
        cfg = _resolve_layer(_PRIMARY_SPEC, environ={})
        assert isinstance(cfg, OpenAICompatibleConfig)
