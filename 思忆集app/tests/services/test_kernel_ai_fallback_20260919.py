# -*- coding: utf-8 -*-
"""U0 用户模拟测试战役（2026-09-19）· kernel_ai 回退家族修复的机器断言。

实测起源：隔离后端启动即见
    kernel_ai: kernel run not ok (status=failed error=dsh: QUOTA: 429 ...)
    kernel_ai: fallback failed: 'JudgeAwareClient' object has no attribute 'chat'
根因：cron_actions / knowledge_synthesis 的 fallback 闭包调 `ai_client.chat(prompt)`，
而 JudgeAwareClient 的真方法名是 `chat_completion` → 内核失败时回退必 AttributeError。
suggestion_engine._local_fallback 则是 shape 消费错误（choices 不被识别 → 恒空串）。
"""

from pathlib import Path

import pytest

from services.kernel_ai import call_ai_text, kernel_generate

REPO = Path(__file__).resolve().parents[3]


class _ChatCompletionStub:
    """JudgeAwareClient 形状：chat_completion 返回 OpenAI choices 结构。"""

    def __init__(self, content: str = "回退产出文本", fail: bool = False):
        self._content = content
        self._fail = fail
        self.calls: list[dict] = []

    def chat_completion(self, messages: list[dict[str, str]], **kwargs):
        self.calls.append({"messages": messages, **kwargs})
        if self._fail:
            raise RuntimeError("simulated llm outage")
        return {"choices": [{"message": {"content": self._content}}]}


class _CompleteTextStub:
    """宽松客户端端口形状：只有 complete_text。"""

    def complete_text(self, prompt: str) -> str:
        return f"port:{prompt}"


def test_call_ai_text_extracts_choices_content():
    stub = _ChatCompletionStub("你好，回退成功")
    out = call_ai_text(stub, "整理要点", system="你是资讯梳理助手")
    assert out == "你好，回退成功"
    assert stub.calls and stub.calls[0]["messages"][0]["role"] == "system"


def test_call_ai_text_none_and_broken_clients_return_empty():
    assert call_ai_text(None, "x") == ""
    broken = _ChatCompletionStub(fail=True)
    assert call_ai_text(broken, "x") == ""  # 异常吞掉返回空串，不外抛


def test_call_ai_text_complete_text_port():
    assert call_ai_text(_CompleteTextStub(), "p") == "port:p"


def test_kernel_generate_gateway_failure_falls_back_to_call_ai_text():
    class _BadGateway:
        name = "bad"

        def submit(self, *_a, **_k):
            raise RuntimeError("kernel down")

    stub = _ChatCompletionStub("fallback-text")
    out = kernel_generate(_BadGateway(), "prompt", fallback=lambda: call_ai_text(stub, "prompt"))
    assert out == "fallback-text"


def test_kernel_generate_offline_mode_skips_everything(monkeypatch):
    import services.kernel_ai as kai

    monkeypatch.setattr(kai, "is_offline_mode", lambda: True)
    called = {"n": 0}

    def _boom():  # 离线下 fallback 也不许被调用
        called["n"] += 1
        return "should not happen"

    class _Gateway:
        def submit(self, *_a, **_k):
            raise AssertionError("offline mode must not reach kernel")

    assert kernel_generate(_Gateway(), "p", fallback=_boom) == ""
    assert called["n"] == 0


# ── 结构断言：杜绝 `.chat(` 家族回潮（同 P1-B 手法，文本级）─────────────────


def test_no_ai_client_chat_fallback_regression():
    cron = (REPO / "思忆集app" / "services" / "cron_actions.py").read_text(encoding="utf-8")
    ksyn = (REPO / "思忆集app" / "services" / "knowledge_synthesis.py").read_text(encoding="utf-8")
    for name, src in (("cron_actions", cron), ("knowledge_synthesis", ksyn)):
        assert ".ai_client.chat(" not in src, f"{name} 回退闭包回潮为 .chat（JudgeAwareClient 无此方法）"
        assert "call_ai_text" in src, f"{name} 未收敛到 kernel_ai.call_ai_text 标准 fallback"


def test_suggestion_engine_local_fallback_consumes_choices():
    src = (REPO / "思忆集app" / "services" / "suggestion_engine.py").read_text(encoding="utf-8")
    assert 'result.get("choices")' in src, "suggestion_engine._local_fallback 必须识别 chat_completion 的 choices 结构"


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
