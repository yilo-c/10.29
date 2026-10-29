import asyncio
import json
import os
import tempfile

import requests

from automation_hub.agent.llm_client import FallbackOpenAIClient, LLMServiceError, OpenAICompatibleClient
import agent.llm_client as llm_client


class _FakeResponse:
    def __init__(self, status_code=200, payload=None, text="", headers=None):
        self.status_code = status_code
        self._payload = payload
        self._text = text
        self.headers = headers or {}

    @property
    def text(self):
        return self._text

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code} Error", response=self)

    def json(self):
        if self._payload is not None:
            return self._payload
        raise ValueError("no json payload")


class _FakeRequests:
    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = 0

    def post(self, *args, **kwargs):
        self.calls += 1
        if not self._responses:
            raise RuntimeError("no more fake responses")
        return self._responses.pop(0)


class _AlwaysFailPrimary:
    async def chat_completion(self, *args, **kwargs):
        raise LLMServiceError(
            "模拟主服务失败: 429 Too Many Requests",
            transient=True,
            status=429,
            retry_after_seconds=1.25,
        )


class _AlwaysFailLocal:
    async def chat_completion(self, *args, **kwargs):
        raise LLMServiceError("模拟本地失败: 401 Unauthorized", transient=False, status=401)


def _make_client_with_fake_requests(fake_requests):
    old_post = llm_client.requests.post
    llm_client.requests.post = fake_requests.post
    os.environ.setdefault("OPENAI_API_KEY", "dummy-key")
    client = OpenAICompatibleClient(api_key="dummy-key", base_url="https://example.com/v1", model="m")
    return client, old_post


async def _case_retry_after_and_transient_retry():
    fake_requests = _FakeRequests([
        _FakeResponse(status_code=429, text='{"error":{"message":"rate limit"}}', headers={"Retry-After": "2"}),
        _FakeResponse(status_code=200, payload={"choices": [{"message": {"content": "OK"}}]}),
    ])

    sleeps = []
    old_sleep = llm_client.asyncio.sleep
    llm_client.asyncio.sleep = lambda delay: _fake_sleep(delay, sleeps)

    client, old_post = _make_client_with_fake_requests(fake_requests)
    try:
        resp = await client.chat_completion([
            {"role": "system", "content": "test"},
            {"role": "user", "content": "hello"},
        ])
        assert resp["choices"][0]["message"]["content"] == "OK"
        assert fake_requests.calls == 2
        assert len(sleeps) == 1
        assert sleeps[0] >= 2.0
    finally:
        llm_client.requests.post = old_post
        llm_client.asyncio.sleep = old_sleep


async def _fake_sleep(delay, collector):
    collector.append(delay)


async def _case_non_transient_no_retry():
    fake_requests = _FakeRequests([
        _FakeResponse(status_code=401, text='{"error":{"message":"invalid api key"}}'),
    ])
    client, old_post = _make_client_with_fake_requests(fake_requests)
    try:
        try:
            await client.chat_completion([
                {"role": "system", "content": "test"},
                {"role": "user", "content": "hello"},
            ])
            raise AssertionError("expected LLMServiceError")
        except LLMServiceError as exc:
            assert exc.transient is False
            assert exc.status == 401
            assert fake_requests.calls == 1
    finally:
        llm_client.requests.post = old_post


async def _case_fallback_transient_propagation():
    with tempfile.TemporaryDirectory() as td:
        cache_path = os.path.join(td, "agent_ai_cache.json")
        client = FallbackOpenAIClient(primary=_AlwaysFailPrimary(), local=_AlwaysFailLocal(), cache_path=cache_path)
        try:
            await client.chat_completion([
                {"role": "system", "content": "test"},
                {"role": "user", "content": "hello"},
            ])
            raise AssertionError("expected LLMServiceError")
        except LLMServiceError as exc:
            assert exc.transient is True
            assert exc.status == 429
            assert exc.retry_after_seconds == 1.25


async def _case_fallback_cache_hit():
    with tempfile.TemporaryDirectory() as td:
        cache_path = os.path.join(td, "agent_ai_cache.json")
        client = FallbackOpenAIClient(primary=_AlwaysFailPrimary(), local=None, cache_path=cache_path)
        messages = [
            {"role": "system", "content": "test"},
            {"role": "user", "content": "hello"},
        ]
        key = client._messages_key(messages)
        client._store_cached(key, "来自缓存")
        resp = await client.chat_completion(messages)
        assert resp["choices"][0]["message"]["content"] == "来自缓存"


async def _run_all():
    cases = [
        ("retry_after_and_transient_retry", _case_retry_after_and_transient_retry),
        ("non_transient_no_retry", _case_non_transient_no_retry),
        ("fallback_transient_propagation", _case_fallback_transient_propagation),
        ("fallback_cache_hit", _case_fallback_cache_hit),
    ]
    results = []
    for name, fn in cases:
        try:
            await fn()
            results.append({"name": name, "status": "passed"})
        except Exception as exc:
            results.append({"name": name, "status": "failed", "error": str(exc)})
    failed = [r for r in results if r["status"] == "failed"]
    print(json.dumps({"summary": {"total": len(results), "failed": len(failed)}, "results": results}, ensure_ascii=False))


if __name__ == "__main__":
    asyncio.run(_run_all())
