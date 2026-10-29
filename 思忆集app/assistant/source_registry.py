"""信息源治理层：搜索源优先级、去重、可信度评分、摘要模板、失败回退。

所有联网搜集（web.search / web.fetch / news）的结果都必须经过此层过滤。
"""
from __future__ import annotations

import hashlib
import logging
import re
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger("siyi.assistant.source_registry")


# ── 可信度等级 ───────────────────────────────────────────
TRUST_OFFICIAL = "official"      # 官方 API / 权威数据
TRUST_AUTHORITY = "authority"    # 权威媒体 / 知名技术站
TRUST_COMMUNITY = "community"   # 社区内容
TRUST_GENERAL = "general"       # 通用搜索结果
TRUST_UNKNOWN = "unknown"       # 未知来源

TRUST_SCORE_MAP = {
    TRUST_OFFICIAL: 1.0,
    TRUST_AUTHORITY: 0.85,
    TRUST_COMMUNITY: 0.6,
    TRUST_GENERAL: 0.4,
    TRUST_UNKNOWN: 0.2,
}

# ── 内置源注册表 ─────────────────────────────────────────
_BUILTIN_SOURCES: list[dict[str, Any]] = [
    # 权威新闻
    {"source_id": "cctv", "name": "央视新闻", "trust": TRUST_AUTHORITY, "domain_pattern": r"cctv\.com|央视新闻", "category": "news"},
    {"source_id": "xinhua", "name": "新华网", "trust": TRUST_AUTHORITY, "domain_pattern": r"xinhuanet\.com|news\.cn", "category": "news"},
    {"source_id": "bbc", "name": "BBC", "trust": TRUST_AUTHORITY, "domain_pattern": r"bbc\.com|bbc\.co\.uk", "category": "news"},
    {"source_id": "reuters", "name": "Reuters", "trust": TRUST_AUTHORITY, "domain_pattern": r"reuters\.com", "category": "news"},
    # 技术权威
    {"source_id": "36kr", "name": "36氪", "trust": TRUST_AUTHORITY, "domain_pattern": r"36kr\.com", "category": "tech"},
    {"source_id": "techcrunch", "name": "TechCrunch", "trust": TRUST_AUTHORITY, "domain_pattern": r"techcrunch\.com", "category": "tech"},
    {"source_id": "hackernews", "name": "HackerNews", "trust": TRUST_COMMUNITY, "domain_pattern": r"news\.ycombinator\.com", "category": "tech"},
    {"source_id": "ithome", "name": "IT之家", "trust": TRUST_COMMUNITY, "domain_pattern": r"ithome\.com", "category": "tech"},
    {"source_id": "github", "name": "GitHub", "trust": TRUST_AUTHORITY, "domain_pattern": r"github\.com", "category": "code"},
    # 百科 / 文档
    {"source_id": "wikipedia", "name": "Wikipedia", "trust": TRUST_AUTHORITY, "domain_pattern": r"wikipedia\.org", "category": "reference"},
    {"source_id": "mdn", "name": "MDN", "trust": TRUST_OFFICIAL, "domain_pattern": r"developer\.mozilla\.org", "category": "reference"},
    {"source_id": "stackoverflow", "name": "StackOverflow", "trust": TRUST_COMMUNITY, "domain_pattern": r"stackoverflow\.com", "category": "qa"},
    # 搜索引擎
    {"source_id": "bing", "name": "Bing", "trust": TRUST_GENERAL, "domain_pattern": r"bing\.com", "category": "search"},
    {"source_id": "google", "name": "Google", "trust": TRUST_GENERAL, "domain_pattern": r"google\.com", "category": "search"},
]


class SourceEntry:
    """信息源条目。"""

    __slots__ = ("category", "domain_pattern", "enabled", "name", "priority", "source_id", "trust")

    def __init__(
        self,
        source_id: str,
        name: str,
        trust: str = TRUST_GENERAL,
        domain_pattern: str = "",
        category: str = "general",
        priority: int = 100,
        enabled: bool = True,
    ):
        self.source_id = source_id
        self.name = name
        self.trust = trust
        self.domain_pattern = domain_pattern
        self.category = category
        self.priority = priority
        self.enabled = enabled

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_id": self.source_id,
            "name": self.name,
            "trust": self.trust,
            "domain_pattern": self.domain_pattern,
            "category": self.category,
            "priority": self.priority,
            "enabled": self.enabled,
        }


class AssistantSourceRegistry:
    """信息源注册中心。"""

    def __init__(self, *, data_store=None, user_profile_service=None):
        self.data_store = data_store
        self._user_profile_service = user_profile_service
        self._sources: dict[str, SourceEntry] = {}
        self._load_builtin_sources()

    def _load_builtin_sources(self) -> None:
        for entry in _BUILTIN_SOURCES:
            self._sources[entry["source_id"]] = SourceEntry(**entry)

    def register_source(self, entry: SourceEntry) -> None:
        self._sources[entry.source_id] = entry

    def list_sources(self, *, category: str | None = None, enabled_only: bool = True) -> list[dict[str, Any]]:
        result = []
        for src in self._sources.values():
            if enabled_only and not src.enabled:
                continue
            if category and src.category != category:
                continue
            result.append(src.to_dict())
        return sorted(result, key=lambda x: x.get("priority", 100))

    # ── 可信度评分 ───────────────────────────────────────

    def score_item(self, item: dict[str, Any]) -> float:
        """对搜索结果条目打可信度分。"""
        url = str(item.get("url") or item.get("link") or "").lower()
        title = str(item.get("title") or "").strip()

        # 域名匹配
        trust_level = TRUST_UNKNOWN
        for src in self._sources.values():
            if src.domain_pattern and re.search(src.domain_pattern, url, re.IGNORECASE):
                trust_level = src.trust
                break

        base_score = TRUST_SCORE_MAP.get(trust_level, 0.2)

        # 标题质量加分
        if title and len(title) > 5:
            base_score += 0.05
        # 有日期加分
        if item.get("published_at") or item.get("date"):
            base_score += 0.05

        return min(1.0, base_score)

    def score_and_rank(self, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """给结果列表打分、排序。"""
        scored = []
        for item in items:
            entry = dict(item)
            entry["_trust_score"] = self.score_item(item)
            scored.append(entry)
        return sorted(scored, key=lambda x: x.get("_trust_score", 0), reverse=True)

    # ── 去重 ─────────────────────────────────────────────

    @staticmethod
    def deduplicate(items: list[dict[str, Any]], *, title_key: str = "title", url_key: str = "url") -> list[dict[str, Any]]:
        """标题 + URL 联合去重。"""
        seen_hashes: set = set()
        result: list[dict[str, Any]] = []
        for item in items:
            title = str(item.get(title_key) or "").strip().lower()
            url = str(item.get(url_key) or "").strip().lower()
            fingerprint = hashlib.md5(f"{title}|{url}".encode(), usedforsecurity=False).hexdigest()
            if fingerprint in seen_hashes:
                continue
            seen_hashes.add(fingerprint)
            result.append(item)
        return result

    # ── 摘要模板 ─────────────────────────────────────────

    def build_summary_prompt(self, items: list[dict[str, Any]], *, style: str = "concise") -> str:
        """根据画像输出风格生成摘要 prompt。"""
        if self._user_profile_service is not None:
            style = self._user_profile_service.get_summary_style() or style

        items_text = "\n".join(
            f"- [{item.get('title', '无标题')}]({item.get('url', '')}) (可信度: {item.get('_trust_score', 'N/A')})"
            for item in items[:15]
        )

        if style == "detailed":
            return (
                f"请对以下信息源做详细摘要，包括关键数据和引用来源：\n\n{items_text}\n\n"
                "要求：按重要性排序，每条 2-3 句详细说明。"
            )
        if style == "bullet":
            return (
                f"请对以下信息源做要点摘要：\n\n{items_text}\n\n"
                "要求：每条一行，不超过 20 字，不含解释。"
            )
        # concise (default)
        return (
            f"请对以下信息源做简洁摘要：\n\n{items_text}\n\n"
            "要求：简明扼要，每条一句话，按重要性排序。"
        )

    # ── 失败回退 ─────────────────────────────────────────

    @staticmethod
    def build_fallback_response(
        query: str,
        error: str,
        *,
        cached_items: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """搜索失败时的回退响应。"""
        response: dict[str, Any] = {
            "success": False,
            "fallback": True,
            "query": query,
            "error": error,
            "suggestion": f"联网搜索“{query}”暂时失败，",
        }
        if cached_items:
            response["cached_items"] = cached_items[:5]
            response["suggestion"] += f"已返回 {len(cached_items[:5])} 条缓存结果。"
        else:
            response["suggestion"] += "请稍后重试，或尝试换个关键词。"
        return response

    # ── 源优先级排序 ─────────────────────────────────────

    def get_preferred_sources(self, category: str = "") -> list[str]:
        """返回按优先级排序的源 ID 列表（结合用户画像）。"""
        user_sources: list[str] = []
        if self._user_profile_service is not None:
            user_sources = self._user_profile_service.get_common_sources()

        all_sources = self.list_sources(category=category or None)
        ordered_ids: list[str] = []

        # 用户偏好源优先
        for src_name in user_sources:
            for s in all_sources:
                if s["name"] == src_name or s["source_id"] == src_name:
                    if s["source_id"] not in ordered_ids:
                        ordered_ids.append(s["source_id"])

        # 其余按 trust 排序
        for s in sorted(all_sources, key=lambda x: TRUST_SCORE_MAP.get(x.get("trust", ""), 0), reverse=True):
            if s["source_id"] not in ordered_ids:
                ordered_ids.append(s["source_id"])

        return ordered_ids

    # ── 完整处理流水线 ───────────────────────────────────

    def process_search_results(
        self,
        items: list[dict[str, Any]],
        *,
        query: str = "",
        max_items: int = 10,
    ) -> dict[str, Any]:
        """完整处理：去重 → 评分 → 排序 → 截断。"""
        deduped = self.deduplicate(items)
        ranked = self.score_and_rank(deduped)
        final = ranked[:max_items]
        return {
            "items": final,
            "original_count": len(items),
            "deduped_count": len(deduped),
            "final_count": len(final),
            "query": query,
            "processed_at": datetime.now(timezone.utc).isoformat(),
        }
