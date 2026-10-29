"""新闻源采集器 — 国内 / 国际 / 成都本地。

All functions are standalone async taking aiohttp.ClientSession.
"""

from __future__ import annotations

import logging
from typing import Any

import aiohttp

from ._info_sources import fetch_rss, CHENGDU_KEYWORDS

logger = logging.getLogger("siyi.services.info_collector.news")


async def fetch_domestic(s: aiohttp.ClientSession) -> list[dict[str, Any]]:
    """国内大势 — 聚合多个中文新闻源。"""
    feeds: list[tuple[str, str, int]] = [
        ("https://rsshub.app/36kr/motif/domestic", "36kr", 8),
        ("https://rsshub.app/thepaper/feature", "thepaper", 8),
    ]
    results: list[dict[str, Any]] = []
    for url, source_tag, limit in feeds:
        try:
            items = await fetch_rss(s, url, source_tag, limit=limit)
            for it in items:
                it["category"] = "domestic"
            results.extend(items)
        except Exception as exc:
            logger.debug("domestic feed %s failed: %s", source_tag, exc)
    if not results:
        fallback_feeds = [
            ("https://www.cls.cn/rss", "cls", 8),
        ]
        for url, source_tag, limit in fallback_feeds:
            try:
                items = await fetch_rss(s, url, source_tag, limit=limit)
                for it in items:
                    it["category"] = "domestic"
                results.extend(items)
            except Exception as exc:
                logger.debug("domestic fallback %s failed: %s", source_tag, exc)
    if not results:
        try:
            items = await fetch_rss(s, "https://rsshub.app/36kr/newsflashes", "36kr-flash", limit=10)
            for it in items:
                it["category"] = "domestic"
            results.extend(items)
        except Exception as exc:
            logger.debug("domestic last-resort failed: %s", exc)
    return results[:20]


async def fetch_international(s: aiohttp.ClientSession) -> list[dict[str, Any]]:
    """国际大势 — BBC 中文 + 纽约时报中文等。"""
    feeds: list[tuple[str, str, int]] = [
        ("https://feeds.bbci.co.uk/zhongwen/simp/rss.xml", "bbc-zh", 10),
        ("https://cn.nytimes.com/rss/", "nytimes-zh", 8),
    ]
    results: list[dict[str, Any]] = []
    for url, source_tag, limit in feeds:
        try:
            items = await fetch_rss(s, url, source_tag, limit=limit)
            for it in items:
                it["category"] = "international"
            results.extend(items)
        except Exception as exc:
            logger.debug("international feed %s failed: %s", source_tag, exc)
    return results[:20]


async def fetch_chengdu(s: aiohttp.ClientSession) -> list[dict[str, Any]]:
    """成都新闻 — 尝试四川/成都本地源。"""
    feeds: list[tuple[str, str, int]] = [
        ("https://rsshub.app/scol/cd", "scol-cd", 8),
    ]
    results: list[dict[str, Any]] = []
    for url, source_tag, limit in feeds:
        try:
            items = await fetch_rss(s, url, source_tag, limit=limit)
            for it in items:
                it["category"] = "local"
            results.extend(items)
        except Exception as exc:
            logger.debug("chengdu feed %s failed: %s", source_tag, exc)
    if not results:
        try:
            items = await fetch_rss(s, "https://rsshub.app/thepaper/feature", "thepaper", limit=12)
            for it in items:
                title = it.get("title", "")
                summary = it.get("summary", "")
                if any(kw in (title + summary) for kw in CHENGDU_KEYWORDS):
                    it["category"] = "local"
                    results.append(it)
        except Exception as exc:
            logger.debug("chengdu fallback failed: %s", exc)
    return results[:15]
