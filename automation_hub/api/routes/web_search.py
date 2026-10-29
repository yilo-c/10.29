"""
Web Search API 路由

提供互联网搜索能力，支持多搜索引擎后端。
"""

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from typing import Optional, List
import logging
import time
import hashlib
import json

from automation_hub.api.auth.deps import require_scope

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/web-search", tags=["web-search"])


# ==================== 搜索引擎适配器 ====================

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)


def _search_duckduckgo(query: str, max_results: int) -> list[dict]:
    """通过 DuckDuckGo 搜索（免 API Key）。"""
    import requests
    import re
    import html
    from urllib.parse import unquote

    def _strip(text: str) -> str:
        text = re.sub(r"<[^>]+>", "", text)
        return html.unescape(text).strip()

    def _extract_url(raw: str) -> str:
        m = re.search(r"uddg=([^&]+)", raw)
        return unquote(m.group(1)) if m else raw

    resp = requests.get(
        "https://html.duckduckgo.com/html/",
        params={"q": query},
        headers={"User-Agent": _USER_AGENT},
        timeout=15,
    )
    resp.raise_for_status()

    results: list[dict] = []
    for m in re.finditer(
        r'<a[^>]+class="result__a"[^>]+href="([^"]+)"[^>]*>(.*?)</a>.*?'
        r'<a[^>]+class="result__snippet"[^>]*>(.*?)</a>',
        resp.text,
        flags=re.DOTALL,
    ):
        results.append({
            "title": _strip(m.group(2)),
            "url": _extract_url(m.group(1)),
            "snippet": _strip(m.group(3))[:500],
        })
        if len(results) >= max_results:
            break
    return results


def _search_tavily(query: str, max_results: int, api_key: str) -> list[dict]:
    """通过 Tavily API 搜索。"""
    import requests

    resp = requests.post(
        "https://api.tavily.com/search",
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json={"query": query, "max_results": max_results, "search_depth": "basic"},
        timeout=15,
    )
    resp.raise_for_status()
    results: list[dict] = []
    for item in resp.json().get("results", []):
        results.append({
            "title": item.get("title", ""),
            "url": item.get("url", ""),
            "snippet": item.get("content", "")[:500],
        })
    return results[:max_results]


def _search_searxng(query: str, max_results: int, base_url: str) -> list[dict]:
    """通过 SearXNG 自建实例搜索。"""
    import requests
    from urllib.parse import urlparse

    parsed = urlparse(base_url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ValueError(f"无效的 SearXNG URL: {base_url}")

    resp = requests.get(
        f"{base_url.rstrip('/')}/search",
        params={"q": query, "format": "json", "language": "zh"},
        headers={"User-Agent": _USER_AGENT},
        timeout=15,
    )
    resp.raise_for_status()
    results: list[dict] = []
    for item in resp.json().get("results", []):
        results.append({
            "title": item.get("title", ""),
            "url": item.get("url", ""),
            "snippet": item.get("content", "")[:500],
        })
    return results[:max_results]


# ==================== 内部状态 ====================

# TODO: 替换为数据库持久化
_SEARCH_CACHE: dict[str, dict] = {}
_CACHE_TTL = 3600  # 1 小时
_ENGINES = {
    "duckduckgo": {"name": "DuckDuckGo", "requires_key": False, "description": "免费搜索引擎，无需 API Key"},
    "tavily": {"name": "Tavily", "requires_key": True, "description": "专为 AI Agent 设计的搜索 API"},
    "searxng": {"name": "SearXNG", "requires_key": False, "description": "自建元搜索引擎实例"},
}
_LAST_HEALTH_CHECK = 0.0
_HEALTH_STATUS = {"status": "unknown", "available_engines": [], "errors": {}}


def _cache_key(query: str, max_results: int, sources: Optional[list[str]]) -> str:
    raw = json.dumps({"q": query, "n": max_results, "s": sources}, sort_keys=True)
    return hashlib.sha256(raw.encode()).hexdigest()


def _get_cached(key: str) -> Optional[list[dict]]:
    entry = _SEARCH_CACHE.get(key)
    if not entry:
        return None
    if time.time() - entry.get("ts", 0) > _CACHE_TTL:
        del _SEARCH_CACHE[key]
        return None
    return entry.get("results")


def _set_cache(key: str, results: list[dict]) -> None:
    _SEARCH_CACHE[key] = {"ts": time.time(), "results": results}
    # 控制缓存大小
    if len(_SEARCH_CACHE) > 256:
        keys = sorted(_SEARCH_CACHE, key=lambda k: _SEARCH_CACHE[k]["ts"], reverse=True)
        for k in keys[192:]:
            del _SEARCH_CACHE[k]


def _dedupe(results: list[dict]) -> list[dict]:
    seen: set[tuple[str, str]] = set()
    deduped: list[dict] = []
    for r in results:
        key = (r.get("url", "").strip().lower(), r.get("title", "").strip().lower())
        if key in seen:
            continue
        seen.add(key)
        deduped.append(r)
    return deduped


def _do_search(query: str, max_results: int, sources: Optional[list[str]] = None) -> dict:
    """执行搜索，带回退逻辑。"""
    import os

    engines_to_try = [s.strip().lower() for s in sources] if sources else ["duckduckgo"]
    # 如果指定了 tavily 但没 key，自动跳过
    api_key = os.environ.get("TAVILY_API_KEY", "")

    all_results: list[dict] = []
    errors: dict[str, str] = {}
    used_engines: list[str] = []

    for engine in engines_to_try:
        if engine not in _ENGINES:
            errors[engine] = "unsupported engine"
            continue
        if engine == "tavily" and not api_key:
            errors[engine] = "TAVILY_API_KEY not configured"
            continue
        try:
            if engine == "duckduckgo":
                results = _search_duckduckgo(query, max_results)
            elif engine == "tavily":
                results = _search_tavily(query, max_results, api_key)
            elif engine == "searxng":
                base_url = os.environ.get("SIYI_SEARXNG_BASE_URL", "")
                if not base_url:
                    errors[engine] = "SIYI_SEARXNG_BASE_URL not configured"
                    continue
                results = _search_searxng(query, max_results, base_url)
            else:
                continue
            all_results.extend(results)
            used_engines.append(engine)
        except Exception as exc:
            errors[engine] = str(exc)
            logger.warning("Search engine %s failed: %s", engine, exc)

    # 所有指定引擎都失败时，回退 DuckDuckGo
    if not all_results and "duckduckgo" not in engines_to_try:
        try:
            fallback = _search_duckduckgo(query, max_results)
            all_results.extend(fallback)
            used_engines.append("duckduckgo (fallback)")
        except Exception as exc:
            errors["duckduckgo (fallback)"] = str(exc)

    deduped = _dedupe(all_results)[:max_results]
    return {"results": deduped, "used_engines": used_engines, "errors": errors if errors else None}


def _check_engines_health() -> dict:
    """检查各搜索引擎可用性。"""
    global _LAST_HEALTH_CHECK, _HEALTH_STATUS

    import os

    if time.time() - _LAST_HEALTH_CHECK < 60:
        return _HEALTH_STATUS

    api_key = os.environ.get("TAVILY_API_KEY", "")
    searxng_url = os.environ.get("SIYI_SEARXNG_BASE_URL", "")

    available = []
    errors = {}

    # DuckDuckGo 始终可用（无外部依赖）
    available.append("duckduckgo")

    if api_key:
        available.append("tavily")
    else:
        errors["tavily"] = "TAVILY_API_KEY not configured"

    if searxng_url:
        available.append("searxng")
    else:
        errors["searxng"] = "SIYI_SEARXNG_BASE_URL not configured"

    _HEALTH_STATUS = {
        "status": "degraded" if errors else "ok",
        "available_engines": available,
        "errors": errors if errors else None,
    }
    _LAST_HEALTH_CHECK = time.time()
    return _HEALTH_STATUS


# ==================== 请求/响应模型 ====================


class SearchRequest(BaseModel):
    """搜索请求"""
    query: str = Field(description="搜索关键词", min_length=1, max_length=500)
    language: str = Field(default="zh", description="搜索结果语言偏好")
    max_results: int = Field(default=5, ge=1, le=20, description="返回结果数量")
    sources: Optional[list[str]] = Field(default=None, description="搜索引擎列表（按优先级）")


class SearchResultItem(BaseModel):
    """单条搜索结果"""
    title: str = Field(description="标题")
    url: str = Field(description="链接")
    snippet: str = Field(description="摘要")


class SearchResponse(BaseModel):
    """搜索响应"""
    query: str = Field(description="原始查询")
    results: list[SearchResultItem] = Field(default_factory=list, description="搜索结果列表")
    total: int = Field(description="结果数量")
    used_engines: list[str] = Field(description="实际使用的搜索引擎")
    cached: bool = Field(default=False, description="是否来自缓存")
    errors: Optional[dict[str, str]] = Field(default=None, description="各引擎错误信息")


class EngineInfo(BaseModel):
    """搜索引擎信息"""
    name: str
    requires_key: bool
    description: str


class EnginesResponse(BaseModel):
    """可用搜索引擎列表响应"""
    engines: dict[str, EngineInfo]


class StatusResponse(BaseModel):
    """搜索服务状态响应"""
    status: str = Field(description="服务状态: ok|degraded|unavailable")
    available_engines: list[str] = Field(description="可用引擎列表")
    cache_size: int = Field(description="当前缓存条目数")
    errors: Optional[dict[str, str]] = Field(default=None, description="不可用引擎的错误信息")


# ==================== API 端点 ====================


@router.post("/search", response_model=SearchResponse)
async def web_search(
    request: SearchRequest,
    token=Depends(require_scope("web-search:use")),
):
    """
    执行 Web 搜索。

    支持多个搜索引擎后端，按 sources 指定的优先级依次尝试。
    所有引擎均失败时自动回退 DuckDuckGo。
    """
    try:
        query = request.query.strip()
        if not query:
            raise HTTPException(status_code=400, detail="query must not be empty")

        cache_key = _cache_key(query, request.max_results, request.sources)
        cached = _get_cached(cache_key)
        if cached is not None:
            return SearchResponse(
                query=query,
                results=[SearchResultItem(**r) for r in cached],
                total=len(cached),
                used_engines=["cache"],
                cached=True,
            )

        result = _do_search(query, request.max_results, request.sources)
        results_data = result["results"]
        _set_cache(cache_key, results_data)

        return SearchResponse(
            query=query,
            results=[SearchResultItem(**r) for r in results_data],
            total=len(results_data),
            used_engines=result["used_engines"],
            cached=False,
            errors=result.get("errors"),
        )

    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.exception("Web search failed")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"搜索请求失败: {str(e)}",
        )


@router.get("/engines", response_model=EnginesResponse)
async def list_engines(
    token=Depends(require_scope("web-search:use")),
):
    """
    列出所有可用搜索引擎及其信息。
    """
    try:
        engines = {
            key: EngineInfo(**info) for key, info in _ENGINES.items()
        }
        return EnginesResponse(engines=engines)

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Failed to list engines")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"获取引擎列表失败: {str(e)}",
        )


@router.get("/status", response_model=StatusResponse)
async def search_status(
    token=Depends(require_scope("web-search:use")),
):
    """
    获取 Web 搜索服务运行状态。
    """
    try:
        health = _check_engines_health()
        return StatusResponse(
            status=health["status"],
            available_engines=health["available_engines"],
            cache_size=len(_SEARCH_CACHE),
            errors=health.get("errors"),
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Failed to get search status")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"获取服务状态失败: {str(e)}",
        )
