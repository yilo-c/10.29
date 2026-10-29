"""思考型模型流式 reasoning 兜底（2026-10-16）。

背景：4 个流式循环只处理 delta.content，qwen3 thinking / deepseek-v4-* 的
delta.reasoning_content 被静默丢弃——推理耗尽 max_tokens 时 content 恒空，
上层收到零文本 → idle 超时 / 误判失败（简报探针截断同族）。修复 =
_ReasoningTextBuffer：缓冲 reasoning，流结束仍无 content 时整段补发。

覆盖：buffer 单元 + stream_chat 端到端（mock httpx，同
test_openai_client_stream_chain 手法）+ 4 点接线门禁。
"""

from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from ai.client.base import OpenAICompatibleClient, _ReasoningTextBuffer  # noqa: E402


def _sse(obj: dict) -> str:
    import json

    return f"data: {json.dumps(obj, ensure_ascii=False)}"


# ── buffer 单元 ──────────────────────────────────────────────


class TestReasoningTextBuffer:
    def test_content_passthrough(self):
        buf = _ReasoningTextBuffer()
        assert buf.feed({"content": "正文"}) == "正文"
        assert buf.fallback_text() == ""

    def test_reasoning_buffered_not_emitted(self):
        buf = _ReasoningTextBuffer()
        assert buf.feed({"reasoning_content": "推理一"}) is None
        assert buf.feed({"reasoning_content": "推理二"}) is None
        assert buf.fallback_text() == "推理一推理二"

    def test_content_after_reasoning_suppresses_fallback(self):
        # 正常思考型：先推理后正文 → 不补发（正文才是答案）
        buf = _ReasoningTextBuffer()
        buf.feed({"reasoning_content": "思考"})
        assert buf.feed({"content": "答案"}) == "答案"
        assert buf.fallback_text() == ""

    def test_empty_delta_ignored(self):
        buf = _ReasoningTextBuffer()
        assert buf.feed({}) is None
        assert buf.feed({"reasoning_content": ""}) is None
        assert buf.fallback_text() == ""


# ── stream_chat 端到端（mock httpx）─────────────────────────


class _FakeResp:
    def __init__(self, lines: list[str]) -> None:
        self.status_code = 200
        self.headers: dict[str, str] = {}
        self._lines = lines

    def iter_lines(self):
        return iter(self._lines)

    def close(self) -> None:
        pass


class _FakeStreamCtx:
    def __init__(self, response: _FakeResp) -> None:
        self._response = response

    def __enter__(self) -> _FakeResp:
        return self._response

    def __exit__(self, *args: object) -> None:
        return None


class _FakeClient:
    def __init__(self, response: _FakeResp) -> None:
        self._response = response
        self.closed = False

    def stream(self, *args: object, **kwargs: object) -> _FakeStreamCtx:
        return _FakeStreamCtx(self._response)

    def close(self) -> None:
        self.closed = True

    @property
    def is_closed(self) -> bool:
        return self.closed


def _client() -> OpenAICompatibleClient:
    return OpenAICompatibleClient(
        api_base="https://example.invalid",
        api_key="sk-test",
        model="qwen3:4b",
        timeout=1,
        max_retries=1,
    )


def test_stream_chat_reasoning_only_yields_fallback(monkeypatch):
    """全程只有 reasoning_content（推理耗尽 max_tokens 的真实形态）→ 补发推理文本。"""
    lines = [
        _sse({"choices": [{"delta": {"reasoning_content": "先想"}}]}),
        _sse({"choices": [{"delta": {"reasoning_content": "再想"}}]}),
        "data: [DONE]",
    ]
    client = _client()
    monkeypatch.setattr(client, "_sync_client", _FakeClient(_FakeResp(lines)))
    out = list(client.stream_chat([{"role": "user", "content": "hi"}]))
    assert out == ["先想再想"]


def test_stream_chat_content_after_reasoning_no_fallback(monkeypatch):
    lines = [
        _sse({"choices": [{"delta": {"reasoning_content": "思考"}}]}),
        _sse({"choices": [{"delta": {"content": "答案"}}]}),
        "data: [DONE]",
    ]
    client = _client()
    monkeypatch.setattr(client, "_sync_client", _FakeClient(_FakeResp(lines)))
    out = list(client.stream_chat([{"role": "user", "content": "hi"}]))
    assert out == ["答案"]


# ── 4 点接线门禁 ─────────────────────────────────────────────


def test_four_stream_sites_wired():
    src = (_ROOT / "ai" / "client" / "base.py").read_text(encoding="utf-8")
    assert src.count("rbuf = _ReasoningTextBuffer()") == 4
    assert src.count(".fallback_text()") == 4
