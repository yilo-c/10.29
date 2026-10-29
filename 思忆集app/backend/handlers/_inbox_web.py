"""web.* JSON-RPC handlers：联网检索（search / fetch）。

从原 _inbox_data.py 拆分而来。
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

logger = logging.getLogger("siyi.backend.handlers.inbox_web")


def register_inbox_web(registry, context) -> None:
    """注册 web.* 命名空间下的 RPC 方法。"""

    # ── web.search ──
    async def web_search(query: str = "", **kwargs: Any) -> dict[str, Any]:
        if not query.strip():
            return {"error": "query is required"}
        svc = context.web_search_service
        if not svc:
            return {"error": "web search unavailable"}
        try:
            result = await asyncio.wait_for(
                asyncio.to_thread(svc.search, query),
                timeout=25,
            )
            return {"results": result}
        except asyncio.TimeoutError:
            return {"error": "web search timed out after 25s"}

    registry.register("web.search", web_search)

    # ── web.fetch ──
    async def web_fetch(url: str = "", **kwargs: Any) -> dict[str, Any]:
        if not url.strip():
            return {"error": "url is required"}
        svc = context.web_fetch_service
        if not svc:
            return {"error": "web fetch unavailable"}
        try:
            raw = await asyncio.wait_for(svc.fetch(url), timeout=30)
            # Serialize properly: handle object, string, or bytes
            if isinstance(raw, str):
                result = raw
            elif hasattr(raw, 'content'):
                result = str(raw.content)
            elif hasattr(raw, 'text'):
                result = str(raw.text)
            else:
                result = str(raw)
            # Cap at 2MB
            if len(result) > 2 * 1024 * 1024:
                result = result[:2 * 1024 * 1024] + "\n\n[截断：内容超过2MB]"
            return {"content": result}
        except asyncio.TimeoutError:
            return {"error": "web fetch timed out after 30s"}

    registry.register("web.fetch", web_fetch)
