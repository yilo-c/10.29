"""统一意图分类器 — 一次 LLM 调用替三个系统。

替代: CopilotEngine 分类 + ReAct._needs_tools + AgentOrchestrator 意图判断
设计: 轻量 prompt + max_tokens=50，目标延迟 ~0.5s
"""
from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger("siyi.backend.classifier")

CLASSIFY_SYSTEM_PROMPT: str = (
    "分析用户输入，返回纯 JSON，不超过 50 字。\n"
    '{"intent":"意图","confidence":0.8}\n\n'
    "意图类型:\n"
    "- chat: 闲聊、提问、讨论、问候、情感表达\n"
    "- create_note: 想保存为笔记\n"
    "- web_search: 需要联网查资料\n"
    "- create_task: 待办/提醒/日程\n"
    "- translate: 需要翻译\n"
    "- remember: 表达偏好/习惯/重要信息\n"
    "- ocr: 图片提取文字\n"
    "- url_learn: 需要学习网页内容\n\n"
    "只返回 JSON，不要其他文字。"
)

INTENT_FALLBACK: dict[str, Any] = {"intent": "chat", "confidence": 0.0}


async def classify_intent(msg: str, ai_client: Any) -> dict[str, Any]:
    """快速分类用户意图。

    Args:
        msg: 用户输入文本
        ai_client: AI 客户端（JudgeAwareClient / FallbackOpenAIClient）

    Returns:
        {"intent": "chat|create_note|...", "confidence": 0.95}
    """
    if not msg or len(msg.strip()) < 3:
        return {"intent": "chat", "confidence": 0.99}

    try:
        resp = ai_client.complete_text(
            messages=[
                {"role": "system", "content": CLASSIFY_SYSTEM_PROMPT},
                {"role": "user", "content": msg[:500]},
            ],
            max_tokens=50,
        )
        text = (resp or "").strip()
        # Extract JSON
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            result = json.loads(text[start:end + 1])
            if isinstance(result, dict) and "intent" in result:
                return {"intent": result["intent"], "confidence": result.get("confidence", 0.5)}
    except Exception as exc:
        logger.debug("Intent classification failed: %s", exc)

    return INTENT_FALLBACK
