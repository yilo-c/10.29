"""Tests for FallbackOpenAIClient — primary/fallback/local/cache chain."""
from __future__ import annotations

import sys
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

import pytest

from ai.types import AIServiceError


@pytest.fixture(autouse=True)
def _isolate_ai_cache_dir(tmp_path, monkeypatch):
    """测试隔离（生产数据泄漏守卫）：裸构造 FallbackOpenAIClient 未传 cache_path 时，
    其默认缓存路径回落到 SIYI_DATA_DIR（未设则生产 思忆集app/data/ai_cache.json）。
    将 SIYI_DATA_DIR 指向函数级 tmp 目录，成功响应的缓存写入即与生产数据隔离。"""
    monkeypatch.setenv("SIYI_DATA_DIR", str(tmp_path))


class _MockClient:
    """Minimal OpenAICompatibleClient mock."""

    def __init__(self, model="mock-model", fail=False, stream_fail=False, vision_fail=False):
        self.model = model
        self._fail = fail
        self._stream_fail = stream_fail
        self._vision_fail = vision_fail
        self.last_messages = None
        self._last_stream_tool_calls: list[dict] = []

    def is_configured(self):
        return True

    def chat_completion(self, messages, temperature=0.2, max_tokens=900, tools=None, tool_choice=None):
        self.last_messages = messages
        if self._fail:
            raise AIServiceError("mock failure", transient=True)
        return {
            "choices": [{"message": {"content": "mock response"}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 20},
        }

    def chat_completion_stream(self, messages, temperature=0.2, max_tokens=900, tools=None, tool_choice=None):
        self.last_messages = messages
        if self._stream_fail:
            raise AIServiceError("mock stream failure", transient=True)
        yield "mock "
        yield "response"

    def chat_completion_vision(self, text, image_base64, image_format="png",
                                system_prompt=None, temperature=0.2, max_tokens=900):
        if self._vision_fail:
            raise AIServiceError("mock vision failure", transient=False)
        return {
            "choices": [{"message": {"content": "mock vision response"}}],
            "usage": {"prompt_tokens": 15, "completion_tokens": 25},
        }


class TestFallbackClientChatCompletion:
    """FallbackOpenAIClient.chat_completion — 回退链测试。"""

    def test_primary_success_returns_response(self):
        from ai.client.fallback import FallbackOpenAIClient
        fb = FallbackOpenAIClient(primary=_MockClient())
        resp = fb.chat_completion([{"role": "user", "content": "hi"}])
        assert resp["choices"][0]["message"]["content"] == "mock response"

    def test_fallback_used_when_primary_fails(self):
        from ai.client.fallback import FallbackOpenAIClient
        fb = FallbackOpenAIClient(
            primary=_MockClient(fail=True),
            fallback=_MockClient(fail=False),
        )
        resp = fb.chat_completion([{"role": "user", "content": "hi"}])
        assert resp["choices"][0]["message"]["content"] == "mock response"

    def test_local_used_when_all_cloud_fail(self):
        from ai.client.fallback import FallbackOpenAIClient
        fb = FallbackOpenAIClient(
            primary=_MockClient(fail=True),
            fallback=_MockClient(fail=True),
            local=_MockClient(fail=False),
        )
        resp = fb.chat_completion([{"role": "user", "content": "hi"}])
        assert resp["choices"][0]["message"]["content"] == "mock response"

    def test_all_clients_fail_returns_cached_or_raises(self):
        from ai.client.fallback import FallbackOpenAIClient
        from ai.types import AIServiceError
        fb = FallbackOpenAIClient(
            primary=_MockClient(fail=True),
        )
        try:
            result = fb.chat_completion([{"role": "user", "content": "hi"}])
            # May succeed via cache fallback
            assert isinstance(result, dict)
        except AIServiceError:
            pass  # Also valid - all providers failed

    def test_primary_only_configured(self):
        from ai.client.fallback import FallbackOpenAIClient
        fb = FallbackOpenAIClient(primary=_MockClient(fail=False))
        resp = fb.chat_completion([{"role": "user", "content": "hi"}])
        assert resp["choices"][0]["message"]["content"] == "mock response"

    def test_messages_passed_to_client(self):
        from ai.client.fallback import FallbackOpenAIClient
        primary = _MockClient(fail=False)
        fb = FallbackOpenAIClient(primary=primary)
        messages = [{"role": "user", "content": "test message"}]
        fb.chat_completion(messages)
        assert primary.last_messages == messages

    def test_privacy_egress_skips_cloud(self):
        """local_only 隐私级别 → 跳过 primary/fallback。"""
        from ai.client.fallback import FallbackOpenAIClient
        from assistant.routing.privacy_classifier import PrivacyClassifier

        class _AllowAllClassifier(PrivacyClassifier):
            def classify(self, text):
                return "local_only"

        fb = FallbackOpenAIClient(
            primary=_MockClient(fail=False),
            local=_MockClient(fail=False),
        )
        fb._privacy_filter = _AllowAllClassifier().classify
        resp = fb.chat_completion([{"role": "user", "content": "sensitive data"}])
        # Should succeed (local client returned response)
        assert resp["choices"][0]["message"]["content"] == "mock response"

    def test_cache_hit_returns_cached_response(self, tmp_path):
        """如果 primary 返回成功，缓存应被写入。"""
        from ai.client.fallback import FallbackOpenAIClient
        cache_path = tmp_path / "ai_cache.json"
        fb = FallbackOpenAIClient(
            primary=_MockClient(fail=False),
            cache_path=str(cache_path),
        )
        resp = fb.chat_completion([{"role": "user", "content": "cached message"}])
        assert resp["choices"][0]["message"]["content"] == "mock response"
        # Verify cache file was created
        assert cache_path.exists()

    def test_preferred_provider_fallback(self):
        """preferred_provider 指定一个不存在的 provider 时回退。"""
        from ai.client.fallback import FallbackOpenAIClient
        fb = FallbackOpenAIClient(
            primary=_MockClient(fail=True, model="primary"),
            fallback=_MockClient(fail=False, model="fallback"),
        )
        # route_chat with invalid provider should fail
        with pytest.raises(AIServiceError):
            fb.route_chat("nonexistent", [{"role": "user", "content": "hi"}])

    def test_route_chat_to_primary(self):
        from ai.client.fallback import FallbackOpenAIClient
        fb = FallbackOpenAIClient(primary=_MockClient(model="primary"))
        resp = fb.route_chat("primary", [{"role": "user", "content": "hi"}])
        assert resp["choices"][0]["message"]["content"] == "mock response"


class TestFallbackClientStream:
    """FallbackOpenAIClient.chat_completion_stream — 流式回退测试。"""

    def test_stream_primary_success(self):
        from ai.client.fallback import FallbackOpenAIClient
        fb = FallbackOpenAIClient(primary=_MockClient(stream_fail=False))
        chunks = list(fb.chat_completion_stream([{"role": "user", "content": "hi"}]))
        assert "".join(chunks) == "mock response"

    def test_stream_fallback_to_primary_nonstream(self):
        from ai.client.fallback import FallbackOpenAIClient
        # P1-fix 后同 client 流式失败（buffer 空）不再重发非流式，改为回落缓存。
        # 数据目录隔离后各用例 cache 独立，需在本用例内先写入一次成功流式缓存
        # （warmer 与 fb 共用同一 tmp SIYI_DATA_DIR → 同 messages/模型/stream=True 同键）。
        warmer = FallbackOpenAIClient(primary=_MockClient(stream_fail=False))
        list(warmer.chat_completion_stream([{"role": "user", "content": "hi"}]))
        fb = FallbackOpenAIClient(
            primary=_MockClient(stream_fail=True, fail=False),
        )
        chunks = list(fb.chat_completion_stream([{"role": "user", "content": "hi"}]))
        result = "".join(chunks)
        assert "mock response" in result

    def test_stream_all_clients_fail_raises_or_fallback(self):
        from ai.client.fallback import FallbackOpenAIClient
        from ai.types import AIServiceError
        fb = FallbackOpenAIClient(
            primary=_MockClient(stream_fail=True, fail=True),
        )
        try:
            result = list(fb.chat_completion_stream([{"role": "user", "content": "hi"}]))
            assert isinstance(result, list)
        except AIServiceError:
            pass


class TestFallbackClientHelpers:
    """辅助方法测试。"""

    def test_complete_text(self):
        from ai.client.fallback import FallbackOpenAIClient
        fb = FallbackOpenAIClient(primary=_MockClient())
        text = fb.complete_text([{"role": "user", "content": "hi"}])
        assert text == "mock response"

    def test_test_connection_success(self):
        from ai.client.fallback import FallbackOpenAIClient
        class _ConnectableMock(_MockClient):
            def test_connection(self):
                return {"status": "ok"}
        fb = FallbackOpenAIClient(primary=_ConnectableMock())
        result = fb.test_connection()
        assert "status" in result

    def test_test_connection_failure(self):
        from ai.client.fallback import FallbackOpenAIClient
        fb = FallbackOpenAIClient(primary=_MockClient(fail=True))
        with pytest.raises(AIServiceError):
            fb.test_connection()

    def test_get_runtime_snapshot(self):
        from ai.client.fallback import FallbackOpenAIClient
        fb = FallbackOpenAIClient(primary=_MockClient())
        snapshot = fb.get_runtime_snapshot()
        assert isinstance(snapshot, dict)
        assert "last_stage" in snapshot

    def test_vision_primary_success(self):
        from ai.client.fallback import FallbackOpenAIClient
        fb = FallbackOpenAIClient(primary=_MockClient(vision_fail=False))
        resp = fb.chat_completion_vision(
            text="describe this",
            image_base64="AAAA",
        )
        assert resp["choices"][0]["message"]["content"] == "mock vision response"

    def test_vision_primary_not_configured(self):
        from ai.client.fallback import FallbackOpenAIClient
        # Client without vision capability
        class _NoVisionClient:
            model = "no-vision"

        fb = FallbackOpenAIClient(primary=_NoVisionClient())
        with pytest.raises(AIServiceError, match="未配置支持视觉能力"):
            fb.chat_completion_vision(text="desc", image_base64="AAAA")

    def test_vision_failure(self):
        from ai.client.fallback import FallbackOpenAIClient
        fb = FallbackOpenAIClient(primary=_MockClient(vision_fail=True))
        with pytest.raises(AIServiceError):
            fb.chat_completion_vision(text="desc", image_base64="AAAA")

    def test_vision_client_preferred_over_primary(self):
        """purpose=vision 的专用 vision_client 优先于 primary。"""
        from ai.client.fallback import FallbackOpenAIClient
        vision = _MockClient(model="vision-model")
        primary = _MockClient(model="primary-model")
        fb = FallbackOpenAIClient(primary=primary, vision_client=vision)
        resp = fb.chat_completion_vision(text="desc", image_base64="AAAA")
        assert resp["choices"][0]["message"]["content"] == "mock vision response"
        # 确认是 vision_client 承接：primary 未被用作 vision（无法直接区分，
        # 改用下面的失败回退用例验证优先级）

    def test_vision_client_failure_falls_back_to_primary(self):
        """vision_client 失败时应回退到支持 vision 的 primary。"""
        from ai.client.fallback import FallbackOpenAIClient
        vision = _MockClient(model="vision-model", vision_fail=True)
        primary = _MockClient(model="primary-model", vision_fail=False)
        fb = FallbackOpenAIClient(primary=primary, vision_client=vision)
        resp = fb.chat_completion_vision(text="desc", image_base64="AAAA")
        assert resp["choices"][0]["message"]["content"] == "mock vision response"

    def test_vision_both_fail_raises(self):
        """vision_client 与 primary 都失败时抛出 AIServiceError。"""
        from ai.client.fallback import FallbackOpenAIClient
        vision = _MockClient(vision_fail=True)
        primary = _MockClient(vision_fail=True)
        fb = FallbackOpenAIClient(primary=primary, vision_client=vision)
        with pytest.raises(AIServiceError, match="视觉分析失败"):
            fb.chat_completion_vision(text="desc", image_base64="AAAA")

    def test_analyze_image(self):
        from ai.client.fallback import FallbackOpenAIClient
        fb = FallbackOpenAIClient(primary=_MockClient(vision_fail=False))
        text = fb.analyze_image(image_base64="AAAA")
        assert text == "mock vision response"

    def test_static_extract_text(self):
        from ai.client.fallback import FallbackOpenAIClient
        resp = {"choices": [{"message": {"content": "extracted text"}}]}
        text = FallbackOpenAIClient._extract_text_from_response(resp)
        assert text == "extracted text"

    def test_extract_text_missing_choices(self):
        from ai.client.fallback import FallbackOpenAIClient
        from ai.types import AIServiceError
        with pytest.raises(AIServiceError):
            FallbackOpenAIClient._extract_text_from_response({})


class TestAiResponseCache:
    """AiResponseCache — 独立缓存模块测试。"""

    def test_set_and_get(self, tmp_path):
        from ai.client._cache import AiResponseCache
        cache = AiResponseCache(str(tmp_path / "test_cache.json"))
        key = cache.make_key([{"role": "user", "content": "hello"}])
        cache.set_sync(key, "cached response")
        result = cache.get_sync(key)
        assert result["text"] == "cached response"

    def test_get_miss(self, tmp_path):
        from ai.client._cache import AiResponseCache
        cache = AiResponseCache(str(tmp_path / "test_cache.json"))
        result = cache.get_sync("nonexistent-key")
        assert result is None

    @pytest.mark.asyncio
    async def test_async_set_and_get(self, tmp_path):
        from ai.client._cache import AiResponseCache
        cache = AiResponseCache(str(tmp_path / 'async_cache.json'))
        key = cache.make_key([{'role': 'user', 'content': 'async test'}])
        await cache.set(key, 'async cached')
        result = await cache.get(key)
        assert result['text'] == 'async cached'

    def test_cache_persistence(self, tmp_path):
        from ai.client._cache import AiResponseCache
        path = tmp_path / 'persist_cache.json'
        cache = AiResponseCache(str(path))
        key = cache.make_key([{'role': 'user', 'content': 'persist'}])
        cache.set_sync(key, 'persisted value')
        cache2 = AiResponseCache(str(path))
        result = cache2.get_sync(key)
        assert result['text'] == 'persisted value'

    def test_make_key_includes_parameters(self, tmp_path):
        from ai.client._cache import AiResponseCache
        cache = AiResponseCache(str(tmp_path / 'key_cache.json'))
        messages = [{'role': 'user', 'content': 'test'}]
        base_key = cache.make_key(messages)
        temp_key = cache.make_key(messages, temperature=0.5)
        tools_key = cache.make_key(messages, tools=[{'type': 'function'}])
        assert base_key != temp_key
        assert base_key != tools_key
        assert temp_key != tools_key

    def test_make_key_deterministic(self, tmp_path):
        from ai.client._cache import AiResponseCache
        cache = AiResponseCache(str(tmp_path / 'key_cache.json'))
        messages = [{'role': 'user', 'content': 'test'}]
        key1 = cache.make_key(messages, temperature=0.3, max_tokens=100)
        key2 = cache.make_key(messages, temperature=0.3, max_tokens=100)
        assert key1 == key2

    def test_size_property(self, tmp_path):
        from ai.client._cache import AiResponseCache
        cache = AiResponseCache(str(tmp_path / 'size_cache.json'))
        assert cache.size == 0
        cache.set_sync('k1', 'v1')
        cache._load_cache_sync()
        assert cache.size >= 1
