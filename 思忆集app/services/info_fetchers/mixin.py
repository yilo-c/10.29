"""Info Collector Fetchers — 从 InfoCollectorService 拆出的抓取器 mixin。

包含 30 个内置源 fetcher（_hn/_devto/_fetch_rss/...）与解析辅助。
方法只依赖 session 参数 + 模块级工具（不引用实例状态），因此可安全
作为 mixin 被 InfoCollectorService 继承，getattr(self, FETCHER_METHOD_MAP[..])
调用方式完全不变。
"""

from __future__ import annotations

import asyncio
import html
import json
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta
from urllib.parse import urljoin
from typing import Any

import aiohttp

from services._info_collector_utils import (
    _e,
    _strip_html,
    ITEM_TIMEOUT,
    SHORT_TIMEOUT,
)

import logging

logger = logging.getLogger("siyi.services.info_collector")


class InfoFetchersMixin:
    """内置源抓取器。通过继承混入 InfoCollectorService。"""

    async def _hn(self, s: aiohttp.ClientSession) -> list[dict[str, Any]]:
        primary_exc: Exception | None = None
        try:
            async with s.get(
                "https://hacker-news.firebaseio.com/v0/topstories.json",
                timeout=ITEM_TIMEOUT,
            ) as r:
                ids = (await r.json()) or []
            items: list[dict[str, Any]] = []
            for sid in ids[:15]:
                try:
                    async with s.get(
                        f"https://hacker-news.firebaseio.com/v0/item/{sid}.json",
                        timeout=SHORT_TIMEOUT,
                    ) as r:
                        i = await r.json()
                        if i:
                            items.append(i)
                except Exception:
                    pass
            if items:
                return self._fmt_hn_items(items)
        except Exception as exc:
            primary_exc = exc
        try:
            async with s.get(
                "https://hn.algolia.com/api/v1/search?tags=front_page&hitsPerPage=15",
                timeout=ITEM_TIMEOUT,
            ) as r:
                data = await r.json()
            hits = data.get("hits", [])
            return [
                {
                    "id": f"hn-{h['objectID']}",
                    "title": str(h.get("title", "")),
                    "url": str(h.get("url") or f"https://news.ycombinator.com/item?id={h['objectID']}"),
                    "source": "hackernews",
                    "summary": str(h.get("title", "")),
                    "tags": [],
                    "score": int(h.get("points", 0)),
                    "publishedAt": int(h.get("created_at_i", 0)) * 1000,
                }
                for h in hits
                if h.get("title")
            ]
        except Exception:
            raise (primary_exc or Exception("HN all sources failed"))

    @staticmethod
    def _fmt_hn_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [
            {
                "id": f"hn-{i['id']}",
                "title": str(i.get("title", "")),
                "url": str(i.get("url", f"https://news.ycombinator.com/item?id={i['id']}")),
                "source": "hackernews",
                "summary": str(i.get("text", "") or i.get("title", "")),
                "tags": [],
                "score": int(i.get("score", 0)),
                "publishedAt": int(i.get("time", 0)) * 1000,
            }
            for i in items
            if i
        ]

    async def _devto(self, s: aiohttp.ClientSession) -> list[dict[str, Any]]:
        primary_exc: Exception | None = None
        try:
            async with s.get(
                "https://dev.to/api/articles?top=1&per_page=12",
                timeout=ITEM_TIMEOUT,
            ) as r:
                data = await r.json()
            out: list[dict[str, Any]] = []
            for i in data or []:
                ts = 0
                if i.get("published_at"):
                    try:
                        dt = datetime.fromisoformat(i["published_at"].replace("Z", "+00:00"))
                        ts = int(dt.timestamp() * 1000)
                    except Exception:
                        pass
                out.append(
                    {
                        "id": f"devto-{i['id']}",
                        "title": str(i.get("title", "")),
                        "url": str(i.get("url", "")),
                        "source": "devto",
                        "summary": _strip_html(str(i.get("description", "") or i.get("title", ""))),
                        "tags": list(i.get("tag_list", [])),
                        "score": int(i.get("positive_reactions_count", 0)),
                        "publishedAt": ts,
                    }
                )
            return out
        except Exception as exc:
            primary_exc = exc
            result = await self._fetch_rss(s, "https://dev.to/feed", "devto", limit=12)
            if result:
                return result
            raise primary_exc

    async def _lobsters(self, s: aiohttp.ClientSession) -> list[dict[str, Any]]:
        return await self._fetch_rss(s, "https://lobste.rs/rss", "lobsters", limit=12)

    async def _github_trending(self, s: aiohttp.ClientSession) -> list[dict[str, Any]]:
        languages = ["", "python", "typescript", "rust", "go"]
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "text/html",
            "Accept-Language": "en-US,en;q=0.9",
        }

        async def _fetch_one(lang: str) -> list[dict[str, Any]]:
            url = "https://github.com/trending"
            if lang:
                url += f"/{lang}"
            url += "?since=daily"
            try:
                async with s.get(url, headers=headers, timeout=ITEM_TIMEOUT) as r:
                    text = await r.text()
                return self._parse_github_trending_html(text)
            except Exception as exc:
                logger.debug("GitHub trending %s failed: %s", lang or "all", exc)
                return []

        results = await asyncio.gather(*[_fetch_one(lang) for lang in languages], return_exceptions=False)
        all_items: list[dict[str, Any]] = []
        seen: set[str] = set()
        for result in results:
            for item in result:
                if item["id"] in seen:
                    continue
                seen.add(item["id"])
                all_items.append(item)
        return all_items[:15]

    @staticmethod
    def _parse_github_trending_html(html_text: str) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        article_pattern = re.compile(
            r'<article[^>]*class="Box-row"[^>]*>(.*?)</article>',
            re.DOTALL,
        )
        for match in article_pattern.finditer(html_text):
            article = match.group(1)
            repo_match = re.search(r'<h2[^>]*class="h3[^"]*"[^>]*>.*?<a[^>]*href="([^"]+)"[^>]*>(.*?)</a>', article, re.DOTALL)
            if not repo_match:
                continue
            href = repo_match.group(1).strip()
            full_name = href.strip("/")
            desc_match = re.search(r'<p[^>]*class="col-9[^"]*"[^>]*>(.*?)</p>', article, re.DOTALL)
            desc = _strip_html(desc_match.group(1)) if desc_match else ""
            lang_match = re.search(r'<span[^>]*itemprop="programmingLanguage"[^>]*>(.*?)</span>', article, re.DOTALL)
            lang = _strip_html(lang_match.group(1)) if lang_match else ""
            stars_match = re.search(r'href="/[^"]+/stargazers"[^>]*>(.*?)</a>', article, re.DOTALL)
            stars_text = _strip_html(stars_match.group(1)) if stars_match else "0"
            stars = int(re.sub(r"[^\d]", "", stars_text) or "0")
            today_stars_match = re.search(r"([\d,]+)\s+stars\s+today", article)
            today_stars = int(re.sub(r"[^\d]", "", today_stars_match.group(1) or "0")) if today_stars_match else 0
            url = f"https://github.com/{full_name}"
            items.append(
                {
                    "id": f"gh-{full_name}",
                    "title": full_name,
                    "url": url,
                    "source": "github",
                    "summary": desc,
                    "tags": [lang] if lang else [],
                    "score": today_stars if today_stars > 0 else stars,
                    "publishedAt": 0,
                }
            )
        return items

    async def _producthunt(self, s: aiohttp.ClientSession) -> list[dict[str, Any]]:
        try:
            items = await self._fetch_rss(s, "https://www.producthunt.com/feed", "producthunt", limit=10)
            if items:
                return items
        except Exception as exc:
            logger.debug("ProductHunt RSS failed: %s", exc)
        items: list[dict[str, Any]] = []
        try:
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                "Accept": "text/html,application/xhtml+xml",
                "Accept-Language": "en-US,en;q=0.9",
            }
            async with s.get("https://www.producthunt.com/", headers=headers, timeout=ITEM_TIMEOUT) as r:
                if r.status == 200:
                    text = await r.text()
                    items = self._parse_producthunt_html(text)
        except Exception as exc:
            logger.debug("ProductHunt HTML failed: %s", exc)
        return items[:10]

    @staticmethod
    def _parse_producthunt_html(html_text: str) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        next_data = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', html_text, re.DOTALL)
        if not next_data:
            return items
        try:
            data = json.loads(next_data.group(1))
            posts = []
            apollo = data.get("props", {}).get("apolloState", {}) if isinstance(data.get("props"), dict) else {}
            for key, val in apollo.items():
                if isinstance(val, dict) and val.get("__typename") == "Post" and val.get("name"):
                    posts.append(val)
            for p in posts[:10]:
                slug = p.get("slug", "")
                name = p.get("name", "")
                tagline = p.get("tagline", "")
                votes = int(p.get("votesCount", 0))
                url = f"https://www.producthunt.com/posts/{slug}" if slug else ""
                items.append(
                    {
                        "id": f"ph-{slug or name}",
                        "title": name,
                        "url": url,
                        "source": "producthunt",
                        "summary": tagline,
                        "tags": [],
                        "score": votes,
                        "publishedAt": 0,
                    }
                )
        except Exception as exc:
            logger.debug("parse ProductHunt __NEXT_DATA__ failed: %s", exc)
        return items

    async def _solidot(self, s: aiohttp.ClientSession) -> list[dict[str, Any]]:
        return await self._fetch_rss(s, "https://www.solidot.org/index.rss", "solidot", limit=10)

    async def _oschina(self, s: aiohttp.ClientSession) -> list[dict[str, Any]]:
        return await self._fetch_rss(s, "https://www.oschina.net/news/rss", "oschina", limit=10)

    async def _juejin(self, s: aiohttp.ClientSession) -> list[dict[str, Any]]:
        return await self._fetch_rss(s, "https://juejin.cn/rss", "juejin", limit=10)

    async def _36kr(self, s: aiohttp.ClientSession) -> list[dict[str, Any]]:
        try:
            items = await self._fetch_rss(s, "https://36kr.com/feed", "36kr", limit=10)
            if items:
                return items
        except Exception:
            pass
        try:
            headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
            async with s.get(
                "https://36kr.com/newsflashes",
                headers=headers,
                timeout=ITEM_TIMEOUT,
            ) as r:
                text = await r.text()
            return self._parse_simple_html_list(text, "36kr", "https://36kr.com")
        except Exception:
            return []

    async def _infoq(self, s: aiohttp.ClientSession) -> list[dict[str, Any]]:
        items = await self._fetch_rss(s, "https://www.infoq.cn/feed", "infoq", limit=10)
        if items:
            return items
        return await self._fetch_rss(s, "https://feed.infoq.com/", "infoq", limit=10)

    async def _sspai(self, s: aiohttp.ClientSession) -> list[dict[str, Any]]:
        return await self._fetch_rss(s, "https://sspai.com/feed", "sspai", limit=10)

    async def _techcrunch(self, s: aiohttp.ClientSession) -> list[dict[str, Any]]:
        return await self._fetch_rss(s, "https://techcrunch.com/feed/", "techcrunch", limit=10)

    async def _arstechnica(self, s: aiohttp.ClientSession) -> list[dict[str, Any]]:
        return await self._fetch_rss(s, "https://feeds.arstechnica.com/arstechnica/index", "arstechnica", limit=10)

    async def _theverge(self, s: aiohttp.ClientSession) -> list[dict[str, Any]]:
        return await self._fetch_rss(s, "https://www.theverge.com/rss/index.xml", "theverge", limit=10)

    async def _smashing(self, s: aiohttp.ClientSession) -> list[dict[str, Any]]:
        return await self._fetch_rss(s, "https://www.smashingmagazine.com/feed/", "smashing", limit=8)

    async def _mittr(self, s: aiohttp.ClientSession) -> list[dict[str, Any]]:
        return await self._fetch_rss(s, "https://www.technologyreview.com/feed/", "mittr", limit=8)

    async def _jiqizhixin(self, s: aiohttp.ClientSession) -> list[dict[str, Any]]:
        return await self._fetch_rss(s, "https://www.jiqizhixin.com/rss", "jiqizhixin", limit=10)

    async def _ruanyifeng(self, s: aiohttp.ClientSession) -> list[dict[str, Any]]:
        items = await self._fetch_rss(s, "http://www.ruanyifeng.com/blog/atom.xml", "ruanyifeng", limit=5)
        if not items:
            items = await self._fetch_rss(s, "https://www.ruanyifeng.com/blog/atom.xml", "ruanyifeng", limit=5)
        return items

    async def _ifanr(self, s: aiohttp.ClientSession) -> list[dict[str, Any]]:
        return await self._fetch_rss(s, "https://www.ifanr.com/feed", "ifanr", limit=10)

    async def _cnbeta(self, s: aiohttp.ClientSession) -> list[dict[str, Any]]:
        # 主站 backend.php 已改为反爬 JS 挑战页（返回 HTML/JS，非 RSS），
        # 改用备用镜像 feed rss.cnbeta.com.tw（2026-08-18 验证返回 150 条）。
        # 该域 SSL 证书链不完整（CA 缺失），需关闭校验才能访问。
        import ssl

        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        return await self._fetch_rss(
            s,
            "https://rss.cnbeta.com.tw/rss",
            "cnbeta",
            limit=10,
            ssl=ctx,
        )

    # ── 2026-08-18 新增源（纯 RSS/Atom，走通用 _fetch_rss，已实测可达）──

    async def _qbitai(self, s: aiohttp.ClientSession) -> list[dict[str, Any]]:
        """量子位 — 中文 AI 头条（AI 板块高频补充源）。"""
        return await self._fetch_rss(s, "https://www.qbitai.com/feed", "qbitai", limit=10)

    async def _ithome(self, s: aiohttp.ClientSession) -> list[dict[str, Any]]:
        """IT之家 — 综合科技资讯（中文科技高频源）。"""
        return await self._fetch_rss(s, "https://www.ithome.com/rss/", "ithome", limit=10)

    async def _gcores(self, s: aiohttp.ClientSession) -> list[dict[str, Any]]:
        """机核 GCORES — 游戏与亚文化深度内容（游戏板块）。"""
        return await self._fetch_rss(s, "https://www.gcores.com/rss", "gcores", limit=10)

    async def _chuapp(self, s: aiohttp.ClientSession) -> list[dict[str, Any]]:
        """触乐 — 游戏行业资讯（游戏板块补充）。"""
        return await self._fetch_rss(s, "https://www.chuapp.com/feed", "chuapp", limit=10)

    async def _arxiv_ai(self, s: aiohttp.ClientSession) -> list[dict[str, Any]]:
        """arXiv cs.AI — 每日 AI 论文动态（AI 板块前沿源）。"""
        return await self._fetch_rss(s, "https://export.arxiv.org/rss/cs.AI", "arxiv_ai", limit=10)

    async def _simonwillison(self, s: aiohttp.ClientSession) -> list[dict[str, Any]]:
        """Simon Willison — AI 生态深度观察（AI 板块英文源）。"""
        return await self._fetch_rss(
            s,
            "https://simonwillison.net/atom/everything/",
            "simonwillison",
            limit=8,
        )

    # ── 2026-09 新增源（16 号文档信源军火库 · 政府站点无 RSS，直连 HTML 列表页，已实测可达）──

    async def _fetch_html_list(
        self,
        s: aiohttp.ClientSession,
        url: str,
        source: str,
        base_url: str,
        href_pat: str,
        limit: int = 12,
        ssl: Any = None,
    ) -> list[dict[str, Any]]:
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
        try:
            async with s.get(url, headers=headers, timeout=ITEM_TIMEOUT, ssl=ssl) as r:
                text = await r.text()
        except Exception as exc:
            logger.warning("HTML list fetch failed for %s [%s]: %s", source, url, exc)
            return []
        return self._parse_html_news_list(text, source, base_url, href_pat, limit)

    @staticmethod
    def _parse_html_news_list(
        text: str,
        source: str,
        base_url: str,
        href_pat: str,
        limit: int = 12,
    ) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        seen: set[str] = set()
        link_pat = re.compile(
            rf'<a[^>]*href="({href_pat})"[^>]*>(.*?)</a>',
            re.DOTALL | re.IGNORECASE,
        )
        text_date_pat = re.compile(r"(\d{4})[-/年](\d{1,2})[-/月](\d{1,2})日?")
        for m in link_pat.finditer(text or ""):
            href = html.unescape(m.group(1)).strip()
            title = _strip_html(m.group(2)).strip()
            if len(title) < 8:
                continue
            url = urljoin(base_url, href)
            if url in seen:
                continue
            seen.add(url)
            dm = re.search(r"t(\d{4})(\d{2})(\d{2})_", href) or text_date_pat.search(text[m.end() : m.end() + 300])
            published_at = 0
            if dm:
                try:
                    dt = datetime(
                        int(dm.group(1)),
                        int(dm.group(2)),
                        int(dm.group(3)),
                        tzinfo=timezone(timedelta(hours=8)),
                    )
                    published_at = int(dt.timestamp() * 1000)
                except Exception:
                    pass
            filename = href.rstrip("/").rsplit("/", 1)[-1] or href
            items.append(
                {
                    "id": f"{source}-{filename}",
                    "title": title,
                    "url": url,
                    "source": source,
                    "summary": title,
                    "tags": [],
                    "score": 0,
                    "publishedAt": published_at,
                }
            )
            if len(items) >= limit:
                break
        return items

    async def _govpolicy(self, s: aiohttp.ClientSession) -> list[dict[str, Any]]:
        """国务院文件 — 政策文件库最新文件（政策板块·一手文书源）。"""
        return await self._fetch_html_list(
            s,
            "https://www.gov.cn/zhengce/zhengceku/gwywj/home.htm",
            "govpolicy",
            base_url="https://www.gov.cn/zhengce/zhengceku/gwywj/home.htm",
            href_pat=r'[^"]*?/\d{6}/content_\d+\.htm',
        )

    async def _statsgov(self, s: aiohttp.ClientSession) -> list[dict[str, Any]]:
        """国家统计局 — 最新发布列表（政策板块·一手数据源）。"""
        return await self._fetch_html_list(
            s,
            "https://www.stats.gov.cn/sj/zxfb/",
            "statsgov",
            base_url="https://www.stats.gov.cn/sj/zxfb/",
            href_pat=r'[^"]*?/\d{6}/t\d{8}_\d+\.html',
        )

    async def _moe(self, s: aiohttp.ClientSession) -> list[dict[str, Any]]:
        """教育部 — 新闻发布列表（政策板块·教育一手源）。"""
        return await self._fetch_html_list(
            s,
            "https://www.moe.gov.cn/jyb_xwfb/",
            "moe",
            base_url="https://www.moe.gov.cn/jyb_xwfb/",
            href_pat=r'[^"]*?t\d{8}_\d+\.html',
        )

    async def _ynzs(self, s: aiohttp.ClientSession) -> list[dict[str, Any]]:
        """云南省招生考试院 — 招考公告要闻（同城板块·本地一手源）。"""
        return await self._fetch_html_list(
            s,
            "https://www.ynzs.cn/",
            "ynzs",
            base_url="https://www.ynzs.cn/",
            href_pat=r"(?:https?://www\.ynzs\.cn)?/?html/content/\d+\.html",
        )

    @staticmethod
    def _parse_simple_html_list(text: str, source: str, base_url: str) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        for m in re.finditer(r'<a[^>]*href="(/[^"]+)"[^>]*>([^<]{8,80})</a>', text):
            href = m.group(1)
            title = _strip_html(m.group(2))
            if not title or len(title) < 6:
                continue
            items.append(
                {
                    "id": f"{source}-{href}",
                    "title": title,
                    "url": base_url + href,
                    "source": source,
                    "summary": title,
                    "tags": [],
                    "score": 0,
                    "publishedAt": 0,
                }
            )
            if len(items) >= 10:
                break
        return items

    async def _fetch_rss(
        self,
        s: aiohttp.ClientSession,
        url: str,
        source: str,
        limit: int = 10,
        ssl: Any = None,
    ) -> list[dict[str, Any]]:
        try:
            headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
            async with s.get(url, headers=headers, timeout=ITEM_TIMEOUT, ssl=ssl) as r:
                text = await r.text()
            # 清洗非法 XML 控制字符（XML 1.0 仅允许 \t \n \r 及 0x20 以上），
            # 部分源混入非法字符导致 ET 严格解析报 "not well-formed"。
            text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", text or "")
            try:
                root = ET.fromstring(text)
            except Exception:
                # 严格解析失败（部分源反爬页含非 RSS 内容）时用宽松正则兜底，
                # 能提取出 item/entry 就返回，否则返回空列表由上层决定降级。
                return self._parse_rss_tolerant(text, source, limit)
            ns = {
                "atom": "http://www.w3.org/2005/Atom",
                "content": "http://purl.org/rss/1.0/modules/content/",
                "dc": "http://purl.org/dc/elements/1.1/",
            }
            items: list[dict[str, Any]] = []
            for entry in root.findall(".//item") or root.findall(".//atom:entry", ns):
                if len(items) >= limit:
                    break
                title = _e(entry, "title")
                link = _e(entry, "link")
                pub_str = (
                    _e(entry, "pubDate")
                    or _e(
                        entry,
                        "dc:date",
                    )
                    or _e(entry, "updated")
                )
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
                raw_desc = _e(entry, "description") or _e(entry, "summary") or _e(entry, "content:encoded") or title
                summary = _strip_html(raw_desc)[:500]
                tag_str = _e(entry, "category")
                tags = [t.strip() for t in tag_str.split(",")] if tag_str else []
                items.append(
                    {
                        "id": f"{source}-rss-{len(items)}",
                        "title": title,
                        "url": link or "",
                        "source": source,
                        "summary": summary,
                        "tags": tags,
                        "score": 0,
                        "publishedAt": ts,
                    }
                )
            return items
        except Exception as exc:
            logger.warning("RSS fetch failed for %s [%s]: %s", source, url, exc)
            return []

    @staticmethod
    def _parse_rss_tolerant(text: str, source: str, limit: int = 10) -> list[dict[str, Any]]:
        """宽松正则解析 RSS/Atom，作为 ET.fromstring 严格解析失败时的兜底。

        部分源会返回含 JS/HTML 的反爬页面，或混入非法字符导致 XML 解析失败；
        只要页面里还存在 <item>/<entry> 块，就能尽量提取出可读条目。
        """
        items: list[dict[str, Any]] = []
        block_pat = re.compile(
            r"<(item|entry)\b[^>]*>(.*?)</\1>",
            re.DOTALL | re.IGNORECASE,
        )

        def _raw(block: str, tag: str) -> str:
            m = re.search(
                rf"<{tag}\b[^>]*>(.*?)</{tag}>",
                block,
                re.DOTALL | re.IGNORECASE,
            )
            if m:
                return m.group(1)
            if tag == "link":
                m = re.search(r'<link\b[^>]*href="([^"]+)"', block, re.IGNORECASE)
                if m:
                    return m.group(1)
            return ""

        for m in block_pat.finditer(text or ""):
            if len(items) >= limit:
                break
            block = m.group(2)
            title = _strip_html(_raw(block, "title"))
            link = _strip_html(_raw(block, "link"))
            if not title and not link:
                continue
            summary = _strip_html(_raw(block, "description") or _raw(block, "summary") or title)[:500]
            items.append(
                {
                    "id": f"{source}-rtol-{len(items)}",
                    "title": title,
                    "url": link or "",
                    "source": source,
                    "summary": summary,
                    "tags": [],
                    "score": 0,
                    "publishedAt": 0,
                }
            )
        return items

    # ── AI brief generation ──────────────────────────────────────────────
