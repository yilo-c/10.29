"""ReActEngine 单元测试 — 启发式判断、工具定义、结果格式化、完整循环。"""
from __future__ import annotations

import sys
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from assistant.react_engine import ReActEngine, TOOL_ALLOWLIST, _TRIGGER_KEYWORDS


# ═══════════════════════════════════════════════════════════════
# 辅助 mock
# ═══════════════════════════════════════════════════════════════


def _make_mock_client():
    """返回一个简单的 mock AI client，允许测试注入响应。"""
    return SimpleNamespace(chat_completion=lambda **kw: {"choices": [{"message": {"content": ""}}]})


def _make_mock_registry():
    """返回 mock CapabilityRegistry，能生成 OpenAI 工具定义。"""
    class MockRegistry:
        def build_openai_tools(self):
            return [
                {"type": "function", "function": {"name": "file_read", "description": "读取文件", "parameters": {"type": "object", "properties": {"path": {"type": "string"}}}}},
                {"type": "function", "function": {"name": "file_write", "description": "写入文件", "parameters": {"type": "object", "properties": {"path": {"type": "string"}, "content": {"type": "string"}}}}},
                {"type": "function", "function": {"name": "web_search", "description": "搜索网络", "parameters": {"type": "object", "properties": {"query": {"type": "string"}}}}},
                {"type": "function", "function": {"name": "shell_run", "description": "执行 shell 命令", "parameters": {"type": "object", "properties": {"command": {"type": "string"}}}}},
                {"type": "function", "function": {"name": "app_launch", "description": "启动应用", "parameters": {"type": "object", "properties": {"app": {"type": "string"}}}}},
            ]
    return MockRegistry()


def _make_mock_router():
    """返回 mock CapabilityRouter，记录最后一次 execute 调用。"""
    class MockRouter:
        def __init__(self):
            self.last_action_id = None
            self.last_payload = None
            self.call_count = 0

        def execute(self, step, context=None):
            self.last_action_id = step.action_id
            self.last_payload = step.payload
            self.call_count += 1
            return {"result": f"executed_{step.action_id}", "data": "some content"}
    return MockRouter()


# ═══════════════════════════════════════════════════════════════
# 启发式判断测试
# ═══════════════════════════════════════════════════════════════


class TestNeedsTools:
    def setup_method(self):
        self.engine = ReActEngine(
            primary_client=_make_mock_client(),
            capability_router=_make_mock_router(),
            capability_registry=_make_mock_registry(),
        )

    def test_empty_message(self):
        assert self.engine._needs_tools("") is False

    def test_short_message(self):
        assert self.engine._needs_tools("hi") is False
        assert self.engine._needs_tools("你好") is False

    def test_greeting_skipped(self):
        assert self.engine._needs_tools("你好") is False
        assert self.engine._needs_tools("hello") is False
        assert self.engine._needs_tools("在吗") is False
        assert self.engine._needs_tools("谢谢") is False

    def test_file_keyword_triggers(self):
        assert self.engine._needs_tools("查看桌面文件") is True
        assert self.engine._needs_tools("读取配置文件") is True
        assert self.engine._needs_tools("搜索文件") is True

    def test_web_keyword_triggers(self):
        assert self.engine._needs_tools("搜索今天的AI新闻") is True
        assert self.engine._needs_tools("打开网页") is True
        assert self.engine._needs_tools("查看 http://example.com") is True

    def test_memory_keyword_triggers(self):
        assert self.engine._needs_tools("回忆昨天的对话") is True
        assert self.engine._needs_tools("搜索记忆") is True

    def test_news_keyword_triggers(self):
        assert self.engine._needs_tools("查看新闻简报") is True

    def test_greeting_with_punctuation_still_skipped(self):
        assert self.engine._needs_tools("你好！") is False
        assert self.engine._needs_tools("hello?") is False

    def test_chat_question_skipped(self):
        assert self.engine._needs_tools("今天天气怎么样") is True  # 触发"天气"
        assert self.engine._needs_tools("你是谁") is False  # 纯聊天
        assert self.engine._needs_tools("讲个笑话") is False  # 纯聊天


# ═══════════════════════════════════════════════════════════════
# 工具定义生成测试
# ═══════════════════════════════════════════════════════════════


class TestBuildToolDefs:
    def setup_method(self):
        self.engine = ReActEngine(
            primary_client=_make_mock_client(),
            capability_router=_make_mock_router(),
            capability_registry=_make_mock_registry(),
        )

    def test_allowlist_filters_high_risk_tools(self):
        """shell.run 和 app.launch 应被 allowlist 过滤掉。"""
        defs = self.engine._build_tool_defs()
        names = [d["function"]["name"] for d in defs]
        assert "file_read" in names
        assert "file_write" in names
        assert "web_search" in names
        assert "shell_run" not in names, "高危工具 shell.run 应被过滤"
        assert "app_launch" not in names, "高危工具 app.launch 应被过滤"

    def test_returns_openai_tool_format(self):
        defs = self.engine._build_tool_defs()
        for d in defs:
            assert d["type"] == "function"
            assert "function" in d
            assert "name" in d["function"]
            assert "description" in d["function"]
            assert "parameters" in d["function"]


# ═══════════════════════════════════════════════════════════════
# 工具名称转换测试
# ═══════════════════════════════════════════════════════════════


class TestToolNameToActionId:
    def setup_method(self):
        self.engine = ReActEngine(
            primary_client=_make_mock_client(),
            capability_router=_make_mock_router(),
            capability_registry=_make_mock_registry(),
        )

    def test_simple_conversion(self):
        assert self.engine._tool_name_to_action_id("file_read") == "file.read"
        assert self.engine._tool_name_to_action_id("web_search") == "web.search"

    def test_no_underscore(self):
        assert self.engine._tool_name_to_action_id("ping") == "ping"

    def test_multi_underscore(self):
        """只有第一个下划线应被替换。"""
        assert self.engine._tool_name_to_action_id("some_deep_tool") == "some.deep_tool"


# ═══════════════════════════════════════════════════════════════
# 工具执行测试
# ═══════════════════════════════════════════════════════════════


class TestExecuteTool:
    def setup_method(self):
        self.router = _make_mock_router()
        self.engine = ReActEngine(
            primary_client=_make_mock_client(),
            capability_router=self.router,
            capability_registry=_make_mock_registry(),
        )

    def test_execute_invoke_router(self):
        result = self.engine._execute_tool("file_read", {"path": "/tmp/test.txt"})
        assert self.router.last_action_id == "file.read"
        assert self.router.last_payload == {"path": "/tmp/test.txt"}
        assert self.router.call_count == 1
        assert "result" in result


# ═══════════════════════════════════════════════════════════════
# 结果格式化测试
# ═══════════════════════════════════════════════════════════════


class TestFormatToolResult:
    def setup_method(self):
        self.engine = ReActEngine(
            primary_client=_make_mock_client(),
            capability_router=_make_mock_router(),
            capability_registry=_make_mock_registry(),
        )

    def test_empty_result(self):
        text = self.engine._format_tool_result({})
        assert text == "（空结果）"

    def test_none_result(self):
        text = self.engine._format_tool_result(None)
        assert text == "（空结果）"

    def test_dict_result(self):
        text = self.engine._format_tool_result({"key": "value", "number": 42})
        parsed = json.loads(text)
        assert parsed["key"] == "value"
        assert parsed["number"] == 42

    def test_truncate_long_result(self):
        long_content = "x" * 10000
        result = {"data": long_content}
        text = self.engine._format_tool_result(result)
        assert len(text) <= 8100  # 8000 + truncation message
        assert "truncated" in text


# ═══════════════════════════════════════════════════════════════
# 完整 ReAct 循环测试
# ═══════════════════════════════════════════════════════════════


class TestProcessMessage:
    def test_no_tools_needed_returns_none(self):
        """纯聊天消息应返回 None，让主流程处理。"""
        client = _make_mock_client()
        engine = ReActEngine(client, _make_mock_router(), _make_mock_registry())
        result = engine.process_message("你好")
        assert result is None

    def test_llm_returns_text_directly(self):
        """LLM 不调用工具直接返回文本时，应返回该文本。"""
        client = SimpleNamespace(
            chat_completion=lambda **kw: {"choices": [{"message": {"content": "这是直接回复"}}]}
        )
        engine = ReActEngine(client, _make_mock_router(), _make_mock_registry())
        result = engine.process_message("查看桌面文件")
        assert result == "这是直接回复"

    def test_tool_call_then_text(self):
        """LLM 调用工具 → 执行 → 结果回注 → LLM 返回最终文本。"""
        router = _make_mock_router()
        call_count = [0]
        responses = [
            # 第一次调用：返回 tool_call
            {
                "choices": [{
                    "message": {
                        "content": "",
                        "tool_calls": [
                            {"id": "call_1", "type": "function", "function": {"name": "file_read", "arguments": '{"path": "/tmp/test.txt"}'}},
                        ],
                    },
                }],
            },
            # 第二次调用：返回最终文本
            {
                "choices": [{"message": {"content": "文件内容已读取：some content"}}],
            },
        ]

        def mock_chat(**kw):
            idx = call_count[0]
            call_count[0] += 1
            return responses[idx] if idx < len(responses) else responses[-1]

        client = SimpleNamespace(chat_completion=mock_chat)
        engine = ReActEngine(client, router, _make_mock_registry())
        result = engine.process_message("读取 /tmp/test.txt")
        assert result == "文件内容已读取：some content"
        assert router.call_count == 1
        assert router.last_action_id == "file.read"

    def test_max_iterations_limit(self):
        """超过最大迭代次数应返回已有内容或超限提示。"""
        # LLM 每次都返回 tool_call，永远不会结束
        router = _make_mock_router()

        def always_tool(**kw):
            return {
                "choices": [{
                    "message": {
                        "content": "",
                        "tool_calls": [
                            {"id": "call_x", "type": "function", "function": {"name": "file_read", "arguments": '{"path": "/tmp/x.txt"}'}},
                        ],
                    },
                }],
            }

        client = SimpleNamespace(chat_completion=always_tool)
        engine = ReActEngine(client, router, _make_mock_registry(), max_iterations=3)
        result = engine.process_message("读取文件")
        # 应返回超限提示（tool calls 中没有 content，所以返回默认提示）
        assert result is not None
        assert "最大迭代次数" in result or "中断" in result or "重试" in result or result == "（AI 未能生成回复）"


# ═══════════════════════════════════════════════════════════════
# TOOL_ALLOWLIST 验证
# ═══════════════════════════════════════════════════════════════


class TestToolAllowlist:
    def test_high_risk_not_in_allowlist(self):
        """高危工具不应出现在 allowlist 中。"""
        assert "shell.run" not in TOOL_ALLOWLIST
        assert "app.launch" not in TOOL_ALLOWLIST
        assert "process.kill" not in TOOL_ALLOWLIST
        assert "assistant.self_update" not in TOOL_ALLOWLIST

    def test_safe_tools_in_allowlist(self):
        """只读和常用工具应出现在 allowlist 中。"""
        assert "file.read" in TOOL_ALLOWLIST
        assert "web.search" in TOOL_ALLOWLIST
        assert "memory.recall" in TOOL_ALLOWLIST
        assert "weather.current" in TOOL_ALLOWLIST
        assert "news.briefing" in TOOL_ALLOWLIST

    def test_trigger_keywords_not_empty(self):
        assert len(_TRIGGER_KEYWORDS) > 0
