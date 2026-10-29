# -*- coding: utf-8 -*-
"""QUA-12 规则收紧：_is_complex_task 边界样例（≥10 条）+ 新旧规则前后对比。

新规则三要点：阈值 _MIN_ACTION_LENGTH 4→12；动词表删单字动词（找/读/写/做）
与礼貌前缀词（帮我/帮忙）；判定前先剥离句首礼貌前缀；拿不准一律判简单。
**默认路由不改（chat_route 仍 legacy）**——本函数仅在 auto 分层时被调用。

OLD 列 = 旧规则（阈值 4 + 旧动词表，含帮我/找/读/写/做）下的判定，用于
前后对比；NEW 列 = 收紧后期望判定（快档优先）。
"""
from __future__ import annotations

import pytest

from backend.handlers.chat_paths.helpers import (
    _ACTION_VERBS,
    _MIN_ACTION_LENGTH,
    _is_complex_task,
    strip_polite_prefix,
)


def test_threshold_is_tightened_to_12():
    assert _MIN_ACTION_LENGTH == 12


def test_single_char_and_polite_verbs_removed():
    for gone in ("找", "读", "写", "做", "帮我", "帮忙", "help", "do", "get", "find", "read", "write", "run"):
        assert gone not in _ACTION_VERBS
    # 明确双字动作词仍在
    for kept in ("打开", "创建", "搜索", "修复", "下载"):
        assert kept in _ACTION_VERBS


# (消息, OLD旧规则判定, NEW新规则期望)
BOUNDARY_CASES = [
    ("你好", False, False),                              # 1 纯问候
    ("帮我", False, False),                              # 2 礼貌前缀裸词（len<4，旧也简单）
    ("找一下", False, False),                            # 3 单字动词"找"（旧 len<4 也简单，但词已删）
    ("打开", False, False),                              # 4 裸动词过短
    ("翻译成英文", True, False),                          # 5 短问句（旧=复杂!）
    ("帮我写首诗", True, False),                          # 6 礼貌+单字"写"（旧=复杂!）
    ("帮忙看一下", True, False),                          # 7 "帮忙/查看"（旧=复杂!）
    ("请说句话吧", True, False),                          # 8 礼貌"请"（旧=复杂!）
    ("今天心情不错想聊聊天", False, False),                # 9 长闲聊无动词
    ("帮我在桌面创建一个txt文件", True, True),             # 10 真实长任务仍复杂
    ("帮我打开浏览器并搜索最新版本的资料", True, True),      # 11 剥前缀后恰 12 边界→复杂
    ("请打开浏览器并搜索新版资料", True, True),             # 12 同上（"请"前缀）
    ("open", True, False),                               # 13 英文裸动词过短（旧 len4 命中"open"=复杂!）
    ("please open the file manager for me right now", True, True),  # 14 please 剥离后≥12 含 open
    ("please don't confuse me just chat", False, False),  # 15 chat 无动作词（新旧都简单）
    ("请帮我写一份完整的季度总结报告并导出pdf", True, True),  # 16 "请帮我"整体先剥（长序优先）
]


@pytest.mark.parametrize(("msg", "old", "new"), BOUNDARY_CASES)
def test_boundary(msg, old, new):
    assert _is_complex_task(msg) is new


def test_old_rule_would_have_judged_polite_short_as_complex():
    """前后对比的核心证据：礼貌前缀/单字动词在旧表下会误判复杂，新规则判简单。

    旧规则 = len>=4 且命中旧动词表（含 帮我/帮忙/找/读/写/做）。
    """
    old_table_triggers = {"帮我", "找", "读", "写", "做", "帮忙"}
    for sample in ("帮我写首诗", "找一下资料", "写点东西吧"):
        assert len(sample) >= 4                      # 旧长度门槛满足
        assert any(v in sample for v in old_table_triggers)  # 旧表必命中 → 旧规则判复杂
        assert _is_complex_task(sample) is False     # 新规则判简单（快档优先）


def test_strip_polite_prefix_cases():
    assert strip_polite_prefix("帮我打开文件") == "打开文件"
    assert strip_polite_prefix("请帮我看看") == "看看"       # 长序优先剥"请帮我"
    assert strip_polite_prefix(" please open the door") == "open the door"
    assert strip_polite_prefix("请说句话吧") == "说句话吧"
    assert strip_polite_prefix("你好") == "你好"             # 无前缀不动
    assert strip_polite_prefix("  ") == ""
    assert strip_polite_prefix("") == ""


def test_pure_function_no_side_effects():
    import json
    before = json.dumps(_ACTION_VERBS, ensure_ascii=False)
    for _ in range(3):
        assert _is_complex_task("帮我在桌面创建一个txt文件") is True
    assert json.dumps(_ACTION_VERBS, ensure_ascii=False) == before
    # 同输入恒同输出（纯函数可测）
    assert _is_complex_task("翻译成英文") == _is_complex_task("翻译成英文")
    assert strip_polite_prefix("帮我测试一下这个函数") == strip_polite_prefix("帮我测试一下这个函数")


def test_unsure_defaults_simple_fastlane_first():
    """拿不准一律判简单（快档优先）：短中文常见模糊句全部 False。"""
    for msg in ("改一下", "看看这个", "做吧", "读完了吗", "帮我查", "写点啥"):
        assert _is_complex_task(msg) is False
