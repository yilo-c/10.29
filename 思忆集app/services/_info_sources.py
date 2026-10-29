"""信息源采集器 — HN / Dev.to / GitHub / Reddit / V2EX / 掘金 / arXiv / Product Hunt / 知乎。

All functions are standalone async taking aiohttp.ClientSession.
"""

from __future__ import annotations

import logging
import xml.etree.ElementTree as ET
from datetime import datetime
from typing import Any

import aiohttp

logger = logging.getLogger("siyi.services.info_collector.sources")

ITEM_TIMEOUT = aiohttp.ClientTimeout(total=10)

CHENGDU_KEYWORDS = ["成都", "四川", "天府", "锦江", "蓉城", "成华", "武侯", "高新西", "龙泉驿", "双流"]


def _e(element: ET.Element | None, path: str) -> str:
    """Safely get element text by path. For <link> elements, returns href attr first (Atom)."""
    if element is None:
        return ""
    found = element.find(path)
    if found is None:
        return ""
    if path == "link":
        href = found.get("href", "")
        if href:
            return href
    return found.text or ""


async def fetch_rss(
    s: aiohttp.ClientSession, url: str, source: str, limit: int = 10
) -> list[dict[str, Any]]:
    """Fetch and parse an RSS/Atom feed, returning standardised items."""
    try:
        async with s.get(url, timeout=ITEM_TIMEOUT) as r:
            text = await r.text()
        root = ET.fromstring(text)
        ns = {"atom": "http://www.w3.org/2005/Atom", "content": "http://purl.org/rss/1.0/modules/content/"}
        items: list[dict[str, Any]] = []
        for entry in (
            root.findall(".//item")
            or root.findall(".//atom:entry", ns)
        ):
            if len(items) >= limit:
                break
            title = _e(entry, "title")
            link = _e(entry, "link")
            pub_str = _e(entry, "pubDate") or _e(entry, "dc:date") or _e(entry, "updated")
            ts = 0
            if pub_str:
                try:
                    dt = datetime.fromisoformat(pub_str.replace("Z", "+00:00").replace(" +", "+").replace(" ", "T"))
                    ts = int(dt.timestamp() * 1000)
                except Exception:
                    try:
                        from email.utils import parsedate_to_datetime
                        dt = parsedate_to_datetime(pub_str)
                        ts = int(dt.timestamp() * 1000)
                    except Exception:
                        pass
            summary = _e(entry, "description") or _e(entry, "summary") or title
            tag_str = _e(entry, "category")
            tags = [t.strip() for t in tag_str.split(",")] if tag_str else []
            items.append({
                "id": f"{source}-rss-{len(items)}",
                "title": title,
                "url": link or "",
                "source": source,
                "summary": summary[:200],
                "tags": tags,
                "score": 0,
                "publishedAt": ts,
            })
        return items
    except Exception as exc:
        logger.debug("RSS fetch failed for %s: %s", url, exc)
        return []


def _fmt_hn_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "id": f'hn-{i["id"]}',
            "title": str(i.get("title", "")),
            "url": str(
                i.get("url", f"https://news.ycombinator.com/item?id={i['id']}")
            ),
            "source": "hackernews",
            "summary": str(i.get("title", "")),
            "tags": [str(i.get("type", ""))] if i.get("type") else [],
            "score": int(i.get("score", 0)),
            "publishedAt": int(i.get("time", 0)) * 1000,
        }
        for i in items
        if i
    ]


async def fetch_hn(s: aiohttp.ClientSession) -> list[dict[str, Any]]:
    """HackerNews: primary Firebase API, fallback to HN search API."""
    try:
        async with s.get(
            "https://hacker-news.firebaseio.com/v0/topstories.json",
            timeout=ITEM_TIMEOUT,
        ) as r:
            ids = (await r.json()) or []
        items: list[dict[str, Any]] = []
        for sid in ids[:20]:
            try:
                async with s.get(
                    f"https://hacker-news.firebaseio.com/v0/item/{sid}.json",
                    timeout=ITEM_TIMEOUT,
                ) as r:
                    i = await r.json()
                    if i:
                        items.append(i)
            except Exception as exc:
                logger.debug("Failed to parse HN item detail: %s", exc)
        if items:
            return _fmt_hn_items(items)
    except Exception as exc:
        logger.debug("HN primary failed: %s, trying mirror...", exc)

    # Mirror: HN Algolia search API
    try:
        async with s.get(
            "https://hn.algolia.com/api/v1/search?tags=front_page&hitsPerPage=20",
            timeout=ITEM_TIMEOUT,
        ) as r:
            data = await r.json()
        hits = data.get("hits", [])
        return [
            {
                "id": f"hn-algolia-{h['objectID']}",
                "title": str(h.get("title", "")),
                "url": str(
                    h.get("url") or f"https://news.ycombinator.com/item?id={h['objectID']}"
                ),
                "source": "hackernews",
                "summary": str(h.get("title", "")),
                "tags": [str(t) for t in (h.get("tags") or []) if isinstance(t, str)],
                "score": int(h.get("points", 0)),
                "publishedAt": int(h.get("created_at_i", 0)) * 1000,
            }
            for h in hits
            if h.get("title")
        ]
    except Exception as exc:
        logger.debug("HN mirror also failed: %s", exc)
        return []


async def fetch_devto(s: aiohttp.ClientSession) -> list[dict[str, Any]]:
    try:
        async with s.get(
            "https://dev.to/api/articles?top=1&per_page=20",
            timeout=ITEM_TIMEOUT,
        ) as r:
            data = await r.json()
        out: list[dict[str, Any]] = []
        for i in data or []:
            ts = 0
            if i.get("published_at"):
                try:
                    dt = datetime.fromisoformat(
                        i["published_at"].replace("Z", "+00:00")
                    )
                    ts = int(dt.timestamp() * 1000)
                except Exception:
                    pass
            out.append(
                {
                    "id": f"devto-{i['id']}",
                    "title": str(i.get("title", "")),
                    "url": str(i.get("url", "")),
                    "source": "devto",
                    "summary": str(i.get("description", "")),
                    "tags": list(i.get("tag_list", [])),
                    "score": int(i.get("positive_reactions_count", 0)),
                    "publishedAt": ts,
                }
            )
        return out
    except Exception as exc:
        logger.debug("Dev.to API failed: %s, trying RSS...", exc)
        return await fetch_rss(s, "https://dev.to/feed", "devto", limit=20)


async def fetch_github(s: aiohttp.ClientSession) -> list[dict[str, Any]]:
    """GitHub trending repos via Search API."""
    try:
        from datetime import datetime, timedelta
        week_ago = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")
        async with s.get(
            f"https://api.github.com/search/repositories"
            f"?q=stars:>50+pushed:>{week_ago}&sort=stars&order=desc&per_page=20",
            headers={"User-Agent": "siyiji-backend/1.0", "Accept": "application/vnd.github.v3+json"},
            timeout=ITEM_TIMEOUT,
        ) as r:
            data = await r.json()
        return [
            {
                "id": f'gh-{item.get("full_name", "")}',
                "title": item.get("full_name", ""),
                "url": item.get("html_url", ""),
                "source": "github",
                "summary": (item.get("description") or "")[:200],
                "tags": [item.get("language", "")] if item.get("language") else [],
                "score": int(item.get("stargazers_count", 0)),
                "publishedAt": 0,
            }
            for item in (data.get("items", []) or [])
            if item.get("full_name")
        ]
    except Exception as exc:
        logger.debug("GitHub fetch failed: %s", exc)
        return []


async def fetch_reddit(s: aiohttp.ClientSession) -> list[dict[str, Any]]:
    """Reddit r/programming 热门 via RSS."""
    return await fetch_rss(
        s, "https://www.reddit.com/r/programming/hot/.rss", "reddit", limit=20,
    )


async def fetch_v2ex(s: aiohttp.ClientSession) -> list[dict[str, Any]]:
    """V2EX 中文技术社区热门话题。"""
    try:
        async with s.get(
            "https://www.v2ex.com/api/topics/hot.json",
            timeout=ITEM_TIMEOUT,
        ) as r:
            data = await r.json()
        return [
            {
                "id": f"v2ex-{t['id']}",
                "title": str(t.get("title", "")),
                "url": f"https://www.v2ex.com/t/{t['id']}",
                "source": "v2ex",
                "summary": str(t.get("content_rendered", "") or t.get("title", ""))[:200],
                "tags": [t["node"]["name"]] if t.get("node") and t["node"].get("name") else [],
                "score": int(t.get("replies", 0)),
                "publishedAt": int(t.get("created", 0)) * 1000,
            }
            for t in (data or [])
            if t.get("title")
        ]
    except Exception as exc:
        logger.debug("V2EX fetch failed: %s", exc)
        return []


async def fetch_juejin(s: aiohttp.ClientSession) -> list[dict[str, Any]]:
    """掘金 — 中文技术社区推荐文章。API 优先，RSS 兜底。"""
    try:
        body = {"id_type": 2, "sort_type": 200, "client_type": 2608}
        headers = {"Content-Type": "application/json"}
        async with s.post(
            "https://api.juejin.cn/recommend_api/v1/article/recommend_all_feed",
            json=body, headers=headers, timeout=ITEM_TIMEOUT,
        ) as r:
            data = await r.json()
        items: list[dict[str, Any]] = []
        for entry in (data.get("data", []) or [])[:20]:
            info = entry.get("item_info", {}) or {}
            article = info.get("article_info", {}) or {}
            tags_list = [t.get("tag_name", "") for t in (info.get("tags", []) or []) if t.get("tag_name")]
            items.append({
                "id": f"juejin-{article.get('article_id', '')}",
                "title": str(article.get("title", "")),
                "url": f"https://juejin.cn/post/{article.get('article_id', '')}",
                "source": "juejin",
                "summary": str(article.get("brief_content", "") or "")[:200],
                "tags": tags_list,
                "score": int(article.get("digg_count", 0)),
                "publishedAt": int(article.get("ctime", 0)) * 1000,
            })
        return items
    except Exception as exc:
        logger.debug("Juejin API failed: %s, trying RSS fallback...", exc)
        return await fetch_rss(s, "https://juejin.cn/rss", "juejin", limit=20)


async def fetch_arxiv(s: aiohttp.ClientSession) -> list[dict[str, Any]]:
    """arXiv 最新论文 — AI/ML/NLP/CL 类别。"""
    try:
        url = (
            "http://export.arxiv.org/api/query?"
            "search_query=cat:cs.AI+OR+cat:cs.LG+OR+cat:cs.CL"
            "&sortBy=submittedDate&sortOrder=descending&max_results=15"
        )
        async with s.get(url, timeout=ITEM_TIMEOUT) as r:
            text = await r.text()
        root = ET.fromstring(text)
        ns = {"atom": "http://www.w3.org/2005/Atom"}
        items: list[dict[str, Any]] = []
        for entry in root.findall("atom:entry", ns)[:15]:
            title = " ".join((_e(entry, "title") or "").split())
            summary = " ".join((_e(entry, "summary") or "").split())[:200]
            link = _e(entry, "id")
            published_str = _e(entry, "published")
            ts = 0
            if published_str:
                try:
                    dt = datetime.fromisoformat(published_str.replace("Z", "+00:00"))
                    ts = int(dt.timestamp() * 1000)
                except Exception:
                    pass
            authors = []
            for author in entry.findall("atom:author", ns):
                name = author.find("atom:name", ns)
                if name is not None and name.text:
                    authors.append(name.text.strip())
            items.append({
                "id": f"arxiv-{len(items)}",
                "title": title,
                "url": link,
                "source": "arxiv",
                "summary": summary,
                "tags": authors[:3],
                "score": 0,
                "publishedAt": ts,
            })
        return items
    except Exception as exc:
        logger.debug("arXiv fetch failed: %s", exc)
        return []


async def fetch_producthunt(s: aiohttp.ClientSession) -> list[dict[str, Any]]:
    """Product Hunt 最新产品发布 via RSS."""
    return await fetch_rss(s, "https://www.producthunt.com/feed", "producthunt", limit=15)


async def fetch_zhihu(s: aiohttp.ClientSession) -> list[dict[str, Any]]:
    """知乎 explore 公共 feed（无需登录）。"""
    try:
        async with s.get(
            "https://www.zhihu.com/api/v3/explore/guest/feeds?limit=10",
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"},
            timeout=ITEM_TIMEOUT,
        ) as r:
            data = await r.json(content_type=None)
        items: list[dict[str, Any]] = []
        for entry in (data.get("data", []) or [])[:20]:
            target = entry.get("target", {}) or {}
            q = target.get("question", {})
            title = (q.get("title") or target.get("title") or "").strip()
            if not title:
                continue
            qid = q.get("id", "")
            answer_id = target.get("id", "")
            url = f"https://www.zhihu.com/question/{qid}/answer/{answer_id}" if qid and answer_id else (
                f"https://www.zhihu.com/question/{qid}" if qid else ""
            )
            excerpt = (target.get("excerpt") or "")[:200]
            score = int(target.get("voteup_count", 0))
            items.append({
                "id": f"zhihu-{answer_id or len(items)}",
                "title": title,
                "url": url,
                "source": "zhihu",
                "summary": excerpt,
                "tags": [],
                "score": score,
                "publishedAt": int(target.get("created_time", 0)) * 1000,
            })
        return items
    except Exception as exc:
        logger.debug("知乎 explore 获取失败: %s", exc)
        return []
