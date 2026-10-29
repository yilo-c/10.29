"""InboxClassifierService — AI 自动分类收件箱条目并路由到对应模块。"""
from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger("siyi.services.inbox_classifier")

CONTENT_KINDS = {
    "note": "笔记 — 需要保存的信息、想法、知识点",
    "task": "任务 — 需要完成的具体事项、待办",
    "reminder": "提醒 — 时间敏感的备忘、约会、日程",
    "idea": "灵感 — 创意、点子、构思",
    "question": "问题 — 需要查找答案的问题",
    "reference": "参考 — 书签、链接、资源引用",
    "uncertain": "不确定 — 无法明确分类",
}


class InboxClassifierService:
    """使用 AI 对收件箱条目自动分类。"""

    def __init__(self, ai_client: Any = None, settings_repository: Any = None,
                 ai_text_service: Any = None):
        self._ai_client = ai_client           # 保留兼容（过渡期）
        self._settings = settings_repository
        self._ai_service = ai_text_service

    async def classify(self, content: str, title: str = "") -> dict[str, Any]:
        """对收件箱内容进行 AI 分类。

        Returns:
            {"kind": str, "confidence": float, "reason": str, "suggested_action": str}
        """
        if not content.strip():
            return {"kind": "uncertain", "confidence": 0.0, "reason": "内容为空", "suggested_action": "none"}

        if self._ai_service is not None:
            try:
                return await self._ai_service.classify(content, title)
            except Exception as exc:
                logger.warning("AI 分类失败，回退到关键词: %s", exc)
                return self._keyword_fallback(content, title)
        if self._ai_client is None:
            return self._keyword_fallback(content, title)
        # ← 保留原 chat_completion 实现作为无端口时的兼容路径（M5 删）
        prompt = (
            "你是一个收件箱分类助手。分析以下内容，判断它最适合归入哪个类别。\n\n"
            f"标题: {title or '（无标题）'}\n"
            f"内容: {content[:500]}\n\n"
            "类别说明:\n"
            + "\n".join(f"  - {k}: {v}" for k, v in CONTENT_KINDS.items()) +
            "\n\n请以 JSON 格式回复，包含:\n"
            '  {"kind": "类别key", "confidence": 0.0~1.0, "reason": "简短理由", '
            '"suggested_action": "none|suggest_convert|auto_convert"}\n\n'
            "仅返回 JSON，不要其他文字。"
        )
        try:
            resp = self._ai_client.chat_completion(
                messages=[{"role": "user", "content": prompt}],
                temperature=0.1,
                max_tokens=200,
            )
            text = self._extract_json(resp)
            result = json.loads(text)
            result.setdefault("kind", "uncertain")
            result.setdefault("confidence", 0.0)
            result.setdefault("reason", "")
            result.setdefault("suggested_action", "none")
            if result["kind"] not in CONTENT_KINDS:
                result["kind"] = "uncertain"
            return result
        except Exception as exc:
            logger.warning("AI 分类失败，回退到关键词: %s", exc)
            return self._keyword_fallback(content, title)

    def _keyword_fallback(self, content: str, title: str) -> dict[str, Any]:
        """基于关键词的简单分类回退。"""
        text = f"{title} {content}".lower()

        # Task keywords
        task_keywords = ["todo", "待办", "完成", "需要做", "记得", "必须", "安排", "计划",
                         "buy", "get", "do", "fix", "schedule", "book", "order", "提交"]
        if any(kw in text for kw in task_keywords):
            return {"kind": "task", "confidence": 0.6, "reason": "包含任务关键词", "suggested_action": "suggest_convert"}

        # Reminder keywords
        reminder_keywords = ["提醒", "明天", "下周", "会议", "约会", "截止", "deadline",
                             "remind", "tomorrow", "next week", "meeting", "appointment"]
        if any(kw in text for kw in reminder_keywords):
            return {"kind": "reminder", "confidence": 0.6, "reason": "包含时间关键词", "suggested_action": "suggest_convert"}

        # Question keywords
        question_keywords = ["?", "？", "什么", "怎么", "为什么", "如何", "是否", "哪里",
                             "what", "how", "why", "when", "where", "can i", "is it"]
        if any(kw in text for kw in question_keywords):
            return {"kind": "question", "confidence": 0.5, "reason": "包含疑问句式", "suggested_action": "none"}

        # URL → reference
        if "http://" in text or "https://" in text:
            return {"kind": "reference", "confidence": 0.7, "reason": "包含链接", "suggested_action": "none"}

        # Longer content → note
        if len(content) > 50:
            return {"kind": "note", "confidence": 0.5, "reason": "较长的文字内容", "suggested_action": "suggest_convert"}

        return {"kind": "uncertain", "confidence": 0.0, "reason": "无法判断", "suggested_action": "none"}

    @staticmethod
    def _extract_json(resp: Any) -> str:
        """从 LLM 回复中提取 JSON 字符串。"""
        if isinstance(resp, dict):
            choices = resp.get("choices") or []
            if choices:
                msg = choices[0].get("message") or {}
                text = (msg.get("content") or "").strip()
            else:
                text = str(resp)
        elif isinstance(resp, str):
            text = resp.strip()
        else:
            text = str(resp)

        # Remove markdown code fences
        if text.startswith("```"):
            text = text.split("\n", 1)[-1]
            text = text.rsplit("```", 1)[0].strip()

        # Find first { and last }
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            text = text[start : end + 1]

        return text
