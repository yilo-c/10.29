"""资讯翻译器 — 英文标题/摘要批量翻译 + 本地缓存。

从 InfoCollectorService 中剥离翻译逻辑，便于独立维护和复用。
"""

from __future__ import annotations

import html
import json
import logging
import os
import re
from typing import Any, Callable

from services.info_constants import _ENGLISH_SOURCES, has_cjk

logger = logging.getLogger("siyi.services.info_translator")

_TRANSLATE_CACHE_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "data",
    "info_translate_cache.json",
)
_translate_cache: dict[str, str] = {}
_translate_cache_dirty = False
_TRANSLATE_CACHE_MAX_ENTRIES = 5000


def _load_translate_cache() -> None:
    global _translate_cache
    try:
        if os.path.exists(_TRANSLATE_CACHE_PATH):
            with open(_TRANSLATE_CACHE_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                _translate_cache = data
                logger.info("loaded translate cache: %d entries", len(_translate_cache))
    except Exception as exc:
        logger.debug("load translate cache failed: %s", exc)


def _save_translate_cache() -> None:
    global _translate_cache_dirty
    if not _translate_cache_dirty:
        return
    try:
        os.makedirs(os.path.dirname(_TRANSLATE_CACHE_PATH), exist_ok=True)
        items = list(_translate_cache.items())
        if len(items) > _TRANSLATE_CACHE_MAX_ENTRIES:
            items = items[-(_TRANSLATE_CACHE_MAX_ENTRIES // 2) :]
            _translate_cache.clear()
            _translate_cache.update(items)
        # 原子写：先写临时文件再 os.replace，防止崩溃损坏缓存文件
        tmp_path = _TRANSLATE_CACHE_PATH + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(_translate_cache, f, ensure_ascii=False)
        os.replace(tmp_path, _TRANSLATE_CACHE_PATH)
        _translate_cache_dirty = False
        logger.debug("saved translate cache: %d entries", len(_translate_cache))
    except Exception as exc:
        logger.debug("save translate cache failed: %s", exc)


_load_translate_cache()


def _strip_html(text: str) -> str:
    text = re.sub("<[^>]+>", "", text or "")
    text = html.unescape(text)
    return text.strip()


class InfoTranslator:
    """基于 AI 的资讯标题/摘要翻译器，带持久化缓存。"""

    BATCH_SIZE = 25

    def __init__(self, call_ai: Callable[..., Any]) -> None:
        self._call_ai = call_ai

    async def translate_items(
        self,
        items: list[dict[str, Any]],
        errors: list[dict[str, str]],
    ) -> None:
        """就地翻译英文来源条目的标题和摘要。"""
        to_translate: list[tuple[int, str, str]] = []
        for idx, item in enumerate(items):
            if item.get("source") not in _ENGLISH_SOURCES:
                continue
            title = item.get("title", "").strip()
            summary = _strip_html(item.get("summary", "")).strip()
            if not title or has_cjk(title):
                continue
            title_cached = title in _translate_cache
            summary_cached = not summary or summary == title or summary in _translate_cache
            if title_cached:
                item["title_zh"] = _translate_cache[title]
            if summary and summary != title and summary_cached:
                item["summary_zh"] = _translate_cache[summary]
            if title_cached and summary_cached:
                continue
            to_translate.append((idx, title, summary[:300]))

        if not to_translate:
            return

        for batch_start in range(0, len(to_translate), self.BATCH_SIZE):
            batch = to_translate[batch_start : batch_start + self.BATCH_SIZE]
            await self._translate_batch(batch, items, errors)

    async def _translate_batch(
        self,
        batch: list[tuple[int, str, str]],
        items: list[dict[str, Any]],
        errors: list[dict[str, str]],
    ) -> None:
        global _translate_cache_dirty
        lines: list[str] = []
        for i, (_, title, summary) in enumerate(batch):
            lines.append(f"{i + 1}. TITLE: {title}")
            if summary and summary != title:
                lines.append(f"   SUMMARY: {summary}")

        prompt = (
            "Translate the following English tech news titles and summaries into Simplified Chinese.\n"
            "Rules:\n"
            "1. Keep tech terms in English (e.g. React, TypeScript, API, LLM, GPU)\n"
            "2. Return in this EXACT format, one per item, two lines per item:\n"
            "   N|Chinese Title\n"
            "   N|Chinese Summary\n"
            "3. If summary is the same as title, repeat the translation for SUMMARY line\n"
            "4. Output ONLY the translations, no explanations\n\n"
        ) + "\n".join(lines)

        try:
            resp = await self._call_ai(
                messages=[
                    {
                        "role": "system",
                        "content": "You are a professional tech translator. Translate English to natural, fluent Simplified Chinese. Always follow the output format exactly: N|Chinese text, one entry per line, title first then summary.",
                    },
                    {"role": "user", "content": prompt},
                ],
                temperature=0.1,
                max_tokens=3000,
                timeout=45,
            )
            content = resp.get("choices", [{}])[0].get("message", {}).get("content", "")
            if not content:
                return

            title_map: dict[int, str] = {}
            summary_map: dict[int, str] = {}
            for line in content.strip().split("\n"):
                line = line.strip()
                m = re.match(r"^(\d+)\s*[\|\:]\s*(.+)$", line)
                if not m:
                    continue
                try:
                    num = int(m.group(1))
                except ValueError:
                    continue
                if not 1 <= num <= len(batch):
                    continue
                text = m.group(2).strip()
                if num not in title_map:
                    title_map[num] = text
                elif num not in summary_map:
                    summary_map[num] = text

            for i, (item_idx, orig_title, orig_summary) in enumerate(batch):
                num = i + 1
                title_zh = title_map.get(num, "")
                summary_zh = summary_map.get(num) or title_zh

                if title_zh:
                    items[item_idx]["title_zh"] = title_zh
                    if orig_title:
                        _translate_cache[orig_title] = title_zh
                        _translate_cache_dirty = True
                if summary_zh and orig_summary and orig_summary != orig_title:
                    items[item_idx]["summary_zh"] = summary_zh
                    _translate_cache[orig_summary] = summary_zh
                    _translate_cache_dirty = True

            _save_translate_cache()
        except Exception as e:
            errors.append({"source": "ai_translate", "error": str(e)})
            logger.warning("batch translate failed: %s", e)
