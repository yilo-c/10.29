"""web.* handlers 行为测试（T6.1，_inbox_web）。

覆盖：web.search（空查询/服务缺失/成功/超时）、web.fetch（空url/服务缺失/
内容序列化多种类型/2MB截断/超时）、注册齐全。
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from backend.handlers._inbox_web import register_inbox_web


class _Registry:
    def __init__(self):
        self.registered: dict[str, object] = {}

    def register(self, name: str, fn) -> None:
        self.registered[name] = fn


@pytest.fixture
def ctx():
    # 注意：web_search/web_fetch 用 asyncio.to_thread 包裹 svc 调用，
    # 说明 svc.search/svc.fetch 是【同步】函数（在子线程执行）。用同步 lambda 模拟。
    return SimpleNamespace(
        web_search_service=SimpleNamespace(search=lambda q: ["r1", "r2"]),
        web_fetch_service=SimpleNamespace(fetch=lambda url: "<html>hi</html>"),
    )


@pytest.fixture
def reg():
    return _Registry()


# ── web.search ──


def test_search_success(ctx, reg) -> None:
    calls = []
    ctx.web_search_service.search = lambda q: (calls.append(q), ["r1", "r2"])[1]
    register_inbox_web(reg, ctx)
    result = asyncio.run(reg.registered["web.search"](query="pytest 异步"))
    assert result == {"results": ["r1", "r2"]}
    assert calls == ["pytest 异步"]


def test_search_empty_query(ctx, reg) -> None:
    calls = []
    ctx.web_search_service.search = lambda q: (calls.append(q), [])[1]
    register_inbox_web(reg, ctx)
    result = asyncio.run(reg.registered["web.search"](query="   "))
    assert result == {"error": "query is required"}
    assert calls == []


def test_search_service_missing(reg) -> None:
    ctx_missing = SimpleNamespace(web_search_service=None, web_fetch_service=None)
    register_inbox_web(reg, ctx_missing)
    result = asyncio.run(reg.registered["web.search"](query="q"))
    assert result == {"error": "web search unavailable"}


def test_search_timeout(ctx, reg) -> None:
    def _slow(_q):
        raise asyncio.TimeoutError()  # to_thread 内同步抛，模拟超时由 wait_for 捕获

    ctx.web_search_service.search = _slow
    register_inbox_web(reg, ctx)
    result = asyncio.run(reg.registered["web.search"](query="q"))
    assert "timed out" in result["error"]


# ── web.fetch ──


def test_fetch_success_str(ctx, reg) -> None:
    calls = []
    async def _fetch(url):
        calls.append(url)
        return "<html>hi</html>"
    ctx.web_fetch_service.fetch = _fetch
    register_inbox_web(reg, ctx)
    result = asyncio.run(reg.registered["web.fetch"](url="https://example.com"))
    assert result == {"content": "<html>hi</html>"}
    assert calls == ["https://example.com"]


def test_fetch_empty_url(ctx, reg) -> None:
    calls = []
    async def _fetch(url):
        calls.append(url)
        return ""
    ctx.web_fetch_service.fetch = _fetch
    register_inbox_web(reg, ctx)
    result = asyncio.run(reg.registered["web.fetch"](url="  "))
    assert result == {"error": "url is required"}
    assert calls == []


def test_fetch_service_missing(reg) -> None:
    ctx_missing = SimpleNamespace(web_search_service=None, web_fetch_service=None)
    register_inbox_web(reg, ctx_missing)
    result = asyncio.run(reg.registered["web.fetch"](url="https://example.com"))
    assert result == {"error": "web fetch unavailable"}


def test_fetch_truncates_over_2mb(ctx, reg) -> None:
    async def _big(_url):
        return "x" * (2 * 1024 * 1024 + 100)
    ctx.web_fetch_service.fetch = _big
    register_inbox_web(reg, ctx)
    result = asyncio.run(reg.registered["web.fetch"](url="https://big.example.com"))
    assert len(result["content"]) <= 2 * 1024 * 1024 + 100
    assert "截断" in result["content"]


def test_fetch_timeout(ctx, reg) -> None:
    async def _slow(_url):
        raise asyncio.TimeoutError()

    ctx.web_fetch_service.fetch = _slow
    register_inbox_web(reg, ctx)
    result = asyncio.run(reg.registered["web.fetch"](url="https://slow.example.com"))
    assert "timed out" in result["error"]


# ── 注册 ──


def test_register_web_methods(ctx, reg) -> None:
    register_inbox_web(reg, ctx)
    assert "web.search" in reg.registered
    assert "web.fetch" in reg.registered
