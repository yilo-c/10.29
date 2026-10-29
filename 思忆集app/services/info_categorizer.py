"""资讯分类器 — 关键词预分类 + AI 二次分类。

从 InfoCollectorService 中剥离分类逻辑，便于独立维护和复用。
"""
from __future__ import annotations

import logging
from typing import Any, Callable

from services.info_constants import get_effective_section_order, get_section_keywords

logger = logging.getLogger("siyi.services.info_categorizer")


def categorize_item(item: dict[str, Any]) -> tuple[str, int]:
    """关键词分类，返回 (category, score)。"""
    keywords_map = get_section_keywords()
    title = (item.get("title_zh") or item.get("title") or "").lower()
    summary = (item.get("summary_zh") or item.get("summary") or "").lower()
    tags = " ".join(t.lower() for t in item.get("tags", []))
    text = f"{title} {title} {summary} {tags}"

    best_cat = "general"
    best_score = 0
    second_score = 0
    for cat, keywords in keywords_map.items():
        score = sum(text.count(kw.lower()) for kw in keywords if kw)
        if score > best_score:
            second_score = best_score
            best_score = score
            best_cat = cat
        elif score > second_score:
            second_score = score
    return best_cat, best_score


class InfoCategorizer:
    """混合分类器：先关键词匹配，再对低置信度条目做 AI 二次分类。"""

    def __init__(self, call_ai: Callable[..., Any], primary_ai: Any = None) -> None:
        self._call_ai = call_ai
        self._primary_ai = primary_ai

    def pre_categorize(self, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """关键词预分类，返回需要 AI 二次分类的候选条目。"""
        candidates: list[dict[str, Any]] = []
        for item in items:
            if not item.get("category"):
                cat, score = categorize_item(item)
                item["category"] = cat
                if score == 0 or cat == "general":
                    candidates.append(item)
        return candidates

    async def ai_categorize_batch(
        self,
        items: list[dict[str, Any]],
        errors: list[dict[str, str]],
        max_items: int = 40,
    ) -> None:
        """对低置信度条目使用 AI 做批量二次分类。"""
        if not items or not self._primary_ai:
            return

        batch = items[:max_items]
        lines: list[str] = []
        for i, item in enumerate(batch):
            title = item.get("title_zh") or item.get("title", "")
            summary = item.get("summary_zh") or item.get("summary", "")
            lines.append(f"{i + 1}. TITLE: {title}\n   SUMMARY: {summary[:200]}")

        order = get_effective_section_order()
        section_lines: list[str] = []
        for sid in order:
            if sid == "general":
                section_lines.append("general（其他）")
                continue
            from services.info_constants import get_effective_section_meta
            meta = get_effective_section_meta(sid)
            section_lines.append(f"{sid}（{meta['emoji']} {meta['name']}）")

        prompt = (
            "请为以下每条资讯选择最合适的单一分类。可选分类："
            + ", ".join(section_lines)
            + "\n返回格式：N|分类代码，每行一条，只返回分类结果，不要解释。\n\n"
            + "\n".join(lines)
        )

        try:
            resp = await self._call_ai(
                messages=[
                    {"role": "system", "content": "你是一个资讯分类助手，只输出 N|分类代码 格式。"},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.1,
                max_tokens=800,
                timeout=30,
            )
            content = resp.get("choices", [{}])[0].get("message", {}).get("content", "")
            if not content:
                return
            import re
            for line in content.strip().split("\n"):
                line = line.strip()
                m = re.match(r"^(\d+)\s*[\|\:]\s*([a-zA-Z0-9_]+)$", line)
                if not m:
                    continue
                try:
                    num = int(m.group(1))
                except ValueError:
                    continue
                cat = m.group(2).lower()
                if 1 <= num <= len(batch) and cat in get_effective_section_order():
                    batch[num - 1]["category"] = cat
        except Exception as e:
            errors.append({"source": "ai_categorize", "error": str(e)})
            logger.warning("AI categorize failed: %s", e)
