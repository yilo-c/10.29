"""QUA-12 收紧规则回归锁定（M4a-1）：helpers._is_complex_task / strip_polite_prefix。

锁定 2026-09-12 已入库实现（commit 1664ae4，未激活：chat_route 缺省仍 legacy）。
任一断言变红 = 收紧口径被推翻。
"""
from __future__ import annotations

import pytest

from backend.handlers.chat_paths.helpers import (
    _ACTION_VERBS,
    _MIN_ACTION_LENGTH,
    _is_complex_task,
    strip_polite_prefix,
)


def test_min_action_length_constant_locked():
    assert _MIN_ACTION_LENGTH == 12


def test_greeting_below_threshold_is_simple():
    assert strip_polite_prefix("你好") == "你好"
    assert _is_complex_task("你好") is False


def test_short_help_prefix_stripped_below_threshold():
    assert strip_polite_prefix("帮我开") == "开"
    assert _is_complex_task("帮我开") is False


def test_polite_prefix_stripped_then_verb_hit_is_complex():
    residual = strip_polite_prefix("请帮我总结这份季度业务复盘报告的要点")
    assert residual == "总结这份季度业务复盘报告的要点"
    assert len(residual) >= _MIN_ACTION_LENGTH
    assert _is_complex_task("请帮我总结这份季度业务复盘报告的要点") is True


def test_taskbook_literal_example_below_threshold_is_simple():
    assert strip_polite_prefix("请帮我总结这份报告的要点") == "总结这份报告的要点"
    assert _is_complex_task("请帮我总结这份报告的要点") is False


@pytest.mark.parametrize(
    "message",
    [
        "帮我找一下那个蓝色文件夹里面的东西放哪了",
        "这篇文章值得读几遍才能体会作者写作用意",
        "帮我写一份下周项目里程碑计划的草稿",
        "请读一读这份用户反馈汇总并给出改进建议",
    ],
)
def test_single_char_verbs_no_longer_trigger(message):
    assert _is_complex_task(message) is False


def test_english_verb_table_hit_after_please_strip():
    assert strip_polite_prefix("please check the system logs") == "check the system logs"
    assert _is_complex_task("please check the system logs") is True


def test_english_prefix_requires_word_boundary():
    assert strip_polite_prefix("pleased to meet you today") == "pleased to meet you today"
    assert _is_complex_task("pleased to meet you today") is False


def test_chinese_prefix_directly_attached_verb_stripped_one_layer():
    assert strip_polite_prefix("帮我打开qoder安装目录") == "打开qoder安装目录"
    assert strip_polite_prefix("帮我帮我打开文件") == "帮我打开文件"
    assert strip_polite_prefix("请帮我") == ""


def test_long_message_without_action_verb_is_simple():
    assert len("今天天气真的非常好适合出去走走散步") >= _MIN_ACTION_LENGTH
    assert _is_complex_task("今天天气真的非常好适合出去走走散步") is False


def test_action_verb_table_quarantines_single_char_and_polite_words():
    for banned in ("找", "读", "写", "做", "帮我", "帮忙", "do", "get"):
        assert banned not in _ACTION_VERBS
    for kept in ("总结", "打开", "翻译", "check", "translate"):
        assert kept in _ACTION_VERBS


@pytest.mark.parametrize("message", [None, "", "   "])
def test_none_and_blank_inputs_are_simple(message):
    assert _is_complex_task(message) is False
