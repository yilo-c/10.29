"""ProviderConfig 数据模型与 CRUD 测试。"""

from __future__ import annotations

import sys
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[2]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

import pytest
from core.provider_config import (
    ProviderConfig,
    load_providers,
    save_providers,
    provider_to_dict,
    dict_to_provider,
    get_default_providers,
    find_provider,
)


class _SettingsRepo:
    def __init__(self, values=None):
        self._values = dict(values or {})

    def get_setting(self, key, default=None):
        return self._values.get(key, default)

    def set_setting(self, key, value):
        self._values[key] = value


# ═══════════════════════════════════════════════════════════
# ProviderConfig dataclass
# ═══════════════════════════════════════════════════════════


class TestProviderConfig:
    def test_create_with_defaults(self):
        """未指定字段应使用默认值。"""
        p = ProviderConfig()
        assert p.name == ""
        assert p.api_base == ""
        assert p.api_key == ""
        assert p.model == ""
        assert p.timeout == 300
        assert p.enabled is True
        assert p.is_fallback is False
        assert p.is_local is False

    def test_create_with_custom_values(self):
        p = ProviderConfig(
            name="TestAI",
            api_base="https://api.test.ai/v1",
            api_key="sk-test",
            model="test-model",
            timeout=60,
            enabled=True,
            is_fallback=True,
            description="测试用 provider",
        )
        assert p.name == "TestAI"
        assert p.api_key == "sk-test"
        assert p.timeout == 60
        assert p.is_fallback is True

    def test_is_valid_with_all_fields(self):
        p = ProviderConfig(name="DeepSeek", api_base="https://api.deepseek.com/v1", api_key="sk-xxx")
        assert p.is_valid() is True

    def test_is_valid_missing_name(self):
        p = ProviderConfig(name="", api_base="https://api.test.com", api_key="sk-xxx")
        assert p.is_valid() is False

    def test_is_valid_missing_api_base(self):
        p = ProviderConfig(name="Test", api_base="", api_key="sk-xxx")
        assert p.is_valid() is False

    def test_is_valid_local_no_key(self):
        """is_local=True 时不需要 api_key。"""
        p = ProviderConfig(name="Local", api_base="http://localhost:11434", api_key="", is_local=True)
        assert p.is_valid() is True

    def test_is_valid_local_with_key(self):
        p = ProviderConfig(name="Local", api_base="http://localhost:11434", api_key="optional", is_local=True)
        assert p.is_valid() is True


# ═══════════════════════════════════════════════════════════
# dict <-> ProviderConfig 转换
# ═══════════════════════════════════════════════════════════


class TestConversion:
    def test_provider_to_dict(self):
        p = ProviderConfig(name="Test", api_base="https://test.ai", api_key="key", model="m", timeout=60)
        d = provider_to_dict(p)
        assert d["name"] == "Test"
        assert d["api_base"] == "https://test.ai"
        assert d["api_key"] == "key"
        assert d["timeout"] == 60
        assert d["enabled"] is True

    def test_dict_to_provider(self):
        d = {
            "name": "TestAI",
            "api_base": "https://api.test.ai",
            "api_key": "sk-test",
            "model": "m1",
            "timeout": 120,
            "enabled": False,
            "is_fallback": True,
            "description": "desc",
        }
        p = dict_to_provider(d)
        assert p.name == "TestAI"
        assert p.timeout == 120
        assert p.enabled is False
        assert p.is_fallback is True

    def test_dict_to_provider_missing_fields(self):
        """缺失字段应使用默认值填充。"""
        p = dict_to_provider({})
        assert p.name == ""
        assert p.api_base == ""
        assert p.timeout == 300
        assert p.enabled is True

    def test_roundtrip(self):
        p1 = ProviderConfig(name="RTTEST", api_base="https://rt.ai", api_key="rt-key", model="rt-model")
        d = provider_to_dict(p1)
        p2 = dict_to_provider(d)
        assert p1.name == p2.name
        assert p1.api_base == p2.api_base
        assert p1.api_key == p2.api_key
        assert p1.model == p2.model


# ═══════════════════════════════════════════════════════════
# CRUD: load / save
# ═══════════════════════════════════════════════════════════


class TestCRUD:
    def test_load_providers_empty(self):
        """无配置时应自动播种默认 provider（修复“模型列表不可用”）。"""
        repo = _SettingsRepo()
        providers = load_providers(repo)
        defaults = get_default_providers()
        assert len(providers) == len(defaults) == 3
        assert [p.name for p in providers] == [p.name for p in defaults]
        # 播种结果应已持久化回仓储（幂等）
        assert len(repo.get_setting("provider_configs", [])) == 3

    def test_save_and_load_roundtrip(self):
        """save_providers → load_providers 应完整往返。"""
        repo = _SettingsRepo()
        original = [
            ProviderConfig(name="A", api_base="https://a.ai", api_key="key-a"),
            ProviderConfig(name="B", api_base="https://b.ai", api_key="key-b", enabled=False),
        ]
        save_providers(repo, original)
        loaded = load_providers(repo)
        assert len(loaded) == 2
        assert loaded[0].name == "A"
        assert loaded[1].enabled is False

    def test_load_skips_non_dict_items(self):
        """load_providers 应跳过非 dict 项。"""
        repo = _SettingsRepo({"provider_configs": [{"name": "Valid", "api_base": "https://v.ai", "api_key": "k"}, "invalid_string"]})
        providers = load_providers(repo)
        assert len(providers) == 1
        assert providers[0].name == "Valid"


# ═══════════════════════════════════════════════════════════
# get_default_providers / find_provider
# ═══════════════════════════════════════════════════════════


class TestDefaults:
    def test_get_default_providers_count(self):
        providers = get_default_providers()
        assert len(providers) == 3

    def test_get_default_providers_include_deepseek(self):
        providers = get_default_providers()
        names = [p.name for p in providers]
        assert "DeepSeek" in names
        assert "OpenAI" in names
        assert "本地 Ollama" in names

    def test_default_ollama_is_local(self):
        providers = get_default_providers()
        ollama = [p for p in providers if p.is_local]
        assert len(ollama) == 1
        assert ollama[0].name == "本地 Ollama"
        assert ollama[0].api_key == ""
        assert ollama[0].is_valid() is True


class TestFindProvider:
    def test_find_by_name(self):
        providers = [
            ProviderConfig(name="Alpha", api_base="https://a.ai", api_key="k"),
            ProviderConfig(name="Beta", api_base="https://b.ai", api_key="k"),
        ]
        found = find_provider(providers, "Alpha")
        assert found is not None
        assert found.name == "Alpha"

    def test_find_not_found(self):
        providers = [ProviderConfig(name="Only", api_base="https://o.ai", api_key="k")]
        assert find_provider(providers, "NonExistent") is None

    def test_find_empty_list(self):
        assert find_provider([], "anything") is None
