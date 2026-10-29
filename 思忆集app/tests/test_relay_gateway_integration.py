"""RelayGateway 集成测试 —— 「真实链路」用户模拟。

不用 StubFallback，而是把 RelayGateway 包在**真** FallbackOpenAIClient 上，
上游挂一个本进程内 aiohttp 桩 OpenAI 服务，验证：模型别名→真实 provider 路由、
auto 走 primary 回退链、SSE 流式、上游失败被翻译成 OpenAI 错误体、以及并发稳定性。
"""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
from typing import Any

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from ai.client.base import OpenAICompatibleClient
from ai.client.fallback import FallbackOpenAIClient
from backend.relay_gateway import RelayGateway

STUB_MODEL = "stub-model"
PRIMARY_MODEL = "primary-model"
_AUTH = {"Authorization": "Bearer relay-secret"}

# 桩上游记录每个请求的 model；默认返回可断言内容，可配置状态码
_recorded: dict[str, list[str]] = {"models": []}


async def _stub_handler(request: web.Request, *, fail_status: int | None = None) -> web.StreamResponse:
    body = await request.json()
    model = body.get("model", "")
    _recorded["models"].append(model)
    if fail_status:
        return web.json_response(
            {"error": {"message": "upstream boom", "type": "rate_limit_error", "code": "rate_limit_exceeded"}},
            status=fail_status,
        )
    if body.get("stream"):
        resp = web.StreamResponse(status=200, headers={"Content-Type": "text/event-stream"})
        await resp.prepare(request)
        for piece in ("甲", "乙"):
            data = {"choices": [{"delta": {"content": piece}, "index": 0}]}
            await resp.write(f"data: {json.dumps(data, ensure_ascii=False)}\n\n".encode("utf-8"))
        await resp.write(b"data: [DONE]\n\n")
        await resp.write_eof()
        return resp
    return web.json_response(
        {
            "id": "stub-1",
            "object": "chat.completion",
            "model": model,
            "choices": [{"index": 0, "message": {"role": "assistant", "content": f"ok:{model}"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 2, "completion_tokens": 3, "total_tokens": 5},
        }
    )


class FakeRepo:
    """最小 settings 仓储：只提供 provider_configs 与 no-op set。"""

    def __init__(self, provider_cfgs: list[dict[str, Any]]) -> None:
        self._p = provider_cfgs

    def get_setting(self, key: str, default: Any = None) -> Any:
        return self._p if key == "provider_configs" else default

    def set_setting(self, key: str, value: Any) -> None:
        pass


def _repo(api_base: str) -> FakeRepo:
    return FakeRepo(
        [
            {
                "name": "stub-proxy",
                "api_base": api_base,
                "api_key": "fake",
                "model": STUB_MODEL,
                "models": [STUB_MODEL],
                "timeout": 10,
                "enabled": True,
                "is_fallback": False,
                "is_local": False,
            }
        ]
    )


async def _build_chain(stub_base: str, repo: FakeRepo, cache_path: str) -> tuple[FallbackOpenAIClient, RelayGateway]:
    primary = OpenAICompatibleClient(api_key="fake", api_base=stub_base, model=PRIMARY_MODEL, timeout=10)
    fallback = FallbackOpenAIClient(primary, cache_path=cache_path)
    fallback._settings_for_providers = repo  # type: ignore[attr-defined]
    gateway = RelayGateway(
        fallback,
        _AUTH["Authorization"].split(" ")[1],
        {"my-alias": f"stub-proxy/{STUB_MODEL}"},
        settings_repository=repo,
    )
    return fallback, gateway


def _relay_app(gateway: RelayGateway) -> web.Application:
    app = web.Application()
    app.router.add_post("/v1/chat/completions", gateway.handle_openai_chat)
    app.router.add_post("/v1/messages", gateway.handle_anthropic_messages)
    return app


async def _make(fail_status: int | None = None) -> tuple[TestServer, TestClient]:
    """启动桩上游 + 中继真实链路。返回 (stub_server, relay_client)。"""
    d = tempfile.mkdtemp()
    cache_path = os.path.join(d, "ai_cache.json")
    _recorded["models"].clear()

    stub = TestServer(web.Application())

    async def handler(request: web.Request) -> web.StreamResponse:
        return await _stub_handler(request, fail_status=fail_status)

    stub.app.router.add_post("/v1/chat/completions", handler)
    await stub.start_server()  # 先启动，才能读到真实 host/port
    v1_base = f"http://{stub.host}:{stub.port}/v1"

    repo = _repo(v1_base)
    _, gateway = await _build_chain(v1_base, repo, cache_path)
    relay = TestServer(_relay_app(gateway))
    relay_client = TestClient(relay)

    await relay.start_server()
    return stub, relay_client


async def _run(relay_client: TestClient, **payload: Any):
    default: dict[str, Any] = {"model": "my-alias", "messages": [{"role": "user", "content": "hi"}]}
    default.update(payload)
    return await relay_client.post("/v1/chat/completions", headers=_AUTH, json=default)


async def test_model_alias_routes_to_real_provider() -> None:
    """用户用别名 my-alias → 真实 provider stub-proxy/stub-model，上游收到 stub-model。"""
    stub, rc = await _make()
    try:
        async with rc:
            resp = await _run(rc)
            assert resp.status == 200
            data = await resp.json()
            assert data["object"] == "chat.completion"
            assert data["choices"][0]["message"]["content"] == f"ok:{STUB_MODEL}"
            assert _recorded["models"][-1] == STUB_MODEL
    finally:
        await stub.close()


async def test_auto_uses_default_primary_chain() -> None:
    """model=auto 走默认回退链 primary → 上游收到 primary-model。"""
    stub, rc = await _make()
    try:
        async with rc:
            resp = await _run(rc, model="default")
            assert resp.status == 200
            data = await resp.json()
            assert data["choices"][0]["message"]["content"] == f"ok:{PRIMARY_MODEL}"
    finally:
        await stub.close()


async def test_alias_streaming_through_real_provider() -> None:
    """流式：别名 → 真实 provider → 上游 SSE → 中继转发，含 [DONE]。"""
    stub, rc = await _make()
    try:
        async with rc:
            resp = await _run(rc, stream=True)
            assert resp.status == 200
            text = (await resp.read()).decode("utf-8")
            assert "甲" in text and "乙" in text
            assert "data: [DONE]" in text
    finally:
        await stub.close()


async def test_unauthorized_and_bad_body() -> None:
    """无效 key 401；错误 JSON → 400；均不崩溃。"""
    stub, rc = await _make()
    try:
        async with rc:
            bad = await rc.post(
                "/v1/chat/completions",
                headers={"Authorization": "Bearer nope"},
                json={"model": "auto", "messages": [{"role": "user", "content": "x"}]},
            )
            assert bad.status == 401
            miss = await rc.post("/v1/chat/completions", headers=_AUTH, json={"model": "auto"})
            assert miss.status == 400
            nauth_anthropic = await rc.post(
                "/v1/messages",
                headers={"x-api-key": "bad"},
                json={"model": "auto", "max_tokens": 10, "messages": [{"role": "user", "content": "x"}]},
            )
            assert nauth_anthropic.status == 401
    finally:
        await stub.close()


async def test_upstream_failure_surfaces_openai_error() -> None:
    """上游 429 → 中继不崩溃，返回 OpenAI 形状错误体（稳定降级）。"""
    stub, rc = await _make(fail_status=429)
    try:
        async with rc:
            resp = await _run(rc, model="auto")
            assert resp.status >= 400
            data = await resp.json()
            assert data["error"]["type"] == "server_error"
            assert data["error"]["code"] == "ai_unavailable"
    finally:
        await stub.close()


async def test_concurrent_stability() -> None:
    """40 并发真实链路调用全部成功、内容正确、无死锁/资源泄漏。"""

    async def call(rc: TestClient, i: int) -> tuple[int, str]:
        r = await _run(rc, model="my-alias")
        return r.status, await r.text()

    stub, rc = await _make()
    try:
        async with rc:
            results = await asyncio.gather(*(call(rc, i) for i in range(40)))
            assert all(s == 200 for s, _ in results)
            assert all(f"ok:{STUB_MODEL}" in b for _, b in results)
            assert _recorded["models"].count(STUB_MODEL) >= 40
    finally:
        await stub.close()
