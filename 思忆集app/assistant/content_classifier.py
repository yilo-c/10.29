"""ContentClassifier — 用本地 Ollama 对内容自动分类。

分类结果用于路由数据到正确的存储（记忆/知识库/笔记/词库/丢弃）。
使用 qwen2.5:3b，单次分类约 1-2s。
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

logger = logging.getLogger("siyi.content_classifier")

# 分类定义
CATEGORIES = {
    "work_tech": "工作技术（代码、技术方案、架构设计、Bug、项目管理）",
    "personal": "个人生活（家庭、健康、社交、兴趣爱好、日常记录）",
    "learning": "学习阅读（知识总结、读书笔记、课程内容、研究）",
    "news_info": "资讯新闻（时事、行业动态、RSS 文章）",
    "trash": "垃圾信息（广告、无关闲聊、重复内容）",
}

# 快速规则匹配（不调 LLM，零延迟）
_RULE_MAP: list[tuple[re.Pattern, str]] = [
    (re.compile(r"(def |class |import |function|const |var |```)", re.I), "work_tech"),
    (re.compile(r"(http|https?://|www\.)\S+", re.I), "news_info"),
    (re.compile(r"(广告|推广|优惠券|点击领取|免费领)", re.I), "trash"),
]


def _rule_match(text: str) -> str | None:
    """快速规则匹配，0 延迟。"""
    for pattern, cat in _RULE_MAP:
        if pattern.search(text):
            return cat
    # 长度过短 → 难以分类
    if len(text.strip()) < 20:
        return "personal"
    return None


def _llm_classify(text: str, ai_client: Any) -> str:
    """用本地 Ollama 分类。1-2s。"""
    prompt = (
        f"将以下内容分类，只返回 JSON，不要其他文字。\n\n"
        f"类别：\n"
        + "\n".join(f'- {k}: {v}' for k, v in CATEGORIES.items()) +
        f"\n\n内容：{text[:1500]}\n\nJSON: {{\"category\":\"类别key\",\"reason\":\"分类原因(5字内)\",\"confidence\":0.0-1.0}}"
    )
    try:
        resp = ai_client.chat_completion(
            messages=[
                {"role": "system", "content": "你是一个内容分类助手，只输出 JSON。"},
                {"role": "user", "content": prompt},
            ],
            temperature=0.1,
            max_tokens=150,
        )
        content = ""
        if isinstance(resp, dict):
            content = resp.get("choices", [{}])[0].get("message", {}).get("content", "")
        else:
            content = str(resp)
        parsed = _parse_json(content)
        if isinstance(parsed, dict) and parsed.get("category") in CATEGORIES:
            return str(parsed["category"])
    except Exception as exc:
        logger.debug("LLM classify failed: %s", exc)
    return "personal"


class ContentClassifier:
    """内容分类器。先规则匹配，规则不命中再调 LLM。"""

    def classify(self, text: str, ai_client: Any | None = None) -> str:
        """对文本分类，返回 category key。"""
        if not text or not text.strip():
            return "trash"

        # 1. 规则匹配（0 延迟）
        cat = _rule_match(text)
        if cat:
            return cat

        # 2. LLM 分类（需 ai_client）
        if ai_client is not None:
            try:
                return _llm_classify(text, ai_client)
            except Exception as exc:
                logger.warning("classifier LLM fallback failed: %s", exc)

        return "personal"


def _parse_json(text: str) -> dict | list | None:
    c = text.strip()
    if c.startswith("```"):
        nl = c.find("\n")
        if nl >= 0:
            c = c[nl + 1:]
        if c.endswith("```"):
            c = c[:-3]
        c = c.strip()
    bs = c.find("{")
    if bs >= 0:
        be = c.rfind("}")
        if be > bs:
            try:
                return json.loads(c[bs:be + 1])
            except json.JSONDecodeError:
                pass
    return None
