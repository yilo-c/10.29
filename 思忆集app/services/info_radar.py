"""信息雷达 — 扫描用户关注领域的最新动态。

基于 TechRadar 设计模式，但扫描用户关注领域的信息而非依赖包：
- RSS 订阅源扫描
- 聚焦话题的网络搜索
- 用户关注领域的新工具发现

结果汇入 SuggestionEngine「信息类」建议。
"""
from __future__ import annotations

import json
import logging
import re
import urllib.request
import urllib.parse
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from urllib.error import URLError

logger = logging.getLogger("siyi.info_radar")

# 默认扫描源
DEFAULT_RSS_FEEDS: list[dict[str, str]] = [
    {"name": "HackerNews", "url": "https://hnrss.org/frontpage", "lang": "en"},
    {"name": "36氪", "url": "https://36kr.com/feed", "lang": "zh"},
]

# 默认聚焦话题（无画像时使用）
DEFAULT_FOCUS_TOPICS = ["AI", "自动化", "软件工程", "生产力工具"]

# 每条搜索结果的最低相关性分数（简单关键词匹配）
_MIN_RELEVANCE_SCORE = 1


@dataclass
class InfoFinding:
    """信息雷达的单条发现。"""
    name: str
    url: str = ""
    description: str = ""
    source: str = ""          # rss | web_search | tool_discovery
    topic: str = ""           # 所属话题
    relevance_score: float = 0.0
    published_at: str = ""
    found_at: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "url": self.url,
            "description": self.description,
            "source": self.source,
            "topic": self.topic,
            "relevance_score": self.relevance_score,
            "published_at": self.published_at,
            "found_at": self.found_at or datetime.now(timezone.utc).isoformat(),
        }


class InfoRadar:
    """信息雷达。

    职责：
    1. 根据用户关注话题扫描 RSS 和信息源
    2. 发现与用户兴趣相关的新文章、工具
    3. 为 SuggestionEngine 提供输入
    """

    def __init__(
        self,
        user_profile_service=None,
        rss_feeds: list[dict[str, str]] | None = None,
        web_search_service=None,
    ):
        self._profile_svc = user_profile_service
        self._rss_feeds = rss_feeds or DEFAULT_RSS_FEEDS
        self._web_search = web_search_service
        self._findings: list[InfoFinding] = []

    def scan(self) -> list[InfoFinding]:
        """执行一次完整扫描。"""
        topics = self._get_focus_topics()
        findings: list[InfoFinding] = []

        findings.extend(self._scan_rss_feeds(topics))
        findings.extend(self._scan_web_search(topics))
        findings.extend(self._scan_tools(topics))

        # 按相关性排序
        findings.sort(key=lambda f: (-f.relevance_score, f.found_at))
        self._findings = findings
        return findings

    def get_recent_findings(self, limit: int = 10) -> list[InfoFinding]:
        return self._findings[:limit]

    # ── 焦点话题 ───────────────────────────────────────────

    def _get_focus_topics(self) -> list[str]:
        """从用户画像读取关注话题。"""
        if self._profile_svc is None:
            return list(DEFAULT_FOCUS_TOPICS)
        try:
            profile = self._profile_svc.load()
            topics = profile.get("long_term", {}).get("focus_topics", [])
            return topics if topics else list(DEFAULT_FOCUS_TOPICS)
        except Exception:
            return list(DEFAULT_FOCUS_TOPICS)

    # ── RSS 扫描 ───────────────────────────────────────────

    def _scan_rss_feeds(self, topics: list[str]) -> list[InfoFinding]:
        """扫描 RSS 订阅源。"""
        results: list[InfoFinding] = []
        for feed in self._rss_feeds:
            try:
                items = self._fetch_and_parse_rss(feed)
                for item in items[:5]:
                    score = self._compute_relevance(item.get("title", ""), topics)
                    if score >= _MIN_RELEVANCE_SCORE:
                        results.append(InfoFinding(
                            name=item.get("title", "")[:120],
                            url=item.get("link", ""),
                            description=item.get("description", "")[:200],
                            source="rss",
                            topic=feed["name"],
                            relevance_score=score,
                            published_at=item.get("pub_date", ""),
                        ))
            except Exception as exc:
                logger.debug("RSS 源 %s 扫描失败: %s", feed.get("name"), exc)
        return results

    def _fetch_and_parse_rss(self, feed: dict[str, str]) -> list[dict[str, str]]:
        """抓取并解析 RSS feed（简化实现：正则提取标题/链接）。

        P0-5 修复：经 services.url_safety.safe_fetch_text 走统一 SSRF 防护，
        防止 RSS feed URL 被改成 http://127.0.0.1:18765/、http://169.254.169.254/
        等内部地址。max_bytes 上限保护内存。
        """
        from services.url_safety import SSRFBlocked, safe_fetch_text

        url = feed.get("url", "")
        try:
            html = safe_fetch_text(
                url,
                timeout=10,
                headers={"User-Agent": "siyi-info-radar/1.0"},
                max_bytes=5 * 1024 * 1024,  # 5MB feed 上限
            )
        except SSRFBlocked as exc:
            logger.warning("RSS 源 %s 被 SSRF 防护拦截：%s", feed.get("name"), exc)
            return []

        items: list[dict[str, str]] = []
        # 简单正则解析（不依赖 feedparser）
        for entry in re.finditer(r"<entry>|<item>(.*?)</entry>|</item>", html, re.DOTALL):
            content = entry.group(0)
            title = self._extract_tag(content, "title")
            link = self._extract_tag(content, "link") or ""
            # <link> may be <link href="..."/> or <link>...</link>
            href_match = re.search(r'href="([^"]+)"', link)
            if href_match:
                link = href_match.group(1)
            desc = self._extract_tag(content, "description") or self._extract_tag(content, "summary")
            pub_date = self._extract_tag(content, "published") or self._extract_tag(content, "pubDate")
            if title:
                items.append({"title": title, "link": link, "description": desc, "pub_date": pub_date})

        # 如果上面正则解析失败，退一步用更简单的方法
        if not items:
            titles = re.findall(r"<title[^>]*>(.*?)</title>", html, re.DOTALL)
            links = re.findall(r"<link[^>]*>(.*?)</link>", html, re.DOTALL)
            for i, t in enumerate(titles):
                link = links[i] if i < len(links) else ""
                href_match = re.search(r'href="([^"]+)"', link)
                if href_match:
                    link = href_match.group(1)
                items.append({"title": t.strip(), "link": link.strip(), "description": "", "pub_date": ""})

        return items

    @staticmethod
    def _extract_tag(html: str, tag: str) -> str:
        m = re.search(f"<{tag}[^>]*>(.*?)</{tag}>", html, re.DOTALL)
        return m.group(1).strip() if m else ""

    # ── Web 搜索 ───────────────────────────────────────────

    def _scan_web_search(self, topics: list[str]) -> list[InfoFinding]:
        """对焦点话题做 Web 搜索。"""
        if self._web_search is None:
            return []
        results: list[InfoFinding] = []
        for topic in topics[:3]:
            try:
                search_results = self._web_search.search(query=topic, num_results=3)
                for sr in search_results:
                    score = self._compute_relevance(sr.title + " " + sr.snippet, topics)
                    if score >= _MIN_RELEVANCE_SCORE:
                        results.append(InfoFinding(
                            name=sr.title[:120],
                            url=sr.url,
                            description=sr.snippet[:200],
                            source="web_search",
                            topic=topic,
                            relevance_score=score,
                        ))
            except Exception as exc:
                logger.debug("话题 %s 搜索失败: %s", topic, exc)
        return results

    # ── 工具发现 ───────────────────────────────────────────

    def _scan_tools(self, topics: list[str]) -> list[InfoFinding]:
        """检查用户关注领域的新工具。"""
        # 预留：可扫描 ProductHunt / GitHub Trending / 技术评测
        _ = topics
        return []

    # ── 相关性 ─────────────────────────────────────────────

    @staticmethod
    def _compute_relevance(text: str, topics: list[str]) -> float:
        """简单关键词匹配计算相关性分数。"""
        if not text or not topics:
            return 0.0
        text_lower = text.lower()
        score = 0.0
        for topic in topics:
            if topic.lower() in text_lower:
                score += 1.0
                # 更长的匹配 = 更相关
                score += len(topic) / 20.0
        return score
