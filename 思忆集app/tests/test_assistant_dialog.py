"""助手提示词上下文与主窗口辅助方法测试。

测试不依赖真正的 Qt/MainWindow/AI，只验证纯逻辑方法。
通过最小桩类直接测试 PromptContext 和仍保留在 MainWindow 中的辅助方法。
"""

from __future__ import annotations

import sys
import types
from pathlib import Path
from types import SimpleNamespace

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from assistant.profile.prompt_context import PromptContext


class _Stub:
    """可动态挂任意属性的桩对象。"""

    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)

    def __getattr__(self, name):
        return None


def _make_prompt_context(**kwargs):
    """构造 PromptContext 测试桩。"""
    return PromptContext(_Stub(**kwargs))


def test_classify_knowledge_query():
    ctx = _make_prompt_context()
    assert ctx._classify_degraded_query("推荐几个知名SaaS系统") == "knowledge"
    assert ctx._classify_degraded_query("什么是微服务架构？") == "knowledge"
    assert ctx._classify_degraded_query("Python 和 Go 有什么区别") == "knowledge"
    assert ctx._classify_degraded_query("如何学习机器学习") == "knowledge"


def test_classify_local_data_query():
    ctx = _make_prompt_context()
    assert ctx._classify_degraded_query("我的任务有多少") == "local_data"
    assert ctx._classify_degraded_query("我的笔记汇总") == "local_data"


def test_classify_unknown_short():
    ctx = _make_prompt_context()
    assert ctx._classify_degraded_query("你好") == "unknown"
    assert ctx._classify_degraded_query("嗯") == "unknown"


# ════════════════ 测试：错误提示格式化 ════════════════


def _error_hint(error_text):
    return PromptContext._format_degraded_error_hint(error_text)


def test_format_degraded_error_hint_timeout():
    assert "超时" in _error_hint("Connection timeout after 10s")


def test_format_degraded_error_hint_auth():
    assert "密钥" in _error_hint("HTTP 401 Unauthorized")


def test_format_degraded_error_hint_rate_limit():
    assert "频率" in _error_hint("429 Too Many Requests")


def test_format_degraded_error_hint_fallback_exhausted():
    assert "不可用" in _error_hint("fallback_exhausted: all providers failed")


def test_format_degraded_error_hint_generic():
    hint = _error_hint("some random error")
    assert hint  # 应返回非空字符串


# ════════════════ 测试：token 估算 ════════════════


def test_estimate_token_count_chinese():
    ctx = _make_prompt_context()
    count = ctx._estimate_token_count("你好世界")
    assert count == 8  # 4 CJK chars × 2


def test_estimate_token_count_english():
    ctx = _make_prompt_context()
    count = ctx._estimate_token_count("hello world")
    assert count > 0
    assert count < 10  # 11 ascii chars × 0.35 ≈ 3.85


def test_estimate_token_count_empty():
    ctx = _make_prompt_context()
    assert ctx._estimate_token_count("") == 0
    assert ctx._estimate_token_count(None) == 0


# ════════════════ 测试：历史裁剪 ════════════════


def test_trim_history_respects_budget():
    ctx = _make_prompt_context()
    # 每条消息约 200 CJK chars = 400 tokens; budget=2400, 最多 6 条
    ctx._mw.chat_history_entries = [
        {"speaker": "你", "message": "问" * 200},
        {"speaker": "助手", "message": "答" * 200},
    ] * 10  # 20 条
    result = ctx._trim_chat_history_by_budget(None)
    # 应该被裁剪到远少于 20 条
    assert len(result) < 20
    assert len(result) > 0


def test_trim_history_aligns_to_user_boundary():
    ctx = _make_prompt_context()
    ctx._mw.chat_history_entries = [
        {"speaker": "助手", "message": "开场白"},
        {"speaker": "你", "message": "第一个问题"},
        {"speaker": "助手", "message": "第一个回答"},
        {"speaker": "你", "message": "第二个问题"},
        {"speaker": "助手", "message": "第二个回答"},
    ]
    result = ctx._trim_chat_history_by_budget(None)
    # 第一条不应该是助手
    assert result[0]["speaker"] == "你"


def test_trim_history_empty():
    ctx = _make_prompt_context()
    ctx._mw.chat_history_entries = []
    assert ctx._trim_chat_history_by_budget(None) == []


# ════════════════ 测试：能力列表与提示词摘要 ════════════════


def test_build_capability_summary_for_prompt():
    ctx = _make_prompt_context()
    summary = ctx.build_capability_summary_for_prompt()
    assert "可按主题生成技术周报并邮件发送" in summary
    assert "可生成每日晨报" in summary
    assert "可用联网搜索补全最新信息" in summary


def test_build_capability_reply_mentions_high_value_capabilities():
    ctx = _make_prompt_context()
    reply = ctx.build_capability_reply()
    assert "邮件新闻推送" in reply
    assert "技术周报" in reply
    assert "联网搜索" in reply


def test_list_assistant_capabilities_with_registry():
    ctx = _make_prompt_context(
        _assistant_tool_registry=SimpleNamespace(
            preview_labels=lambda limit=6: ["任务窗口", "笔记窗口"]
        )
    )
    labels = ctx.list_assistant_capabilities()
    assert "邮件新闻推送" in labels
    assert "任务窗口" in labels


# ════════════════ 测试：聊天报告 ════════════════


def test_build_chat_report_includes_capabilities():
    ctx = _make_prompt_context(
        _assistant_tool_registry=SimpleNamespace(preview_labels=lambda limit=6: ["任务窗口", "笔记窗口", "新闻窗口"])
    )
    report = ctx.build_chat_report({"last_stage": "local"}, {"recent_execution_results": []})
    assert "当前可用能力" in report["lines"][2]


# ════════════════ 测试：降级回答 ════════════════


def test_build_smart_degraded_answer_for_local_data():
    ctx = _make_prompt_context()
    snapshot = {
        "pending_tasks": [{"name": "任务A"}],
        "notes": [{"title": "笔记A"}],
        "inbox_entries": [],
    }
    answer = ctx.build_smart_degraded_answer("我的任务有多少", snapshot, Exception("timeout"))
    assert "待办" in answer
    assert "任务A" in answer


def test_build_smart_degraded_answer_for_knowledge():
    ctx = _make_prompt_context()
    snapshot = {"pending_tasks": [], "notes": [], "inbox_entries": []}
    answer = ctx.build_smart_degraded_answer("什么是 Docker", snapshot, Exception("timeout"))
    assert "无法回答" in answer
    assert "稍后重试" in answer


# ════════════════ 测试：默认下一步操作 ════════════════


def test_build_default_next_actions_non_empty():
    ctx = _make_prompt_context()
    actions = ctx.build_default_next_actions("test")
    assert isinstance(actions, list)
    assert len(actions) > 0


# ════════════════ 测试：仪表盘快照 ════════════════


