"""Tests for JudgeAwareClient — Judge routing + persona injection + FallbackOpenAIClient wrapper."""
from __future__ import annotations

import sys
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

import pytest


class _MockInnerClient:
    """Minimal mock for FallbackOpenAIClient behavior."""

    def __init__(self, fail=False):
        self._fail = fail
        self._last_stream_tool_calls = []
        self.model = "mock-model"

    def chat_completion(self, messages, temperature=0.2, max_tokens=900, tools=None, tool_choice=None):
        if self._fail:
            raise Exception("mock inner failure")
        return {"choices": [{"message": {"content": "inner response"}}], "usage": {"prompt_tokens": 5, "completion_tokens": 10}}

    def chat_completion_stream(self, messages, temperature=0.2, max_tokens=900, tools=None, tool_choice=None):
        if self._fail:
            raise Exception("mock inner stream failure")
        yield "inner "
        yield "stream"

    def chat_completion_vision(self, text, image_base64, image_format="png",
                                system_prompt=None, temperature=0.2, max_tokens=900):
        if self._fail:
            raise Exception("mock vision failure")
        return {"choices": [{"message": {"content": "vision result"}}], "usage": {"prompt_tokens": 8, "completion_tokens": 15}}


class _MockJudge:
    """Minimal Judge mock returning fixed decisions."""

    def __init__(self, executor="deepseek", reason="mock", privacy="shareable", confidence=0.9):
        self.executor = executor
        self.reason = reason
        self.privacy = privacy
        self.confidence = confidence

    def route(self, text: str):
        return self


class TestStripThinkTags:
    """_strip_think_tags — Qwen think block stripping."""

    def test_strips_think_block(self):
        from ai.client._judge_aware_client import _strip_think_tags
        result = _strip_think_tags("<think>internal reasoning</think>actual response")
        assert result == "actual response"

    def test_no_think_block(self):
        from ai.client._judge_aware_client import _strip_think_tags
        result = _strip_think_tags("normal response")
        assert result == "normal response"

    def test_empty_string(self):
        from ai.client._judge_aware_client import _strip_think_tags
        assert _strip_think_tags("") == ""

    def test_nested_think_blocks(self):
        from ai.client._judge_aware_client import _strip_think_tags
        result = _strip_think_tags("<think>first</think>middle<think>second</think>end")
        assert result == "middleend"


class TestExtractUserText:
    """_extract_user_text — last user message extraction."""

    def test_last_user_message(self):
        from ai.client._judge_aware_client import JudgeAwareClient
        messages = [
            {"role": "assistant", "content": "hello"},
            {"role": "user", "content": "my question"},
        ]
        result = JudgeAwareClient._extract_user_text(messages)
        assert result == "my question"

    def test_empty_messages(self):
        from ai.client._judge_aware_client import JudgeAwareClient
        assert JudgeAwareClient._extract_user_text([]) == ""

    def test_no_user_role(self):
        from ai.client._judge_aware_client import JudgeAwareClient
        messages = [{"role": "assistant", "content": "hello"}]
        result = JudgeAwareClient._extract_user_text(messages)
        assert result == "hello"

    def test_single_message(self):
        from ai.client._judge_aware_client import JudgeAwareClient
        result = JudgeAwareClient._extract_user_text([{"role": "user", "content": "hi"}])
        assert result == "hi"


class TestSanitizeMcpName:
    """_sanitize_mcp_name — MCP name sanitization."""

    def test_normal_name_passes(self):
        from ai.client._judge_aware_client import JudgeAwareClient
        result = JudgeAwareClient._sanitize_mcp_name("my-server_tool.1")
        assert result == "my-server_tool.1"

    def test_strips_dangerous_chars(self):
        from ai.client._judge_aware_client import JudgeAwareClient
        result = JudgeAwareClient._sanitize_mcp_name("../evil;rm -rf /")
        assert ".." not in result
        assert ";" not in result

    def test_truncates_long_names(self):
        from ai.client._judge_aware_client import JudgeAwareClient
        long_name = "a" * 200
        result = JudgeAwareClient._sanitize_mcp_name(long_name)
        assert len(result) == 128

    def test_none_default(self):
        from ai.client._judge_aware_client import JudgeAwareClient
        result = JudgeAwareClient._sanitize_mcp_name(None)
        assert result == "unknown"

    def test_empty_default(self):
        from ai.client._judge_aware_client import JudgeAwareClient
        result = JudgeAwareClient._sanitize_mcp_name("")
        assert result == "unknown"


class TestJudgeAwareClientBasics:
    """JudgeAwareClient basic operations without Judge routing."""

    def test_persona_injected_into_chat_completion(self, monkeypatch):
        """Verify inject_xiaowen_persona is called for chat_completion."""
        from ai.client._judge_aware_client import JudgeAwareClient
        injected = []

        def _mock_inject(messages, **kw):
            injected.append((messages, kw))
            if kw.get("inject_persona", True):
                return [{"role": "system", "content": "你"}, *messages]
            return list(messages)

        import ai.client._judge_aware_client as mod
        monkeypatch.setattr(mod, "inject_xiaowen_persona", _mock_inject)

        jac = JudgeAwareClient(inner_client=_MockInnerClient())
        jac.chat_completion([{"role": "user", "content": "hi"}])
        assert len(injected) > 0, "persona should have been injected"

    def test_persona_skipped_for_explicit_action_chat_completion(self, monkeypatch):
        """P1-16 回归：翻译/摘要等显式工具型操作应跳过人格注入。"""
        from ai.client._judge_aware_client import JudgeAwareClient
        captured: list[dict] = []

        def _mock_inject(messages, **kw):
            captured.append(dict(kw))
            if kw.get("inject_persona", True):
                return [{"role": "system", "content": "你是小问。"}, *messages]
            return list(messages)

        import ai.client._judge_aware_client as mod
        monkeypatch.setattr(mod, "inject_xiaowen_persona", _mock_inject)

        jac = JudgeAwareClient(inner_client=_MockInnerClient())
        # "专业翻译助手" 命中 _EXPLICIT_ACTION_KEYWORDS
        jac.chat_completion([
            {"role": "system", "content": "专业翻译助手：将下文翻译成英文。"},
            {"role": "user", "content": "你好世界"},
        ])
        assert captured and captured[0].get("inject_persona") is False, \
            "explicit action 必须以 inject_persona=False 调用 inject_xiaowen_persona"

    def test_persona_skipped_for_explicit_action_stream(self, monkeypatch):
        """P1-16 回归：流式入口同样要跳过显式工具型操作的人格注入。"""
        from ai.client._judge_aware_client import JudgeAwareClient
        captured: list[dict] = []

        def _mock_inject(messages, **kw):
            captured.append(dict(kw))
            return list(messages)

        import ai.client._judge_aware_client as mod
        monkeypatch.setattr(mod, "inject_xiaowen_persona", _mock_inject)

        jac = JudgeAwareClient(inner_client=_MockInnerClient())
        list(jac.chat_completion_stream([
            {"role": "system", "content": "润色助手：把下面这段话润色得更专业。"},
            {"role": "user", "content": "今天天气真好"},
        ]))
        assert captured and captured[0].get("inject_persona") is False, \
            "stream 模式下 explicit action 同样需 inject_persona=False"

    def test_chat_completion_no_tools(self):
        from ai.client._judge_aware_client import JudgeAwareClient
        jac = JudgeAwareClient(inner_client=_MockInnerClient())
        resp = jac.chat_completion([{"role": "user", "content": "hi"}])
        assert resp["choices"][0]["message"]["content"] == "inner response"

    def test_chat_completion_with_tools(self):
        """tools 参数直接绕过 Judge。"""
        from ai.client._judge_aware_client import JudgeAwareClient
        jac = JudgeAwareClient(inner_client=_MockInnerClient())
        resp = jac.chat_completion(
            [{"role": "user", "content": "hi"}],
            tools=[{"type": "function", "function": {"name": "test"}}],
        )
        assert resp["choices"][0]["message"]["content"] == "inner response"

    def test_stream_basic(self):
        from ai.client._judge_aware_client import JudgeAwareClient
        jac = JudgeAwareClient(inner_client=_MockInnerClient())
        chunks = list(jac.chat_completion_stream([{"role": "user", "content": "hi"}]))
        assert "".join(chunks) == "inner stream"

    def test_vision_basic(self):
        from ai.client._judge_aware_client import JudgeAwareClient
        jac = JudgeAwareClient(inner_client=_MockInnerClient())
        resp = jac.chat_completion_vision(text="desc", image_base64="AAAA")
        assert resp["choices"][0]["message"]["content"] == "vision result"

    def test_audit_log_starts_empty(self):
        from ai.client._judge_aware_client import JudgeAwareClient
        jac = JudgeAwareClient(inner_client=_MockInnerClient())
        assert jac.get_judge_audit_log() == []

    def test_build_mcp_response(self):
        from ai.client._judge_aware_client import JudgeAwareClient
        decision = type("D", (), {"mcp_tool": {"server": "my-server", "tool": "my-tool"}})()
        resp = JudgeAwareClient._build_mcp_response(decision)
        assert "routed_to" in resp["choices"][0]["message"]["content"]

    def test_classify_privacy_shareable(self):
        from ai.client._judge_aware_client import JudgeAwareClient
        jac = JudgeAwareClient(inner_client=_MockInnerClient())
        result = jac._classify_privacy("hello world")
        assert result == "syncable"


class TestJudgeAwareClientWithJudge:
    """JudgeAwareClient routing with mocked Judge."""

    def test_judge_deepseek_routing(self):
        from ai.client._judge_aware_client import JudgeAwareClient
        jac = JudgeAwareClient(
            inner_client=_MockInnerClient(),
            judge=_MockJudge(executor="deepseek"),
        )
        resp = jac.chat_completion([{"role": "user", "content": "complex question"}])
        assert resp is not None

    def test_judge_local_routing(self):
        from ai.client._judge_aware_client import JudgeAwareClient
        jac = JudgeAwareClient(
            inner_client=_MockInnerClient(),
            judge=_MockJudge(executor="local"),
        )
        resp = jac.chat_completion([{"role": "user", "content": "simple greeting"}])
        assert resp is not None

    def test_judge_decision_appears_in_audit(self):
        from ai.client._judge_aware_client import JudgeAwareClient
        jac = JudgeAwareClient(
            inner_client=_MockInnerClient(),
            judge=_MockJudge(executor="deepseek", reason="test_reason"),
        )
        jac.chat_completion([{"role": "user", "content": "test audit"}])
        log = jac.get_judge_audit_log()
        assert len(log) > 0
        assert log[0]["executor"] == "deepseek"


    def test_preferred_provider_routing(self):
        from ai.client._judge_aware_client import JudgeAwareClient
        jac = JudgeAwareClient(inner_client=_MockInnerClient())
        resp = jac.chat_completion(
            [{"role": "user", "content": "hi"}],
            preferred_provider="deepseek",
        )
        assert resp["choices"][0]["message"]["content"] == "inner response"


class TestJudgeAwareClientPrivacy:
    """Privacy checks in JudgeAwareClient."""

    def test_privacy_local_only_routes_to_local(self, monkeypatch):
        from ai.client._judge_aware_client import JudgeAwareClient
        from assistant.routing.privacy_classifier import PrivacyClassifier

        class _LocalOnlyClassifier(PrivacyClassifier):
            def classify(self, text):
                return "local_only"

        class _LocalMockClient:
            model = "local"
            def chat_completion(self, messages, **kw):
                return {"choices": [{"message": {"content": "local response"}}], "usage": {}}
            def chat_completion_stream(self, messages, **kw):
                yield "local "

        jac = JudgeAwareClient(inner_client=_LocalMockClient())
        jac._inner._classify_privacy = _LocalOnlyClassifier().classify
        # Should run through local path without crashing
        resp = jac.chat_completion([{"role": "user", "content": "my private data"}])
        assert resp is not None
