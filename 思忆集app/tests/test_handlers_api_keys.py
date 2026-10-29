"""Integration tests for API key service — CRUD operations."""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

import pytest


class FakeSettingsRepo:
    """In-memory settings repository for testing ApiKeyService."""
    def __init__(self):
        self._data: dict[str, object] = {}

    def get_setting(self, key: str, default=None):
        return self._data.get(key, default)

    def set_setting(self, key: str, value):
        self._data[key] = value


@pytest.fixture
def api_key_service(monkeypatch):
    from services.api_key_service import ApiKeyService

    # Prevent env var leakage into flat-key path
    for env_var in (
        "SIYI_LLM_API_KEY", "OPENAI_API_KEY", "DEEPSEEK_API_KEY",
        "MOONSHOT_API_KEY", "SIYI_LLM_BASE_URL", "SIYI_LLM_MODEL",
    ):
        monkeypatch.setenv(env_var, "", prepend=False)

    repo = FakeSettingsRepo()
    # Initialize with empty provider_configs
    repo.set_setting("provider_configs", [])
    # 提供 data_dir 以启用加密（测试安全路径而非明文降级）
    with tempfile.TemporaryDirectory() as tmpdir:
        yield ApiKeyService(repo, data_dir=tmpdir)


def test_list_keys_empty(api_key_service):
    keys = api_key_service.list_keys()
    # Without any config, should return empty or only env-based keys
    assert isinstance(keys, list)


def test_create_provider_key(api_key_service):
    result = api_key_service.create_key({
        "name": "Test DeepSeek",
        "key_value": "sk-test-key-12345",
        "base_url": "https://api.deepseek.com/v1",
        "model": "deepseek-chat",
        "is_provider": True,
        "purpose": "primary",
    })
    assert result.get("id")
    # Verify it appears in list
    keys = api_key_service.list_keys()
    provider_keys = [k for k in keys if k.get("name") == "Test DeepSeek"]
    assert len(provider_keys) == 1
    # Key should be masked in list (must not reveal plaintext)
    key_val = provider_keys[0].get("key_value", "")
    assert len(key_val) > 0
    assert "sk-test" not in key_val


def test_delete_provider_key(api_key_service):
    create_result = api_key_service.create_key({
        "name": "ToDelete",
        "key_value": "sk-delete-me",
        "base_url": "https://api.openai.com/v1",
        "model": "gpt-4o",
        "is_provider": True,
    })
    key_id = create_result.get("id", "")
    assert key_id

    api_key_service.delete_key(key_id)

    keys = api_key_service.list_keys()
    ids = {k.get("id") for k in keys}
    assert key_id not in ids


def test_create_local_provider(api_key_service):
    result = api_key_service.create_key({
        "name": "Local Ollama",
        "base_url": "http://localhost:11434/v1",
        "model": "qwen2.5-7b:latest",
        "is_provider": True,
        "is_local": True,
        "purpose": "local",
    })
    assert result.get("id")
    keys = api_key_service.list_keys()
    local = [k for k in keys if k.get("name") == "Local Ollama"]
    assert len(local) == 1
    assert local[0].get("is_local") is True


def test_update_provider_key(api_key_service):
    create_result = api_key_service.create_key({
        "name": "Before Update",
        "key_value": "sk-original",
        "base_url": "https://api.deepseek.com/v1",
        "model": "deepseek-chat",
        "is_provider": True,
    })
    key_id = create_result.get("id", "")

    api_key_service.update_key(key_id, {
        "description": "Updated description",
        "purpose": "fallback",
        "enabled": False,
    })

    keys = api_key_service.list_keys()
    updated = [k for k in keys if k.get("id") == key_id]
    assert len(updated) == 1
    assert updated[0].get("description") == "Updated description"
    assert updated[0].get("purpose") == "fallback"
    assert updated[0].get("enabled") is False


def test_list_keys_has_purpose(api_key_service):
    api_key_service.create_key({
        "name": "Purpose Test",
        "key_value": "sk-purpose",
        "base_url": "https://api.deepseek.com/v1",
        "model": "deepseek-chat",
        "is_provider": True,
        "purpose": "primary",
    })
    keys = api_key_service.list_keys()
    purpose_test = [k for k in keys if k.get("name") == "Purpose Test"]
    assert len(purpose_test) == 1
    assert "purpose" in purpose_test[0]
