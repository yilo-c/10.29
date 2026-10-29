"""P1-13 回归：OpenAICompatibleClient.stream_chat / stream_chat_with_tools

确保连接阶段重试耗尽后抛出的 AIServiceError 始终保留根因（__cause__），
避免出现 `last_error is None` 时丢失原始异常的情况。

迁移到 httpx 后，测试通过 mock ``OpenAICompatibleClient._get_sync_client``
注入 fake httpx.Client，避免依赖具体 HTTP 库的全局 monkeypatch。
"""
from __future__ import annotations

import sys
from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from ai.client.base import OpenAICompatibleClient  # noqa: E402
from ai.types import AIServiceError  # noqa: E402


class _FakeResp:
    def __init__(self, status_code: int = 200, text: str = "") -> None:
        self.status_code = status_code
        self.headers: dict[str, str] = {}
        self.text = text
        self._lines: list[str] = []

    def json(self) -> dict:
        return {"error": {"message": self.text}}

    def iter_lines(self) -> Iterator[str]:
        return iter(self._lines)

    def close(self) -> None:
        pass


class _StreamResp(_FakeResp):
    def __init__(self, lines: list[str]) -> None:
        super().__init__(status_code=200)
        self._lines = lines


class _FakeStreamCtx:
    def __init__(self, response: _FakeResp) -> None:
        self._response = response

    def __enter__(self) -> _FakeResp:
        return self._response

    def __exit__(self, *args: object) -> None:
        return None


class _FakeClient:
    """Minimal httpx.Client 替身，仅支持 stream 方法。"""

    def __init__(self, stream_response: _FakeResp | None = None, stream_exc: BaseException | None = None) -> None:
        self._stream_response = stream_response
        self._stream_exc = stream_exc
        self.closed = False

    def stream(self, *args: object, **kwargs: object) -> _FakeStreamCtx:
        if self._stream_exc is not None:
            raise self._stream_exc
        return _FakeStreamCtx(self._stream_response)

    def close(self) -> None:
        self.closed = True

    @property
    def is_closed(self) -> bool:
        return self.closed


def _client(max_retries: int = 2) -> OpenAICompatibleClient:
    return OpenAICompatibleClient(
        api_base="https://example.invalid",
        api_key="sk-test",
        model="x",
        timeout=1,
        max_retries=max_retries,
    )


def _patch_sync_client(monkeypatch, client: OpenAICompatibleClient, fake: _FakeClient) -> None:
    monkeypatch.setattr(client, "_sync_client", fake)


# ── stream_chat ───────────────────────────────────────────────


def test_stream_chat_timeout_preserves_root_cause(monkeypatch):
    """所有尝试都 Timeout 时，最终抛出的 AIServiceError 应链回 httpx.TimeoutException。"""
    calls = {"n": 0}

    def fake_stream(*args: object, **kwargs: object) -> _FakeStreamCtx:
        calls["n"] += 1
        raise httpx.TimeoutException("simulated timeout")

    client = _client(max_retries=2)
    fake_client = _FakeClient()
    monkeypatch.setattr(fake_client, "stream", fake_stream)
    _patch_sync_client(monkeypatch, client, fake_client)
    monkeypatch.setattr("ai.client.base.time.sleep", lambda *_a, **_k: None)

    with pytest.raises(AIServiceError) as exc:
        list(client.stream_chat([{"role": "user", "content": "hi"}]))

    assert calls["n"] == 2
    assert isinstance(exc.value.__cause__, httpx.TimeoutException)


def test_stream_chat_connection_error_preserves_root_cause(monkeypatch):
    def fake_stream(*args: object, **kwargs: object) -> _FakeStreamCtx:
        raise httpx.ConnectError("conn refused")

    client = _client(max_retries=2)
    fake_client = _FakeClient()
    monkeypatch.setattr(fake_client, "stream", fake_stream)
    _patch_sync_client(monkeypatch, client, fake_client)
    monkeypatch.setattr("ai.client.base.time.sleep", lambda *_a, **_k: None)

    with pytest.raises(AIServiceError) as exc:
        list(client.stream_chat([{"role": "user", "content": "hi"}]))

    assert isinstance(exc.value.__cause__, httpx.ConnectError)


def test_stream_chat_zero_retries_raises_explicit_error(monkeypatch):
    """max_retries=0 时 for 循环不执行；应抛出明确错误而非 NameError/AttributeError。"""
    client = _client(max_retries=0)
    # 预置 fake client，避免真实构造 httpx.Client；循环体不会执行。
    monkeypatch.setattr(client, "_sync_client", _FakeClient())

    with pytest.raises(AIServiceError) as exc:
        list(client.stream_chat([{"role": "user", "content": "hi"}]))

    assert "no attempts made" in str(exc.value)


def test_stream_chat_success_after_retry(monkeypatch):
    """前一次 Timeout、第二次成功：应该正常 yield 内容，不抛错。"""
    attempts = {"n": 0}

    def fake_stream(*args: object, **kwargs: object) -> _FakeStreamCtx:
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise httpx.TimeoutException("first attempt times out")
        return _FakeStreamCtx(
            _StreamResp([
                'data: {"choices":[{"delta":{"content":"hello"}}]}',
                "data: [DONE]",
            ])
        )

    client = _client(max_retries=3)
    fake_client = _FakeClient()
    monkeypatch.setattr(fake_client, "stream", fake_stream)
    _patch_sync_client(monkeypatch, client, fake_client)
    monkeypatch.setattr("ai.client.base.time.sleep", lambda *_a, **_k: None)

    chunks = list(client.stream_chat([{"role": "user", "content": "hi"}]))
    assert chunks == ["hello"]
    assert attempts["n"] == 2


# ── stream_chat_with_tools ────────────────────────────────────


def test_stream_chat_with_tools_timeout_preserves_root_cause(monkeypatch):
    def fake_stream(*args: object, **kwargs: object) -> _FakeStreamCtx:
        raise httpx.TimeoutException("simulated")

    client = _client(max_retries=2)
    fake_client = _FakeClient()
    monkeypatch.setattr(fake_client, "stream", fake_stream)
    _patch_sync_client(monkeypatch, client, fake_client)
    monkeypatch.setattr("ai.client.base.time.sleep", lambda *_a, **_k: None)

    with pytest.raises(AIServiceError) as exc:
        list(client.stream_chat_with_tools([{"role": "user", "content": "hi"}]))

    assert isinstance(exc.value.__cause__, httpx.TimeoutException)


def test_stream_chat_with_tools_zero_retries_explicit(monkeypatch):
    client = _client(max_retries=0)
    # 预置 fake client，避免真实构造 httpx.Client；循环体不会执行。
    monkeypatch.setattr(client, "_sync_client", _FakeClient())

    with pytest.raises(AIServiceError) as exc:
        list(client.stream_chat_with_tools([{"role": "user", "content": "hi"}]))

    # 既有逻辑：无响应分支
    assert "no response" in str(exc.value).lower() or "no attempts" in str(exc.value).lower()


# ── chat_completion_vision payload 构造 ──

def test_chat_completion_vision_builds_openai_vision_payload(monkeypatch):
    """验证 chat_completion_vision 构造出标准 OpenAI vision 格式（image_url data URL）。"""
    client = _client()
    captured: dict = {}

    def _fake_request(payload, *, timeout=None):
        captured.update(payload)
        return {"choices": [{"message": {"content": "a desk screenshot"}}], "usage": {}}

    monkeypatch.setattr(client, "_request", _fake_request)
    resp = client.chat_completion_vision(
        text="describe this", image_base64="AAAA", image_format="jpg",
        system_prompt="you are a vision helper", max_tokens=300,
    )

    assert resp["content"] == "a desk screenshot"
    msgs = captured["messages"]
    assert msgs[0]["role"] == "system" and msgs[0]["content"] == "you are a vision helper"
    user = msgs[1]
    assert user["role"] == "user"
    # content 是多模态数组：text + image_url
    parts = user["content"]
    assert isinstance(parts, list) and len(parts) == 2
    assert parts[0] == {"type": "text", "text": "describe this"}
    assert parts[1]["type"] == "image_url"
    # jpg 归一化为 jpeg
    assert parts[1]["image_url"]["url"] == "data:image/jpeg;base64,AAAA"
    assert captured["max_tokens"] == 300


def test_chat_completion_vision_png_format_and_no_system(monkeypatch):
    client = _client()
    captured: dict = {}

    def _fake_request(payload, *, timeout=None):
        captured.update(payload)
        return {"choices": [{"message": {"content": "ok"}}], "usage": {}}

    monkeypatch.setattr(client, "_request", _fake_request)
    client.chat_completion_vision(text="t", image_base64="BBBB", image_format="png")

    msgs = captured["messages"]
    # 未传 system_prompt 时不应有 system 消息
    assert msgs[0]["role"] == "user"
    assert msgs[0]["content"][1]["image_url"]["url"] == "data:image/png;base64,BBBB"
