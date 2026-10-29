"""Web 搜索服务。"""

from __future__ import annotations

import hashlib
import html
import json
import logging
import os
import re
import time
from typing import Any
from urllib.parse import urlparse

import requests

from .prompt_shield import wrap_untrusted

logger = logging.getLogger("siyi.web_search")

_USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"


def _strip_tags(text: str) -> str:
    text = re.sub(r"<script[\s\S]*?</script>", "", text, flags=re.I)
    text = re.sub(r"<style[\s\S]*?</style>", "", text, flags=re.I)
    text = re.sub(r"<[^>]+>", "", text)
    return html.unescape(text).strip()


def _normalize_ws(text: str) -> str:
    text = re.sub(r"[ \t]+", " ", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def _extract_duckduckgo_url(url_raw: str) -> str:
    actual_url = url_raw
    uddg_match = re.search(r"uddg=([^&]+)", url_raw)
    if uddg_match:
        from urllib.parse import unquote

        actual_url = unquote(uddg_match.group(1))
    return actual_url


# ── 质量评分 ─────────────────────────────────────────────
_AUTHORITY_DOMAINS: dict[str, float] = {
    # 高权威域名加分
    ".edu": 0.25,
    ".gov": 0.25,
    ".org": 0.1,
    "arxiv.org": 0.3,
    "github.com": 0.15,
    " scholar.google.com": 0.2,
    "wikipedia.org": 0.15,
    "nature.com": 0.25,
    "ieee.org": 0.2,
    "acm.org": 0.2,
    "sciencedirect.com": 0.2,
}


def score_search_result(result: WebSearchResult, *, query: str = "") -> float:
    """对搜索结果进行质量评分（0.0-1.0）。

    综合考虑：新鲜度、来源权威性、内容长度、标题与查询相关性。
    """
    score = 0.4  # 基础分

    # 1. 来源权威性
    url_lower = (result.url or "").lower()
    for domain, boost in _AUTHORITY_DOMAINS.items():
        if domain in url_lower:
            score += boost
            break
    # 降级：低质量域名
    if any(d in url_lower for d in ("baidu.com", "zhidao", "toutiao")):
        score -= 0.1

    # 2. 内容质量（摘要长度和完整度）
    snippet_len = len(result.snippet or "")
    if snippet_len > 300:
        score += 0.15
    elif snippet_len > 150:
        score += 0.1
    elif snippet_len < 30:
        score -= 0.1

    # 3. 标题质量（非空、非纯 URL、有实际内容）
    title = (result.title or "").strip()
    if not title:
        score -= 0.15
    elif len(title) < 5:
        score -= 0.05
    elif title.lower().startswith(("http", "www.")):
        score -= 0.1

    # 4. 查询相关性（标题中命中查询关键词）
    if query:
        query_terms = {w.lower() for w in query.split() if len(w) > 1}
        title_lower = title.lower()
        if query_terms:
            hits = sum(1 for t in query_terms if t in title_lower)
            score += min(0.2, hits * 0.05)

    return max(0.0, min(1.0, score))


def filter_by_quality(
    results: list[WebSearchResult],
    *,
    min_score: float = 0.3,
    query: str = "",
    top_k: int = 0,
) -> list[WebSearchResult]:
    """按质量评分过滤和排序搜索结果。

    Args:
        results: 搜索结果列表。
        min_score: 最低质量阈值（0-1），低于此值的结果被过滤。
        query: 查询文本，用于相关性评分。
        top_k: 如果 >0，只返回前 k 个最高分结果。

    Returns:
        按质量评分降序排列的结果列表。
    """
    scored = [(score_search_result(r, query=query), r) for r in results]
    scored.sort(key=lambda x: x[0], reverse=True)
    filtered = [r for s, r in scored if s >= min_score]
    if top_k > 0:
        filtered = filtered[:top_k]
    return filtered


class WebSearchResult:
    __slots__ = ("quality_score", "snippet", "title", "url")

    def __init__(self, title: str, url: str, snippet: str, quality_score: float = 0.0):
        self.title = title
        self.url = url
        self.snippet = snippet
        self.quality_score = quality_score

    def to_dict(self) -> dict[str, str | float]:
        d: dict[str, str | float] = {"title": self.title, "url": self.url, "snippet": self.snippet}
        if self.quality_score:
            d["quality_score"] = self.quality_score
        return d


def _search_tavily(query: str, num_results: int, api_key: str, timeout: int = 15) -> list[WebSearchResult]:
    resp = requests.post(
        "https://api.tavily.com/search",
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json={"query": query, "max_results": num_results, "search_depth": "basic"},
        timeout=timeout,
    )
    resp.raise_for_status()
    results: list[WebSearchResult] = []
    for item in resp.json().get("results", []):
        results.append(
            WebSearchResult(
                title=_normalize_ws(_strip_tags(item.get("title", ""))),
                url=item.get("url", ""),
                snippet=_normalize_ws(_strip_tags(item.get("content", "")))[:500],
            )
        )
    return results[:num_results]


def _search_duckduckgo_html(query: str, num_results: int, timeout: int = 10) -> list[WebSearchResult]:
    resp = requests.get("https://html.duckduckgo.com/html/", params={"q": query}, headers={"User-Agent": _USER_AGENT}, timeout=timeout)
    resp.raise_for_status()
    results: list[WebSearchResult] = []
    for match in re.finditer(
        r'<a[^>]+class="result__a"[^>]+href="([^"]+)"[^>]*>(.*?)</a>.*?<a[^>]+class="result__snippet"[^>]*>(.*?)</a>', resp.text, flags=re.DOTALL
    ):
        url_raw = match.group(1)
        title = _strip_tags(match.group(2))
        snippet = _strip_tags(match.group(3))
        actual_url = _extract_duckduckgo_url(url_raw)
        if title:
            results.append(WebSearchResult(title=title, url=actual_url, snippet=snippet[:500]))
        if len(results) >= num_results:
            break
    return results


def _search_duckduckgo_lite(query: str, num_results: int, timeout: int = 10) -> list[WebSearchResult]:
    resp = requests.get("https://lite.duckduckgo.com/lite/", params={"q": query}, headers={"User-Agent": _USER_AGENT}, timeout=timeout)
    resp.raise_for_status()
    results: list[WebSearchResult] = []
    for match in re.finditer(
        r'<a[^>]+class="result-link"[^>]+href="([^"]+)"[^>]*>(.*?)</a>(.*?)(?=<a[^>]+class="result-link"|</body>|</html>)', resp.text, flags=re.DOTALL
    ):
        url_raw = match.group(1)
        title = _strip_tags(match.group(2))
        snippet_match = re.search(r'<td[^>]+class="result-snippet"[^>]*>(.*?)</td>', match.group(3), flags=re.DOTALL)
        snippet = _strip_tags(snippet_match.group(1)) if snippet_match else ""
        if title:
            results.append(WebSearchResult(title=title, url=_extract_duckduckgo_url(url_raw), snippet=snippet[:500]))
        if len(results) >= num_results:
            break
    return results


def _search_duckduckgo(query: str, num_results: int, timeout: int = 10) -> list[WebSearchResult]:
    try:
        results = _search_duckduckgo_html(query, num_results, timeout=timeout)
    except Exception:
        results = []
    if results:
        return results
    return _search_duckduckgo_lite(query, num_results, timeout=timeout)


def _search_searxng(query: str, num_results: int, base_url: str, timeout: int = 10) -> list[WebSearchResult]:
    parsed = urlparse(base_url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ValueError(f"无效的 SearXNG URL: {base_url}")
    resp = requests.get(
        f"{base_url.rstrip('/')}/search",
        params={"q": query, "format": "json", "language": "zh"},
        headers={"User-Agent": _USER_AGENT},
        timeout=timeout,
    )
    resp.raise_for_status()
    results: list[WebSearchResult] = []
    for item in resp.json().get("results", []):
        results.append(
            WebSearchResult(
                title=_normalize_ws(_strip_tags(item.get("title", ""))),
                url=item.get("url", ""),
                snippet=_normalize_ws(_strip_tags(item.get("content", "")))[:500],
            )
        )
    return results[:num_results]


class WebSearchService:
    # Implements WebSearchPort

    SUPPORTED_PROVIDERS = ("tavily", "duckduckgo", "searxng")
    CACHE_TTL_SECONDS = 3600

    def __init__(self, *, settings_repository=None, environ: dict | None = None):
        self._settings = settings_repository
        self._env = environ or os.environ
        self._data_store = getattr(settings_repository, "data_store", None)

    def _get_setting(self, key: str, default: str = "") -> str:
        if self._settings is not None:
            val = self._settings.get_setting(key, "")
            if isinstance(val, str) and val.strip():
                return self._decrypt_if_secure(val.strip())
        return str(self._env.get(key, default)).strip()

    @staticmethod
    def _decrypt_if_secure(value: str) -> str:
        from core.secure_key_storage import SecureKeyStorage

        secure = SecureKeyStorage(namespace="com.siyi.apikey")
        if secure.is_secure_token(value):
            return secure.decrypt(value)
        return value

    @property
    def _provider(self) -> str:
        p = self._get_setting("web_search_provider") or self._env.get("SIYI_WEB_SEARCH_PROVIDER", "")
        return p.strip().lower() if p else self._auto_detect_provider()

    def _auto_detect_provider(self) -> str:
        tavily_key = self._get_setting("web_search_tavily_api_key") or self._env.get("TAVILY_API_KEY", "")
        if tavily_key:
            return "tavily"
        if self._get_setting("searxng_base_url") or self._env.get("SIYI_SEARXNG_BASE_URL"):
            return "searxng"
        return "duckduckgo"

    def search(self, query: str, num_results: int = 5, provider: str | None = None) -> list[WebSearchResult]:
        query = str(query or "").strip()
        if not query:
            return []
        num_results = max(1, min(num_results, 10))
        chosen = (provider or self._provider).strip().lower()
        cached = self._load_cached_results(query=query, num_results=num_results, provider=chosen)
        if cached is not None:
            return cached
        try:
            if chosen == "tavily":
                api_key = self._get_setting("web_search_tavily_api_key") or self._env.get("TAVILY_API_KEY", "")
                if not api_key:
                    logger.warning("TAVILY_API_KEY 未设置，回退到 DuckDuckGo")
                    results = _search_duckduckgo(query, num_results)
                else:
                    results = _search_tavily(query, num_results, api_key)
            elif chosen == "searxng":
                base_url = self._get_setting("searxng_base_url") or self._env.get("SIYI_SEARXNG_BASE_URL", "")
                if not base_url:
                    logger.warning("SEARXNG_BASE_URL 未设置，回退到 DuckDuckGo")
                    results = _search_duckduckgo(query, num_results)
                else:
                    results = _search_searxng(query, num_results, base_url)
            else:
                results = _search_duckduckgo(query, num_results)
        except Exception as exc:
            logger.warning("Web 搜索失败 (provider=%s): %s", chosen, exc)
            if chosen != "duckduckgo":
                try:
                    results = _search_duckduckgo(query, num_results)
                except Exception as fallback_exc:
                    logger.warning("DuckDuckGo 回退也失败: %s", fallback_exc)
                    results = []
            else:
                results = []
        deduped = self._dedupe_results(results)[:num_results]
        self._save_cached_results(query=query, num_results=num_results, provider=chosen, results=deduped)
        return deduped

    def format_results_for_prompt(self, results: list[WebSearchResult], query: str = "") -> str:
        if not results:
            return f"未找到与「{query}」相关的搜索结果。" if query else "未找到搜索结果。"
        lines: list[str] = []
        for i, r in enumerate(results, 1):
            lines.append(f"{i}. {r.title}")
            lines.append(f"   链接: {r.url}")
            if r.snippet:
                lines.append(f"   摘要: {r.snippet}")
            lines.append("")
        # 安C2：搜索结果来自外部网页，进 prompt 前定界+中和（见 services/prompt_shield.py）
        block = "\n".join(lines)
        source = f"网络搜索({query})" if query else "网络搜索"
        header = f"以下是关于「{query}」的网络搜索结果："
        return f"{header}\n{wrap_untrusted(source, block)}"

    @staticmethod
    def _dedupe_results(results: list[WebSearchResult]) -> list[WebSearchResult]:
        deduped, seen = [], set()
        for item in results:
            key = (str(item.url or "").strip().lower(), str(item.title or "").strip().lower())
            if key in seen:
                continue
            seen.add(key)
            deduped.append(item)
        return deduped

    def _cache_key(self, *, query: str, num_results: int, provider: str) -> str:
        return hashlib.sha256(
            json.dumps({"query": query, "num_results": num_results, "provider": provider}, ensure_ascii=False, sort_keys=True).encode("utf-8")
        ).hexdigest()

    def _load_cached_results(self, *, query: str, num_results: int, provider: str) -> list[WebSearchResult] | None:
        if self._data_store is None:
            return None
        bucket = self._data_store.load_json("web_search_cache", {})
        if not isinstance(bucket, dict):
            return None
        entry = bucket.get(self._cache_key(query=query, num_results=num_results, provider=provider))
        if not isinstance(entry, dict):
            return None
        cached_at = float(entry.get("cached_at") or 0)
        if not cached_at or (time.time() - cached_at) > self.CACHE_TTL_SECONDS:
            return None
        results = []
        for item in entry.get("results") or []:
            if not isinstance(item, dict):
                continue
            results.append(WebSearchResult(item.get("title", ""), item.get("url", ""), item.get("snippet", "")))
        return results

    def _save_cached_results(self, *, query: str, num_results: int, provider: str, results: list[WebSearchResult]) -> None:
        if self._data_store is None:
            return
        cache_key = self._cache_key(query=query, num_results=num_results, provider=provider)

        def _updater(bucket: dict[str, Any]) -> dict[str, Any]:
            normalized = dict(bucket) if isinstance(bucket, dict) else {}
            normalized[cache_key] = {"cached_at": time.time(), "results": [item.to_dict() for item in results]}
            if len(normalized) > 128:
                keys = sorted(normalized, key=lambda item: float((normalized.get(item) or {}).get("cached_at") or 0), reverse=True)
                normalized = {key: normalized[key] for key in keys[:128]}
            return normalized

        self._data_store.update_json("web_search_cache", {}, _updater)
