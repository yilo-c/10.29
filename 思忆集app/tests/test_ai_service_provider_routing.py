from __future__ import annotations

import logging
import sys
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from services.ai_service import (
    AIServiceError,
    OpenAICompatibleClient,
    ProviderRoutingTable,
    FallbackOpenAIClient,
)
from core.settings_schema import KEY_LLM_PROVIDER_ROUTING


class _SettingsRepository:
    def __init__(self, value: str = ""):
        self.value = value
        self.requested_keys: list[tuple[str, object]] = []

    def get_setting(self, key: str, default=None):
        self.requested_keys.append((key, default))
        return self.value


def test_provider_routing_table_uses_schema_constant_for_settings_key():
    settings = _SettingsRepository('{"coding": "primary"}')
    ProviderRoutingTable(settings_repository=settings, environ={})

    assert settings.requested_keys == [(KEY_LLM_PROVIDER_ROUTING, "")]


def test_provider_routing_table_reads_settings_repository_before_environment():
    settings = _SettingsRepository('{"reasoning": "primary"}')
    routing = ProviderRoutingTable(
        settings_repository=settings,
        environ={"SIYI_LLM_PROVIDER_ROUTING": '{"reasoning": "deepseek"}'},
    )

    assert settings.requested_keys == [(KEY_LLM_PROVIDER_ROUTING, "")]
    assert routing._routing == {"reasoning": "primary"}


def test_provider_routing_table_falls_back_to_environment_when_settings_empty():
    routing = ProviderRoutingTable(
        settings_repository=_SettingsRepository(""),
        environ={"SIYI_LLM_PROVIDER_ROUTING": '{"summarization": "deepseek"}'},
    )

    assert routing._routing == {"summarization": "deepseek"}


def test_provider_routing_table_logs_invalid_json_and_degrades(caplog):
    with caplog.at_level(logging.WARNING, logger="siyi.ai"):
        routing = ProviderRoutingTable(
            settings_repository=_SettingsRepository("{not-json"),
            environ={},
        )

    assert routing._routing == {}
    assert "llm_provider_routing 解析失败" in caplog.text


class _MockClient:
    def __init__(self, model="mock-model", fail=False, fail_count=0):
        self.model = model
        self._fail = fail
        self._call_count = 0
        self._fail_count = fail_count
        self.last_messages = None

    def is_configured(self):
        return True

    def chat_completion(self, messages, temperature=0.2, max_tokens=900, tools=None, tool_choice=None):
        self._call_count += 1
        self.last_messages = messages
        if self._fail:
            raise AIServiceError("mock failure", transient=True)
        return {"choices": [{"message": {"content": "mock response"}}]}


def test_route_chat_routes_to_primary():
    fb = FallbackOpenAIClient(primary=_MockClient(model="primary-model"))
    resp = fb.route_chat("primary", [{"role": "user", "content": "hello"}])
    assert resp["choices"][0]["message"]["content"] == "mock response"


def test_route_chat_unknown_provider_raises():
    fb = FallbackOpenAIClient(primary=_MockClient())
    try:
        fb.route_chat("nonexistent", [{"role": "user", "content": "hello"}])
        assert False, "should have raised"
    except AIServiceError as e:
        assert "invalid_provider" in str(e.error_kind) or "invalid" in str(e.error_kind).lower()


def test_route_chat_falls_back_when_target_fails():
    fb = FallbackOpenAIClient(
        primary=_MockClient(model="primary", fail=False),
        fallback=_MockClient(model="fallback"),
    )
    resp = fb.route_chat("fallback", [{"role": "user", "content": "hello"}])
    # fallback is preferred, should route to it directly
    assert resp["choices"][0]["message"]["content"] == "mock response"


def test_route_chat_raises_when_all_exhausted():
    fb = FallbackOpenAIClient(
        primary=_MockClient(model="primary", fail=True),
        fallback=None,
        local=None,
    )
    try:
        fb.route_chat("primary", [{"role": "user", "content": "hello"}])
        assert False, "should have raised"
    except AIServiceError as e:
        assert e.error_kind == "routing_exhausted"


def test_route_chat_runtime_snapshot_updated():
    fb = FallbackOpenAIClient(primary=_MockClient(model="primary-model"))
    fb.route_chat("primary", [{"role": "user", "content": "hello"}])
    snap = fb.get_runtime_snapshot()
    assert "routed:" in snap.get("last_stage", "")


def test_build_provider_chain_preferred_first():
    fb = FallbackOpenAIClient(primary=_MockClient(), local=_MockClient())
    chain = fb._build_provider_chain("local")
    names = [name for name, _ in chain]
    assert names[0] == "local"
    assert "primary" in names


def test_build_provider_chain_unknown_preferred():
    fb = FallbackOpenAIClient(primary=_MockClient())
    chain = fb._build_provider_chain("unknown")
    names = [name for name, _ in chain]
    assert names == ["primary", "fallback", "local", "local_coder"]


def test_provider_routing_table_rejects_non_dict_json_and_falls_back(caplog):
    primary = OpenAICompatibleClient(api_key="primary-key", api_base="https://primary.example.com/v1", model="gpt", timeout=60)

    with caplog.at_level(logging.WARNING, logger="siyi.ai"):
        routing = ProviderRoutingTable(
            settings_repository=_SettingsRepository('["not", "a", "dict"]'),
            environ={},
        )

    selected = routing.get_client_for_query("请分析这个问题", primary)

    assert routing._routing == {}
    assert selected is primary
    assert "llm_provider_routing 必须是字典" in caplog.text


def test_provider_routing_table_returns_primary_when_provider_not_configured():
    primary = OpenAICompatibleClient(api_key="primary-key", api_base="https://primary.example.com/v1", model="gpt", timeout=60)
    routing = ProviderRoutingTable(
        settings_repository=_SettingsRepository('{"reasoning": "deepseek"}'),
        environ={},
    )

    selected = routing.get_client_for_query("请分析这个问题", primary)

    assert selected is primary


def test_cache_ttl_prunes_expired_entries(tmp_path):
    """缓存 TTL 过期清理和大小限制。"""
    import asyncio
    import time
    cache_file = tmp_path / "ai_cache.json"
    fb = FallbackOpenAIClient(
        primary=_MockClient(),
        cache_path=str(cache_file),
        cache_ttl_seconds=1,
        cache_max_entries=10,
    )

    fb._store_cached_sync("key1", "hello")
    assert fb._get_cached_sync("key1") is not None

    time.sleep(1.1)
    assert fb._get_cached_sync("key1") is None  # TTL expired at read time

    for i in range(15):
        fb._store_cached_sync(f"k{i:04d}", f"val{i}")
    asyncio.run(fb._cache_obj.prune())
    loaded = fb._load_cache_sync()
    assert len(loaded) <= 10


def test_cache_old_format_pruned(tmp_path):
    """不带 ts 字段的旧格式条目在加载时被视为过期并清理。"""
    import asyncio
    import json
    cache_file = tmp_path / "ai_cache.json"
    cache_file.write_text(json.dumps({"old_key": {"text": "old data"}}, ensure_ascii=False), encoding="utf-8")
    fb = FallbackOpenAIClient(
        primary=_MockClient(),
        cache_path=str(cache_file),
        cache_ttl_seconds=86400,
    )
    asyncio.run(fb._cache_obj.prune())
    loaded = fb._load_cache_sync()
    assert "old_key" not in loaded
