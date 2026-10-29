"""RelayGateway 单测：模型映射、双协议非流/流式、错误体、Bearer 校验。

用 stub FallbackOpenAIClient（实现 chat_completion / chat_completion_stream /
achat_completion_stream / get_client_by_provider）验证中继网关的转换与鉴权逻辑，
不触网。
"""

from __future__ import annotations

from typing import Any

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from backend.relay_gateway import RelayGateway


class StubFallback:
    """最小 FallbackOpenAIClient 桩：记录调用并返回固定内容。"""

    ROUTE_PROVIDERS = {"primary": "primary", "fallback": "fallback", "local": "local", "local_coder": "local_coder"}

    def __init__(self) -> None:
        self.last_messages: list[dict[str, Any]] = []
        self._providers: dict[str, Any] = {}

    def chat_completion(self, messages: list[dict[str, str]], **kwargs: Any) -> dict[str, Any]:
        self.last_messages = list(messages)
        joined = "".join(str(m.get("content", "")) for m in messages)
        return {
            "choices": [{"message": {"content": f"echo:{joined}"}}],
            "usage": {"prompt_tokens": 3, "completion_tokens": 4, "total_tokens": 7},
        }

    def chat_completion_stream(self, messages: list[dict[str, str]], **kwargs: Any):
        for piece in ("你", "好"):
            yield piece

    async def achat_completion_stream(self, messages: list[dict[str, str]], **kwargs: Any):
        for piece in ("你", "好"):
            yield piece

    def get_client_by_provider(self, provider: str, model: str | None = None, settings_repository: Any = None):
        if provider == "deepseek" and model == "deepseek-v4":
            return _ProviderStub()
        return self._providers.get(provider)


class _ProviderStub:
    """provider 直选命中的裸 OpenAICompatibleClient 桩。"""

    model = "deepseek-v4"

    def chat_completion(self, messages: list[dict[str, str]], **kwargs: Any) -> dict[str, Any]:
        return {"choices": [{"message": {"content": "provider-ok"}}], "usage": {"prompt_tokens": 1, "completion_tokens": 1}}

    def chat_completion_stream(self, messages: list[dict[str, str]], **kwargs: Any):
        for piece in ("P", "rovider"):
            yield piece


def _app(gateway: RelayGateway) -> web.Application:
    app = web.Application()
    app.router.add_post("/v1/chat/completions", gateway.handle_openai_chat)
    app.router.add_post("/v1/messages", gateway.handle_anthropic_messages)
    return app


def _gateway(fallback: Any = None, key: str = "secret-key", model_map: dict[str, str] | None = None) -> RelayGateway:
    return RelayGateway(fallback or StubFallback(), key, model_map)


# ── 鉴权 ──────────────────────────────────────────────────


async def test_openai_wrong_key_unauthorized() -> None:
    async with TestClient(TestServer(_app(_gateway()))) as client:
        resp = await client.post(
            "/v1/chat/completions",
            headers={"Authorization": "Bearer wrong"},
            json={"model": "auto", "messages": [{"role": "user", "content": "hi"}]},
        )
        assert resp.status == 401
        data = await resp.json()
        assert data["error"]["type"] == "authentication_error"


async def test_openai_missing_key_unauthorized() -> None:
    async with TestClient(TestServer(_app(_gateway()))) as client:
        resp = await client.post(
            "/v1/chat/completions",
            json={"model": "auto", "messages": [{"role": "user", "content": "hi"}]},
        )
        assert resp.status == 401


async def test_anthropic_wrong_key_unauthorized() -> None:
    async with TestClient(TestServer(_app(_gateway()))) as client:
        resp = await client.post(
            "/v1/messages",
            headers={"x-api-key": "nope"},
            json={"model": "auto", "max_tokens": 100, "messages": [{"role": "user", "content": "hi"}]},
        )
        assert resp.status == 401
        data = await resp.json()
        assert data["type"] == "error"


# ── OpenAI 非流式 ─────────────────────────────────────────


async def test_openai_nonstream() -> None:
    fallback = StubFallback()
    async with TestClient(TestServer(_app(_gateway(fallback, key="k")))) as client:
        resp = await client.post(
            "/v1/chat/completions",
            headers={"Authorization": "Bearer k"},
            json={"model": "auto", "messages": [{"role": "user", "content": "你好"}]},
        )
        assert resp.status == 200
        data = await resp.json()
        assert data["object"] == "chat.completion"
        assert data["choices"][0]["message"]["content"] == "echo:你好"
        assert fallback.last_messages == [{"role": "user", "content": "你好"}]


# ── OpenAI 流式（SSE，含 str/dict 两态）──────────────────


async def test_openai_stream_str_chunks() -> None:
    fallback = StubFallback()
    async with TestClient(TestServer(_app(_gateway(fallback, key="k")))) as client:
        resp = await client.post(
            "/v1/chat/completions",
            headers={"Authorization": "Bearer k"},
            json={"model": "auto", "stream": True, "messages": [{"role": "user", "content": "hi"}]},
        )
        assert resp.status == 200
        text = (await resp.read()).decode("utf-8")
        assert "data: [DONE]" in text
        assert "你" in text and "好" in text  # 每个 chunk 一条 SSE 行，逐字增量


async def test_openai_stream_dict_chunks() -> None:
    # dict 形态 chunk：choices[0].delta.content
    class DictFallback(StubFallback):
        async def achat_completion_stream(self, messages, **kwargs):
            yield {"choices": [{"delta": {"content": "甲"}}]}
            yield {"choices": [{"delta": {"content": "乙"}}]}

    async with TestClient(TestServer(_app(_gateway(DictFallback(), key="k")))) as client:
        resp = await client.post(
            "/v1/chat/completions",
            headers={"Authorization": "Bearer k"},
            json={"model": "auto", "stream": True, "messages": [{"role": "user", "content": "hi"}]},
        )
        assert resp.status == 200
        text = (await resp.read()).decode("utf-8")
        assert "甲" in text and "乙" in text


# ── Anthropic ─────────────────────────────────────────────


async def test_anthropic_nonstream_converts_system_and_message() -> None:
    fallback = StubFallback()
    async with TestClient(TestServer(_app(_gateway(fallback, key="k")))) as client:
        resp = await client.post(
            "/v1/messages",
            headers={"x-api-key": "k", "anthropic-version": "2023-06-01"},
            json={
                "model": "auto",
                "system": [{"type": "text", "text": "你是助手"}],
                "max_tokens": 200,
                "messages": [{"role": "user", "content": "你好"}],
            },
        )
        assert resp.status == 200
        data = await resp.json()
        assert data["type"] == "message"
        assert data["content"][0]["type"] == "text"
        assert data["content"][0]["text"] == "echo:你是助手你好"
        assert fallback.last_messages[0]["role"] == "system"
        assert fallback.last_messages[-1]["content"] == "你好"


async def test_anthropic_missing_max_tokens_defaults() -> None:
    async with TestClient(TestServer(_app(_gateway(key="k")))) as client:
        resp = await client.post(
            "/v1/messages",
            headers={"x-api-key": "k"},
            json={"model": "auto", "messages": [{"role": "user", "content": "hi"}]},
        )
        assert resp.status == 200


async def test_anthropic_stream() -> None:
    async with TestClient(TestServer(_app(_gateway(key="k")))) as client:
        resp = await client.post(
            "/v1/messages",
            headers={"x-api-key": "k"},
            json={"model": "auto", "stream": True, "max_tokens": 100, "messages": [{"role": "user", "content": "hi"}]},
        )
        assert resp.status == 200
        text = (await resp.read()).decode("utf-8")
        assert "event: content_block_start" in text
        assert "event: message_stop" in text
        assert "content_block_delta" in text and "你" in text and "好" in text


# ── 模型映射 ──────────────────────────────────────────────


def test_resolve_auto_models_returns_none() -> None:
    gw = _gateway()
    for name in ("", "auto", "default", None):
        assert gw._resolve_model(name) is None


def test_resolve_model_map_provider_model() -> None:
    fallback = StubFallback()
    gw = _gateway(fallback, key="k", model_map={"my-model": "deepseek/deepseek-v4"})
    client = gw._resolve_model("my-model")
    assert client is not None and getattr(client, "model", None) == "deepseek-v4"


def test_resolve_known_provider_name() -> None:
    fallback = StubFallback()
    prov = _ProviderStub()
    fallback._providers["glm"] = prov
    gw = _gateway(fallback, key="k")
    assert gw._resolve_model("glm") is prov


def test_resolve_unknown_falls_back_to_none() -> None:
    gw = _gateway()
    assert gw._resolve_model("no-such-provider") is None
