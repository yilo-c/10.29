"""P2-17 回归：翻译任务优先路由到本地 qwen。

之前任何含 "翻译"/"translate" 关键词的请求都走 DeepSeek，浪费 token。
修复后：
- 短翻译（≤200 字符，可由 ``SIYI_TRANSLATE_LOCAL_MAX_CHARS`` 覆盖）→ 本地 qwen
- 长翻译/复杂格式 → 仍走 DeepSeek
- 阈值 0 关闭本地优先（回到旧行为）
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from assistant import judge as _judge_mod
from assistant.routing.judge import (
    JudgeScheduler,
    _is_short_translation,
    _resolve_local_translate_max_chars,
)
from assistant.routing.judge_decision import (
    ROUTE_EXECUTOR_DEEPSEEK,
    ROUTE_EXECUTOR_LOCAL,
)


# ── helper 函数 ──


def test_default_threshold_is_200(monkeypatch) -> None:
    monkeypatch.delenv("SIYI_TRANSLATE_LOCAL_MAX_CHARS", raising=False)
    assert _resolve_local_translate_max_chars() == 200


def test_env_override_threshold(monkeypatch) -> None:
    monkeypatch.setenv("SIYI_TRANSLATE_LOCAL_MAX_CHARS", "50")
    assert _resolve_local_translate_max_chars() == 50


def test_invalid_threshold_falls_back(monkeypatch) -> None:
    monkeypatch.setenv("SIYI_TRANSLATE_LOCAL_MAX_CHARS", "abc")
    assert _resolve_local_translate_max_chars() == 200


def test_zero_disables_local_preference(monkeypatch) -> None:
    monkeypatch.setenv("SIYI_TRANSLATE_LOCAL_MAX_CHARS", "0")
    assert _is_short_translation("翻译 hello") is False


def test_recognises_chinese_keyword(monkeypatch) -> None:
    monkeypatch.delenv("SIYI_TRANSLATE_LOCAL_MAX_CHARS", raising=False)
    assert _is_short_translation("帮我翻译 hello world 成中文") is True


def test_recognises_english_keyword(monkeypatch) -> None:
    monkeypatch.delenv("SIYI_TRANSLATE_LOCAL_MAX_CHARS", raising=False)
    assert _is_short_translation("translate this short text") is True


def test_long_translation_is_not_short(monkeypatch) -> None:
    monkeypatch.setenv("SIYI_TRANSLATE_LOCAL_MAX_CHARS", "10")
    assert _is_short_translation("翻译这段很长很长的文本到英文") is False


def test_non_translation_text_is_ignored() -> None:
    assert _is_short_translation("你好啊") is False
    assert _is_short_translation("帮我写代码") is False


# ── 集成：通过 JudgeScheduler 路由路径验证 ──


def _make_judge(monkeypatch) -> JudgeScheduler:
    """构造一个不依赖真实 LLM 的 JudgeScheduler。"""
    monkeypatch.delenv("SIYI_TRANSLATE_LOCAL_MAX_CHARS", raising=False)
    ai_client = MagicMock()
    ai_client.chat_completion.return_value = ""
    judge = JudgeScheduler(ai_client=ai_client, local_router=None)
    return judge


def test_short_translation_takes_local_precheck(monkeypatch) -> None:
    judge = _make_judge(monkeypatch)
    decision = judge.route("翻译 hello world 成中文")
    assert decision.executor == ROUTE_EXECUTOR_LOCAL
    assert "translation" in decision.reason


def test_long_translation_falls_through_to_deepseek(monkeypatch) -> None:
    """超过阈值的翻译应保留在 DeepSeek 路径上（不命中本地预检）。"""
    monkeypatch.setenv("SIYI_TRANSLATE_LOCAL_MAX_CHARS", "10")
    ai_client = MagicMock()
    ai_client.chat_completion.return_value = ""
    judge = JudgeScheduler(ai_client=ai_client, local_router=None)

    decision = judge.route("翻译这段非常非常非常非常长的文本到英文")
    # 本地预检不会命中（>阈值），LLM 路由返回空，规则 fallback 应该路由 DeepSeek
    assert decision.executor == ROUTE_EXECUTOR_DEEPSEEK
    assert "translation" in decision.reason


def test_rule_fallback_routes_short_translation_to_local(monkeypatch) -> None:
    """如果跳过 LLM 路由，规则 fallback 也应该把短翻译路由到本地。"""
    monkeypatch.delenv("SIYI_TRANSLATE_LOCAL_MAX_CHARS", raising=False)
    # ai_client=None → 跳过 LLM 路由，强制走规则 fallback
    judge = JudgeScheduler(ai_client=None, local_router=None)

    # 由于无 LLM，文本"翻译 yes" 会先命中 _route_local_if_simple 的短翻译分支
    decision = judge.route("翻译 yes")
    assert decision.executor == ROUTE_EXECUTOR_LOCAL
