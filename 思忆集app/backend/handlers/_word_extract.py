"""生词提取 — translate.extract_words RPC + 文章异步生词卡片提议。

策略：
  1. 同步提取（translate.extract_words）：返回候选生词列表 [{word, phonetic, meaning, context}]
     - 调用 ai_client.complete_text，强 JSON schema
     - 已在词库的词不再返回
     - 限制最多 10 个候选
  2. 异步提议（_extract_words_async）：在 reading.save_article 完成后调用，
     emit `reading.words_proposed` 事件，由前端弹卡片让用户勾选入库
"""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Any

from backend.handlers._json_utils import parse_json_from_llm as _parse_json
from core.events import AppEvent, event_bus

logger = logging.getLogger("siyi.backend.handlers.word_extract")

# 提取上限
MAX_CANDIDATES = 10
# 最小英文单词长度（用于初筛，过滤 the/and/of 这种 stopword）
MIN_WORD_LENGTH = 5
# 常见 stopword（不入候选）
STOPWORDS = frozenset({
    "about", "after", "again", "against", "because", "before", "below",
    "between", "could", "during", "every", "first", "found", "further",
    "having", "however", "might", "other", "should", "their", "there",
    "these", "those", "through", "under", "until", "where", "which",
    "while", "would", "without", "around", "among", "above", "across",
    "though", "still", "really", "always", "almost", "another", "since",
})


def _candidate_words_from_text(text: str, exclude: set[str]) -> list[str]:
    """从文本中粗筛候选英文单词（按长度+stopword 排除+词频）。"""
    if not text:
        return []
    words = re.findall(r"\b[A-Za-z]+(?:[-'][A-Za-z]+)?\b", text.lower())
    counter: dict[str, int] = {}
    for w in words:
        if len(w) < MIN_WORD_LENGTH:
            continue
        if w in STOPWORDS:
            continue
        if w in exclude:
            continue
        counter[w] = counter.get(w, 0) + 1
    # 按频次倒序，相同频次按长度倒序（更长的更可能"难"）
    return sorted(counter.keys(), key=lambda w: (-counter[w], -len(w)))[:30]


def register_word_extract(registry, context) -> None:
    """注册 translate.extract_words 与文章异步提议钩子。"""

    async def _existing_words_lower() -> set[str]:
        repo = getattr(context, "vocabulary_repository", None)
        if not repo:
            return set()
        try:
            # P1 修复：list_words 是同步仓储方法（文件 I/O），用 to_thread 避免阻塞事件循环
            words = await asyncio.to_thread(repo.list_words)
            return {str(w.get("word") or "").strip().lower() for w in words}
        except Exception:
            logger.exception("获取现有生词列表失败")
            return set()

    async def _ai_extract(candidates: list[str], context_text: str) -> list[dict[str, Any]]:
        """让本地/云端 AI 从候选词中筛出真正"难且有学习价值"的并附释义+例句。"""
        ai_client = getattr(context, "ai_client", None)
        if not ai_client or not candidates:
            return []
        try:
            prompt = (
                "下面是一篇英文文章里反复出现的词。"
                "请挑选其中 5–8 个对中级学习者来说**最值得收入生词本**的词（CEFR B1+ 难度），"
                "对每个词给出：音标、最常用的中文释义（≤25 字）、文章中的一个例句。"
                "不要选过于简单或过于罕见的词。\n\n"
                "候选词：" + ", ".join(candidates[:30]) + "\n\n"
                "文章片段：\n" + (context_text[:1500] or "（无）") + "\n\n"
                "严格 JSON 输出：{\"words\":[{\"word\":\"\",\"phonetic\":\"\",\"meaning\":\"\",\"context\":\"\"}]}"
            )
            resp = await asyncio.to_thread(
                ai_client.chat_completion,
                messages=[
                    {"role": "system", "content": "你是英语词汇分析助手，只输出 JSON，不要任何解释。"},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.2,
                max_tokens=900,
                preferred_provider="local",
            )
            content = ""
            if isinstance(resp, dict):
                content = resp.get("choices", [{}])[0].get("message", {}).get("content", "")
            else:
                content = str(resp)
            parsed = _parse_json(content)
            out: list[dict[str, Any]] = []
            if isinstance(parsed, dict):
                for w in parsed.get("words") or []:
                    if not isinstance(w, dict):
                        continue
                    word = str(w.get("word") or "").strip()
                    if not word:
                        continue
                    out.append({
                        "word": word,
                        "phonetic": str(w.get("phonetic") or "").strip(),
                        "meaning": str(w.get("meaning") or "").strip(),
                        "context": str(w.get("context") or "").strip(),
                    })
                    if len(out) >= MAX_CANDIDATES:
                        break
            return out
        except Exception:
            logger.debug("AI extract failed", exc_info=True)
            return []

    # ── translate.extract_words ──
    async def translate_extract_words(
        text: str = "",
        max_words: int = MAX_CANDIDATES,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """提取文章中可能值得加入生词本的英文词。

        返回 {"words": [{"word", "phonetic", "meaning", "context"}], "candidates_count": N}.
        """
        if not text or not text.strip():
            return {"words": [], "candidates_count": 0}
        exclude = await _existing_words_lower()
        candidates = _candidate_words_from_text(text, exclude)
        if not candidates:
            return {"words": [], "candidates_count": 0}
        words = await _ai_extract(candidates, text)
        # AI 失败时降级：返回前 5 个频次高的候选词，不带释义（前端可继续编辑）
        if not words:
            words = [{"word": w, "phonetic": "", "meaning": "", "context": ""} for w in candidates[:5]]
        return {"words": words[:max(1, int(max_words or MAX_CANDIDATES))], "candidates_count": len(candidates)}

    registry.register("translate.extract_words", translate_extract_words)

    # ── 公开异步接口：reading.save_article 完成后调用 ──
    async def propose_words_for_article(article_id: str, title: str, summary: str, content: str = "") -> None:
        """从文章里挖生词并 emit reading.words_proposed 事件。永不抛错。"""
        try:
            text = "\n".join(filter(None, [title, summary, content]))
            if not text.strip():
                return
            result = await translate_extract_words(text=text, max_words=MAX_CANDIDATES)
            words = result.get("words") or []
            if not words:
                return
            event_bus.emit(AppEvent(
                event_type="reading.words_proposed",
                payload={
                    "article_id": article_id,
                    "title": title,
                    "words": words,
                    "count": len(words),
                },
                source="word_extract",
            ))
            logger.info("proposed %d words for article %s", len(words), article_id[:8])
        except Exception:
            logger.debug("propose_words_for_article failed", exc_info=True)

    # 暴露给 _reading.py 调用
    setattr(context, "_propose_words_for_article", propose_words_for_article)
