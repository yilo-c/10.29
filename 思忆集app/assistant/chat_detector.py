# -*- coding: utf-8 -*-
"""统一闲聊检测器 — agent_loop 和 autonomy 共用。

P1-3: 将 agent_loop._is_chat_only 和 autonomy._is_simple_chat_question
合并为单一模块，避免规则分散和不一致。
"""
from __future__ import annotations

# 闲聊式前缀（以这些开头多半是提问/闲聊，不是指令）
CHAT_PREFIXES = (
    "为什么", "什么是", "怎么理解", "如何理解", "你觉得", "你认为",
    "你能", "可以", "告诉我", "解释一下", "讲讲", "说说", "聊聊",
    "有没有", "是不是", "会不会", "好不好", "能不能", "我想", "我想问", "请问",
)

# 精确匹配的闲聊触发词（寒暄/简单问答）
CHAT_TRIGGERS = frozenset({
    "你好", "嗨", "hi", "hello", "hey",
    "早安", "早上好", "下午好", "晚上好", "晚安", "拜拜", "再见",
    "嗯", "哦", "对", "是的", "好", "好的", "哈哈", "呵呵", "嘿嘿",
    "谢谢", "感谢", "thanks", "thank you", "thx",
    "在吗", "在不在", "你是谁", "你叫什么", "你会什么", "能做什么",
    "讲个笑话", "说个故事", "现在几点", "几点了", "今天天气", "ok",
})

# 命令式动词开头（这些是指令，不是闲聊）
COMMAND_STARTERS = (
    "打开", "关闭", "启动", "运行", "执行", "下载", "同步", "搜索",
    "读取", "写入", "帮我", "检查", "扫描", "抓取", "创建", "删除",
    "修改", "更新", "添加", "保存", "发送", "翻译", "总结", "分析",
    "整理", "导出", "导入",
)


def is_chat_only(text: str, *, strict: bool = True) -> bool:
    """判断消息是否为纯闲聊，可跳过 Agent 循环直接走 LLM 对话。

    Args:
        text: 用户消息
        strict: 严格模式（autonomy 用 True，agent_loop 用 False）。
                strict=True 时启用短消息/长消息规则；
                strict=False 时只检查精确匹配和前缀。

    规则优先级：
    1. 空消息或极短消息 → True
    2. 精确匹配 CHAT_TRIGGERS → True
    3. 以 CHAT_PREFIXES 开头 → True
    4. 短消息（≤15字）+ 问句结尾 → True
    5. [strict] 短消息（<10字）→ True
    6. [strict] 长消息（>30字）且不以命令式动词开头 → True
    7. 其他 → False
    """
    text = text.strip()
    # 1. 空消息或极短消息（<3字）直接当闲聊
    if not text or len(text) < 3:
        return True

    lower = text.lower()

    # 2. 精确匹配触发词（原文或小写）
    if text in CHAT_TRIGGERS or lower in CHAT_TRIGGERS:
        return True

    # 3. 闲聊式前缀
    if any(lower.startswith(w) for w in CHAT_PREFIXES):
        return True

    # 4. 短消息且以问句结尾
    if len(text) <= 15 and text[-1] in ("?", "？", "吗", "呢", "吧"):
        return True

    if not strict:
        return False

    # 5. [strict] 超短消息（<10字）当闲聊
    if len(text) < 10:
        return True

    # 6. [strict] 长消息且不以命令式动词开头 → 闲聊
    if len(text) > 30 and not any(lower.startswith(w) for w in COMMAND_STARTERS):
        return True

    # 7. 其他：当作指令
    return False


__all__ = ["is_chat_only", "CHAT_PREFIXES", "CHAT_TRIGGERS", "COMMAND_STARTERS"]
