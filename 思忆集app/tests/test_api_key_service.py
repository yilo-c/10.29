"""Tests for ApiKeyService."""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

import pytest

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from services.api_key_service import ApiKeyService, mask_key


class _FakeSettings:
    def __init__(self):
        self._store: dict[str, str] = {}

    def get_setting(self, key: str, default: str = "") -> str:
        return self._store.get(key, default)

    def set_setting(self, key: str, value: str) -> None:
        self._store[key] = value


@pytest.fixture
def svc():
    # 提供 data_dir 以启用加密（测试安全路径而非明文降级）
    with tempfile.TemporaryDirectory() as tmpdir:
        yield ApiKeyService(_FakeSettings(), data_dir=tmpdir)


def test_mask_key_short():
    assert mask_key("abc") == "a**"         # len=3, keep=1
    assert mask_key("abcd") == "a***"       # len=4, keep=1
    assert mask_key("abcdefgh") == "ab******"  # len=8, keep=2


def test_mask_key_long():
    result = mask_key("sk-fakefakefakefakefakefakefakefa12")
    assert result.startswith("sk-f")
    assert "****" in result or "*" in result
    assert result.endswith("efa12")


def test_mask_key_empty():
    assert mask_key("") == ""
    assert mask_key("   ").strip() == ""


def test_list_keys_empty(svc, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-env-test-key")
    keys = svc.list_keys()
    # Should return flat keys + service keys (all empty)
    flat = [k for k in keys if not k["key_value"]]
    assert len(flat) > 0
    # env_var_names should always be a list
    for k in keys:
        assert isinstance(k.get("env_var_names"), list)
    # 确认环境变量密钥被正确识别
    assert any(k["from_env"] for k in keys)


def test_list_keys_with_provider(svc):
    svc._settings.set_setting("provider_configs", [
        {"name": "TestAI", "api_base": "https://test.ai/v1", "api_key": "sk-test123", "model": "test-model"},
    ])
    keys = svc.list_keys()
    provider_keys = [k for k in keys if k["id"] == "provider:TestAI"]
    assert len(provider_keys) == 1
    pk = provider_keys[0]
    assert pk["name"] == "TestAI"
    assert "*" in pk["key_value"]  # 掩码后应包含星号
    assert pk["is_provider"]
    assert pk["category"] == "llm"


def test_list_keys_with_service_key(svc):
    svc._settings.set_setting("youdao_app_key", "my_youdao_key")
    keys = svc.list_keys()
    yd = [k for k in keys if k["id"] == "service:youdao_app_key"]
    assert len(yd) == 1
    assert "*" in yd[0]["key_value"]  # 掩码后应包含星号


def test_create_provider(svc):
    result = svc.create_key({
        "name": "NewAI",
        "key_value": "sk-new",
        "base_url": "https://new.ai/v1",
        "model": "new-model",
        "is_provider": True,
    })
    assert result["id"] == "provider:NewAI"

    providers = svc._settings.get_setting("provider_configs", [])
    assert len(providers) == 1
    assert providers[0]["name"] == "NewAI"
    # API key 应被安全存储加密（dpapi:/keychain:/secret:/fernet: 前缀），而非明文
    assert svc._secure.is_secure_token(providers[0]["api_key"])
    assert providers[0]["api_key"] != "sk-new"


def test_create_duplicate_provider_raises(svc):
    svc._settings.set_setting("provider_configs", [
        {"name": "DupAI", "api_key": "sk-old"},
    ])
    with pytest.raises(ValueError, match="已存在"):
        svc.create_key({"name": "DupAI", "key_value": "sk-new", "is_provider": True})


def test_create_flat_key(svc):
    result = svc.create_key({
        "flat_key": "web_search_tavily_api_key",
        "key_value": "tavily-secret",
    })
    assert result["id"] == "flat:web_search_tavily_api_key"
    # 值应被安全存储加密
    stored = svc._settings.get_setting("web_search_tavily_api_key", "")
    assert svc._secure.is_secure_token(stored)
    assert stored != "tavily-secret"


def test_update_provider_key(svc):
    svc._settings.set_setting("provider_configs", [
        {"name": "TestAI", "api_base": "https://test.ai/v1", "api_key": "sk-old", "model": "old-model"},
    ])
    svc.update_key("provider:TestAI", {"key_value": "sk-new", "model": "new-model"})
    providers = svc._settings.get_setting("provider_configs", [])
    # api_key 应被安全存储加密
    assert svc._secure.is_secure_token(providers[0]["api_key"])
    assert providers[0]["api_key"] != "sk-new"
    assert providers[0]["model"] == "new-model"


def test_update_flat_key(svc):
    svc._settings.set_setting("web_search_tavily_api_key", "old-val")
    svc.update_key("service:web_search_tavily_api_key", {"key_value": "new-val"})
    # 值应被安全存储加密
    stored = svc._settings.get_setting("web_search_tavily_api_key", "")
    assert svc._secure.is_secure_token(stored)
    assert stored != "new-val"


def test_delete_provider(svc):
    svc._settings.set_setting("provider_configs", [
        {"name": "TestAI", "api_key": "sk-test"},
        {"name": "OtherAI", "api_key": "sk-other"},
    ])
    svc.delete_key("provider:TestAI")
    providers = svc._settings.get_setting("provider_configs", [])
    assert len(providers) == 1
    assert providers[0]["name"] == "OtherAI"


def test_delete_flat_key_clears(svc):
    svc._settings.set_setting("youdao_app_key", "some-key")
    svc.delete_key("service:youdao_app_key")
    assert svc._settings.get_setting("youdao_app_key", "") == ""


def test_get_key_returns_full_value(svc):
    svc._settings.set_setting("provider_configs", [
        {"name": "TestAI", "api_base": "https://test.ai/v1", "api_key": "sk-full-value", "model": "t"},
    ])
    entry = svc.get_key("provider:TestAI")
    assert entry is not None
    assert entry["key_value"] == "sk-full-value"


def test_get_nonexistent_key(svc):
    entry = svc.get_key("provider:NoExist")
    assert entry is None


def test_load_empty_env_flat_key(svc, monkeypatch):
    """Without env vars, flat key value is empty when not stored."""
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("SIYI_LLM_API_KEY", raising=False)
    # P1.8: DEEPSEEK_API_KEY 现在也是 llm_api_key 的 fallback env，需一并清除
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    keys = svc.list_keys()
    flat_key = next((k for k in keys if k["id"] == "flat:llm_api_key"), None)
    assert flat_key is not None
    assert flat_key["from_env"] is False


def test_migrate_plaintext_flat_key(svc):
    """明文存储的旧密钥应在首次读取时透明迁移到安全存储。"""
    svc._settings.set_setting("youdao_app_key", "plain-old-key")
    entry = svc.get_key("service:youdao_app_key")
    assert entry is not None
    assert entry["key_value"] == "plain-old-key"
    # 存储值应已被替换为安全 token
    stored = svc._settings.get_setting("youdao_app_key", "")
    assert svc._secure.is_secure_token(stored)


def test_migrate_plaintext_provider_key(svc):
    """明文存储的 provider api_key 应在首次读取时透明迁移。"""
    svc._settings.set_setting("provider_configs", [
        {"name": "PlainAI", "api_base": "https://plain.ai/v1", "api_key": "sk-plain", "model": "m"},
    ])
    entry = svc.get_key("provider:PlainAI")
    assert entry is not None
    assert entry["key_value"] == "sk-plain"
    providers = svc._settings.get_setting("provider_configs", [])
    assert svc._secure.is_secure_token(providers[0]["api_key"])


def test_migrate_legacy_fernet_flat_key(svc):
    """旧版 core.crypto_utils Fernet 加密的密钥应透明迁移。"""
    from core.crypto_utils import encrypt as _legacy_encrypt

    legacy_ciphertext = _legacy_encrypt("legacy-secret", svc._data_dir)
    svc._settings.set_setting("youdao_app_key", legacy_ciphertext)
    entry = svc.get_key("service:youdao_app_key")
    assert entry is not None
    assert entry["key_value"] == "legacy-secret"
    stored = svc._settings.get_setting("youdao_app_key", "")
    assert svc._secure.is_secure_token(stored)
    assert not stored.startswith("gAAAAA")


def test_roundtrip_after_migration(svc):
    """迁移后的密钥可以正常解密并在后续读取中保持可用。"""
    svc._settings.set_setting("todoist_api_token", "todoist-token-123")
    assert svc.get_key("service:todoist_api_token")["key_value"] == "todoist-token-123"
    # 再次读取不应重复迁移或丢失
    assert svc.get_key("service:todoist_api_token")["key_value"] == "todoist-token-123"
