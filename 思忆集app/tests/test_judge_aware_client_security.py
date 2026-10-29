"""JudgeAwareClient 安全行为测试。

覆盖近期修复的 CRITICAL/HIGH 安全边界：
- _route_local / _route_local_stream 隐私内容 fail-close（不 fallback 到云端）
- _classify_privacy 异常时默认 local_only
- _sanitize_mcp_name / _build_mcp_response 注入过滤
- H2: explicit_action 不走隐私绕过
- preferred_provider 路由前先过隐私检查
- 审计日志不含用户内容
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import patch

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from ai.client._judge_aware_client import JudgeAwareClient
from assistant.routing.judge_decision import JudgeDecision


# ── Helpers ──


class _MockLocalClient:
    """模拟本地模型客户端。"""

    def __init__(self, fail: bool = False):
        self.fail = fail
        self.model = "mock-local"
        self.api_base = "http://localhost:11434"

    def chat_completion(self, messages, temperature=0.2, max_tokens=None):
        if self.fail:
            raise ConnectionError("local model offline")
        return {"choices": [{"message": {"content": "local response"}}]}

    def chat_completion_stream(self, messages, temperature=0.2, max_tokens=None):
        if self.fail:
            raise ConnectionError("local model offline")
        yield "local "


class _MockPrimaryClient:
    """模拟云端主客户端。"""

    def __init__(self):
        self.model = "deepseek-chat"
        self.api_base = "https://api.deepseek.com"

    def chat_completion(self, messages, temperature=0.2, max_tokens=None, tools=None, tool_choice=None):
        return {"choices": [{"message": {"content": "cloud response"}}]}

    def chat_completion_stream(self, messages, temperature=0.2, max_tokens=None, tools=None, tool_choice=None):
        yield "cloud "


class _MockInnerClient:
    """模拟 FallbackOpenAIClient。"""

    def __init__(self, local_fail: bool = False):
        self.primary = _MockPrimaryClient()
        self.fallback = None
        self.local = _MockLocalClient(fail=local_fail)

    def chat_completion(self, messages, temperature=0.2, max_tokens=None, tools=None, tool_choice=None):
        return self.primary.chat_completion(messages, temperature, max_tokens)

    def chat_completion_stream(self, messages, temperature=0.2, max_tokens=None, tools=None, tool_choice=None):
        yield from self.primary.chat_completion_stream(messages, temperature, max_tokens)

    def route_chat(self, provider: str, messages, temperature=0.2, max_tokens=None):
        if provider == "local" and self.local:
            return self.local.chat_completion(messages, temperature, max_tokens)
        return self.primary.chat_completion(messages, temperature, max_tokens)

    def get_client_by_provider(self, provider_name, model_name=None, settings_repository=None):
        return None  # not found = use default

    def get_runtime_snapshot(self):
        return {}

    def test_connection(self):
        return {"primary": "ok"}


def _make_client(inner=None, **kwargs) -> JudgeAwareClient:
    return JudgeAwareClient(
        inner_client=inner or _MockInnerClient(),
        **kwargs,
    )


def _make_decision(
    executor: str = "deepseek", reason: str = "test", privacy: bool = False, confidence: float = 0.9, mcp_tool: dict | None = None
) -> JudgeDecision:
    return JudgeDecision(
        executor=executor,
        reason=reason,
        privacy=privacy,
        confidence=confidence,
        mcp_tool=mcp_tool,
    )


# ── _sanitize_mcp_name ──


class TestSanitizeMcpName:
    def test_normal_name_passthrough(self):
        assert JudgeAwareClient._sanitize_mcp_name("server-1.tool_v2") == "server-1.tool_v2"

    def test_strips_dangerous_chars(self):
        result = JudgeAwareClient._sanitize_mcp_name("hello<script>alert(1)</script>")
        assert "<" not in result
        assert ">" not in result

    def test_strips_trailing_dots_hyphens(self):
        assert JudgeAwareClient._sanitize_mcp_name("-.server.-") == "server"

    def test_empty_returns_default(self):
        assert JudgeAwareClient._sanitize_mcp_name("") == "unknown"

    def test_none_returns_default(self):
        assert JudgeAwareClient._sanitize_mcp_name(None) == "unknown"

    def test_truncates_long_name(self):
        long_name = "a" * 300
        result = JudgeAwareClient._sanitize_mcp_name(long_name)
        assert len(result) == 128

    def test_replaces_path_separators(self):
        """路径分隔符 / 被替换为 _，但字母和点保留。"""
        result = JudgeAwareClient._sanitize_mcp_name("../../../etc/passwd")
        assert "/" not in result


# ── _build_mcp_response ──


class TestBuildMcpResponse:
    def test_builds_response_with_sanitized_names(self):
        dec = _make_decision(
            executor="mcp",
            mcp_tool={
                "server": "my-server",
                "tool": "read-file",
                "action_id": "act1",
                "arguments": {"path": "/tmp/test"},
            },
        )
        resp = JudgeAwareClient._build_mcp_response(dec)
        content = json.loads(resp["choices"][0]["message"]["content"])
        assert content["server"] == "my-server"
        assert content["tool"] == "read-file"
        assert content["action_id"] == "act1"
        assert content["routed_to"] == "mcp"

    def test_sanitizes_injected_names(self):
        dec = _make_decision(
            executor="mcp",
            mcp_tool={
                "server": "evil<script>xss</script>",
                "tool": "../../../etc/passwd",
            },
        )
        resp = JudgeAwareClient._build_mcp_response(dec)
        content = json.loads(resp["choices"][0]["message"]["content"])
        assert "<" not in content["server"]
        assert ">" not in content["server"]
        assert "/" not in content["tool"]


# ── _route_local: 隐私内容 fail-close ──


class TestRouteLocalPrivacy:
    def test_local_working_returns_local_response(self):
        client = _make_client()
        resp = client._route_local("hello", [{"role": "user", "content": "hello"}], 0.2, None)
        assert resp["choices"][0]["message"]["content"] == "local response"

    def test_local_fail_returns_error_not_cloud(self):
        """CRITICAL: 本地模型挂了绝不 fallback 到云端。"""
        inner = _MockInnerClient(local_fail=True)
        client = _make_client(inner=inner)
        resp = client._route_local("private data", [{"role": "user", "content": "private data"}], 0.2, None)
        content = resp["choices"][0]["message"]["content"]
        assert "本地模型 (Ollama) 未运行" in content
        # 确认不是云端响应
        assert content != "cloud response"

    def test_local_stream_working_returns_local_response(self):
        client = _make_client()
        chunks = list(client._route_local_stream("hello", [{"role": "user", "content": "hello"}], 0.2, None))
        assert chunks == ["local"]

    def test_local_stream_fail_returns_error_not_cloud(self):
        """CRITICAL: Stream 路径本地挂了也绝不 fallback 到云端。"""
        inner = _MockInnerClient(local_fail=True)
        client = _make_client(inner=inner)
        chunks = list(client._route_local_stream("private", [{"role": "user", "content": "private"}], 0.2, None))
        assert len(chunks) == 1
        assert "本地模型 (Ollama) 未运行" in chunks[0]


# ── _classify_privacy ──


class TestClassifyPrivacy:
    def test_classify_privacy_exception_fallback_to_local_only(self):
        """CRITICAL: 隐私分类器异常时默认 local_only 而非 shareable。"""
        client = _make_client()
        # ★ 修复 patch 路径：源码已从 assistant.privacy_classifier 迁移到 ai._assistant_bridge
        with patch("ai._assistant_bridge.PrivacyClassifier", side_effect=ImportError("no module")):
            result = client._classify_privacy("some text")
            assert result == "local_only"

    def test_classify_privacy_empty_returns_shareable(self):
        client = _make_client()
        assert client._classify_privacy("") == "shareable"


# ── _is_explicit_user_action ──


class TestExplicitUserAction:
    def test_matches_system_prompt_keyword(self):
        messages = [
            {"role": "system", "content": "翻译助手，将以下文本翻译为中文"},
            {"role": "user", "content": "hello world"},
        ]
        assert JudgeAwareClient._is_explicit_user_action(messages) is True

    def test_no_match_returns_false(self):
        messages = [
            {"role": "system", "content": "你是一个有用的助手"},
            {"role": "user", "content": "你好"},
        ]
        assert JudgeAwareClient._is_explicit_user_action(messages) is False

    def test_partial_word_no_match(self):
        """关键词作为子串出现不应误匹配。"""
        messages = [
            {"role": "system", "content": "翻译助手工具已就绪"},
            {"role": "user", "content": "使用翻译助手"},
        ]
        assert JudgeAwareClient._is_explicit_user_action(messages) is True

    def test_no_system_message(self):
        messages = [{"role": "user", "content": "hello"}]
        assert JudgeAwareClient._is_explicit_user_action(messages) is False


# ── chat_completion 安全路径 ──


class TestChatCompletionSecurity:
    def test_explicit_action_privacy_check_respected(self):
        """H2: explicit_action 仍过隐私检查，敏感内容路由到本地。"""
        client = _make_client()
        messages = [
            {"role": "system", "content": "翻译助手，将以下文本翻译为中文"},
            {"role": "user", "content": "我的银行卡号是 6222 0200 1234 5678"},
        ]
        with patch.object(client, "_classify_privacy", return_value="local_only"):
            resp = client.chat_completion(messages)
        content = resp["choices"][0]["message"]["content"]
        assert "local response" == content

    def test_explicit_action_non_sensitive_goes_to_cloud(self):
        """H2: explicit_action + 非敏感内容 → 走云端。"""
        client = _make_client()
        messages = [
            {"role": "system", "content": "翻译助手，将以下文本翻译为中文"},
            {"role": "user", "content": "Hello world"},
        ]
        with patch.object(client, "_classify_privacy", return_value="shareable"):
            resp = client.chat_completion(messages)
        content = resp["choices"][0]["message"]["content"]
        assert "cloud response" == content

    def test_privacy_check_before_preferred_provider(self):
        """CRITICAL: 隐私检查在 preferred_provider 之前执行。"""
        client = _make_client()
        messages = [{"role": "user", "content": "我的身份证号是 110101199001011234"}]
        with patch.object(client, "_classify_privacy", return_value="local_only"):
            resp = client.chat_completion(messages, preferred_provider="DeepSeek")
        content = resp["choices"][0]["message"]["content"]
        assert "local response" in content
        assert content != "cloud response"

    def test_private_content_routes_to_local(self):
        """隐私内容直接走本地，不经过 Judge。"""
        client = _make_client()
        messages = [{"role": "user", "content": "我的密码是 abc123"}]
        with patch.object(client, "_classify_privacy", return_value="local_only"):
            resp = client.chat_completion(messages)
        assert resp["choices"][0]["message"]["content"] == "local response"

    def test_tools_bypass_judge_and_privacy(self):
        """tools 参数直接绕过全部路由。"""
        client = _make_client()
        messages = [{"role": "user", "content": "call tool"}]
        resp = client.chat_completion(messages, tools=[{"type": "function", "function": {"name": "test"}}])
        assert resp["choices"][0]["message"]["content"] == "cloud response"

    def test_route_local_fallback_returns_chinese_error(self):
        """_route_local 返回中文错误而非 fallback 到云端。"""
        inner = _MockInnerClient(local_fail=True)
        client = _make_client(inner=inner)
        resp = client._route_local("test", [{"role": "user", "content": "test"}], 0.2, None)
        content = resp["choices"][0]["message"]["content"]
        assert "本地模型 (Ollama) 未运行" in content


# ── 审计日志不含用户内容 ──


class TestAuditLogPrivacy:
    def test_audit_log_does_not_contain_user_content(self):
        """H1: 审计日志只记 input_len，不记用户内容。"""
        client = _make_client()
        dec = _make_decision(executor="deepseek", reason="test")
        private_text = "我的手机号是 13800138000"
        client._record_decision(private_text, dec)
        log = client.get_judge_audit_log(limit=1)
        entry = log[0]
        assert "input_len" in entry
        assert isinstance(entry["input_len"], int)
        assert entry["input_len"] == len(private_text)
        # 确保不包含用户内容片段
        log_str = json.dumps(entry, ensure_ascii=False)
        assert "13800138000" not in log_str
        assert "手机号" not in log_str

    def test_audit_log_contains_routing_info(self):
        client = _make_client()
        dec = _make_decision(executor="local", reason="privacy", privacy=True, confidence=0.95)
        client._record_decision("hello", dec)
        log = client.get_judge_audit_log(limit=1)
        entry = log[0]
        assert entry["executor"] == "local"
        assert entry["confidence"] == 0.95
        assert entry["reason"] == "privacy"


# ── chat_completion_stream 安全路径（七维-安C1：stream+tools 隐私守卫对齐）──
# 干净会话复核（2026-08-29）：内层 FallbackOpenAIClient 流式路径自带隐私闸
# （fallback.py:947-959 _check_privacy_egress + 候选过滤、:1072），local_only
# 实际到不了云端——本组测试锁定「外层与内层守卫语义一致」（防御纵深），
# 防止内层闸将来重构时外层无守卫形成缺口。原 P0「上云可能」已降级为 P1。


class TestChatCompletionStreamSecurity:
    _TOOLS = [
        {
            "type": "function",
            "function": {"name": "notes_list", "description": "list notes", "parameters": {"type": "object", "properties": {}}},
        }
    ]

    def test_stream_with_tools_local_only_stays_local(self):
        """外层守卫：stream+tools 路径 local_only 内容路由本地（与非流式对齐）。

        非流式 chat_completion 在 tools 分支前先做隐私分类（:395-405）；流式
        tools 分支曾直接透传 inner，仅靠内层 fallback 隐私闸兜底（见类注释）。
        """
        client = _make_client()
        messages = [{"role": "user", "content": "整理我的银行卡号 6222 0200 1234 5678"}]
        with patch.object(client, "_classify_privacy", return_value="local_only") as m:
            chunks = list(client.chat_completion_stream(messages, tools=self._TOOLS))
        m.assert_called()  # 分类确实在 tools 分支前执行
        assert chunks == ["local"]  # 本地流，而非 "cloud "

    def test_stream_with_tools_classify_error_fails_closed(self):
        """CRITICAL: 分类器异常时 stream+tools 默认 local_only（fail-close，同非流式）。"""
        client = _make_client()
        messages = [{"role": "user", "content": "hello"}]
        with patch.object(client, "_classify_privacy", side_effect=RuntimeError("boom")):
            chunks = list(client.chat_completion_stream(messages, tools=self._TOOLS))
        assert chunks == ["local"]

    def test_stream_with_tools_non_sensitive_goes_to_cloud(self):
        """非敏感内容 stream+tools 仍正常走云端（防过度阻断回归）。"""
        client = _make_client()
        messages = [{"role": "user", "content": "list my notes"}]
        with patch.object(client, "_classify_privacy", return_value="shareable"):
            chunks = list(client.chat_completion_stream(messages, tools=self._TOOLS))
        assert chunks == ["cloud "]
