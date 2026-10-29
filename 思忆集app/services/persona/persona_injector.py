"""人格注入器 — 将小问人格注入 LLM 调用上下文中。

接入点：在 _judge_aware_client.py 的 chat_completion() 入口处调用 inject_xiaowen_persona()。
"""

from __future__ import annotations

import logging

from .persona_definition import (
    XIAOWEN_SYSTEM_PROMPT_TEMPLATE,
    get_persona_traits_text,
    get_language_style_text,
)

logger = logging.getLogger("siyi.services.persona.injector")


def build_persona_system_prompt(
    user_name: str = "你",
    self_context: str = "",
    memory_narrative: str = "",
) -> str:
    """构建完整的小问系统提示词。

    Args:
        user_name: 用户名或称呼
        self_context: 当前自我认知上下文（由 agent_state 提供）
        memory_narrative: 共享记忆叙事（由 narrative 提供）

    Returns:
        格式化的系统提示词字符串
    """
    return XIAOWEN_SYSTEM_PROMPT_TEMPLATE.format(
        user_name=user_name,
        traits=get_persona_traits_text(),
        language_style=get_language_style_text(),
        self_context=self_context or "你正在逐渐了解他。你们的关系会随着交互自然增长。",
        memory_narrative=memory_narrative or "这是你们刚开始的对话。你还没有太多关于他的记忆，但你会记住这一切。",
    )


def inject_xiaowen_persona(
    messages: list[dict[str, str]],
    *,
    user_name: str = "你",
    self_context: str = "",
    memory_narrative: str = "",
    inject_persona: bool = True,
) -> list[dict[str, str]]:
    """将小问人格注入消息列表。

    在消息列表开头插入小问 system prompt（如果尚未存在）。
    翻译等不需要人格的场景可设置 inject_persona=False。

    Args:
        messages: 原始消息列表
        user_name: 用户名
        self_context: 自我认知上下文
        memory_narrative: 共享记忆叙事
        inject_persona: 是否注入人格（设为 False 跳过）

    Returns:
        注入后的消息列表
    """
    if not inject_persona:
        return messages

    if not messages:
        return messages

    # 检查是否已经注入了小问人格
    has_persona = False
    for m in messages:
        if m.get("role") == "system" and "你是小问" in m.get("content", ""):
            has_persona = True
            break

    if has_persona:
        return messages

    persona_prompt = build_persona_system_prompt(
        user_name=user_name,
        self_context=self_context,
        memory_narrative=memory_narrative,
    )

    # 在开头插入 system prompt
    result = list(messages)
    result.insert(0, {"role": "system", "content": persona_prompt})
    return result
