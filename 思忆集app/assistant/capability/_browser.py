"""Browser capability domain.

Extracted from capability_router.py — BrowserCapabilityDomain.
"""
from __future__ import annotations

import logging
import webbrowser
from typing import Any
from urllib.parse import quote_plus

logger = logging.getLogger("siyi.assistant.capability.browser")

_HAS_BROWSER_ADAPTER = False
BrowserAdapter = None  # type: ignore

try:
    from adapters.browser import BrowserAdapter
    _HAS_BROWSER_ADAPTER = True
except ImportError:
    logger.debug("Non-critical error (see details)", exc_info=True)



class BrowserCapabilityDomain:
    """Browser open, context get, and summarize capabilities."""

    def __init__(self, router) -> None:
        self.router = router

    def browser_open(self, payload: dict[str, Any]) -> dict[str, Any]:
        url = str(payload.get("url") or "").strip()
        search = str(payload.get("search") or "").strip()
        if not url and search:
            url = f"https://www.bing.com/search?q={quote_plus(search)}"
        if not url:
            return {"opened": False, "reason": "no_url_or_search"}
        webbrowser.open(url)
        return {"opened": True, "url": url}

    def _get_browser_adapter(self) -> Any | None:
        if not _HAS_BROWSER_ADAPTER:
            return None
        try:
            return BrowserAdapter(settings_repository=self.router.settings_repository)
        except Exception:
            logger.debug("browser adapter init failed", exc_info=True)
            return None

    def browser_context_get(self, payload: dict[str, Any]) -> dict[str, Any]:
        adapter = self._get_browser_adapter()
        if adapter is None:
            return {"success": False, "reason": "browser_adapter_unavailable"}
        try:
            return adapter.get_page_context()
        except Exception as exc:
            logger.warning("browser.context.get failed: %s", exc)
            return {"success": False, "reason": str(exc)}

    def browser_summarize(self, payload: dict[str, Any]) -> dict[str, Any]:
        adapter = self._get_browser_adapter()
        if adapter is None:
            return {"success": False, "reason": "browser_adapter_unavailable"}
        try:
            return adapter.summarize_page()
        except Exception as exc:
            logger.warning("browser.summarize failed: %s", exc)
            return {"success": False, "reason": str(exc)}
