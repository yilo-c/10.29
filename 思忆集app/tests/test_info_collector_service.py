"""Tests for InfoCollectorService（基于 FETCHER_METHOD_MAP + InfoSourceRegistry 架构）。

覆盖 collect 核心路径（拆分前安全网，T1.3 / T2.4 教训）：
- registry 解析启用源 → 按 FETCHER_METHOD_MAP 动态调用 fetcher
- asyncio.gather 聚合 → 去重 → 写缓存 → 返回 {brief, items, errors}
- 成功抓取写缓存 / 全空不写缓存
- generate_brief 透传（AI mock 注入）
- _call_ai 在 finish_reason=length 且空内容时放大 max_tokens 重试

网络被隔离：只启用单一 hackernews 源，并将其 fetcher `_hn` patch 为 AsyncMock，
不触发任何真实 HTTP。
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from services.info_collector_service import (
    InfoCollectorService,
    DEFAULT_TOPICS,
    _cache,
)
from services.info_source_registry import InfoSourceRegistry, FETCHER_METHOD_MAP


class _FakeAIClient:
    """最小 AI client：提供 chat_completion，返回固定 markdown 简报。"""

    def chat_completion(self, messages, temperature=0.3, max_tokens=1500):
        return {"choices": [{"message": {"content": "## 测试简报\n\n这是 AI 生成的测试简报。"}}]}


class _LengthEmptyThenContentClient:
    """先返回 finish_reason=length 空内容，重试后返回正常内容（记录每次 max_tokens）。"""

    def __init__(self):
        self.calls: list[int] = []

    def chat_completion(self, messages, temperature=0.3, max_tokens=1500):
        self.calls.append(max_tokens)
        if len(self.calls) == 1:
            return {"choices": [{"message": {"content": ""}, "finish_reason": "length"}]}
        return {"choices": [{"message": {"content": "## 重试后的简报"}}]}


def _make_single_source_registry(keep: str = "hackernews"):
    """构造真实 registry，仅保留一个源，其余全部禁用 —— 隔离网络又走真实解析路径。"""
    reg = InfoSourceRegistry()
    for name in list(reg._sources):
        if name != keep:
            reg.disable_source(name)
    assert reg.get_enabled_source_names() == [keep], reg.get_enabled_source_names()
    return reg


def _fake_items(n: int = 2):
    # title 刻意差异化，避免触发 title_similarity 去重（阈值 0.72）。
    titles = [
        "Python 异步编程实战指南",
        "Rust 内存安全模型解析",
        "Go 语言并发原语对比",
        "TypeScript 类型体操进阶",
    ]
    return [
        {
            "id": f"fake-{i}",
            "title": titles[i % len(titles)],
            "url": f"https://example.com/{i}",
            "source": "hackernews",
            "score": float(10 - i),
        }
        for i in range(n)
    ]


@pytest.fixture
def clean_cache():
    saved = dict(_cache)
    try:
        _cache.clear()
        _cache.update({"items": [], "brief": "", "cached_at": 0.0})
        yield
    finally:
        _cache.clear()
        _cache.update(saved)


@pytest.fixture(scope="module", autouse=True)
def _isolate_info_cache(tmp_path_factory):
    """隔离 info_cache.json 的落盘目录（2026-09-17 生产数据泄漏守卫）。

    ``InfoCollectorService.collect()`` 抓取成功后会调用 ``_save_persisted_cache()``；
    未设 ``SIYI_DATA_DIR`` 时该函数回落到**生产**
    ``思忆集app/data/info_cache.json``（``data/**`` 在 .gitignore 内，
    ``git status`` 看不见）。本模块多个用例（test_collect_updates_cache_on_success）
    会真实走到写入分支，因此整体把 ``SIYI_DATA_DIR`` 指向本模块专属临时目录
    —— ``_resolve_persist_path()`` 是调用期解析 env，故能覆盖全部用例。
    """
    data_dir = tmp_path_factory.mktemp("info-collector-data")
    saved = os.environ.get("SIYI_DATA_DIR")
    os.environ["SIYI_DATA_DIR"] = str(data_dir)
    try:
        yield data_dir
    finally:
        if saved is None:
            os.environ.pop("SIYI_DATA_DIR", None)
        else:
            os.environ["SIYI_DATA_DIR"] = saved


@pytest.mark.asyncio
async def test_call_ai_retries_on_length_empty_content():
    """finish_reason=length 且内容为空时，应放大 max_tokens 重试而非直接返回空。"""
    fake = _LengthEmptyThenContentClient()
    svc = InfoCollectorService(ai_client=fake)
    resp = await svc._call_ai(
        [{"role": "user", "content": "hi"}],
        temperature=0.3,
        max_tokens=1000,
    )
    assert resp["choices"][0]["message"]["content"] == "## 重试后的简报"
    assert len(fake.calls) == 2
    assert fake.calls[0] == 1000
    assert fake.calls[1] == 2000  # max_tokens 翻倍重试


def test_default_topics_defined():
    assert isinstance(DEFAULT_TOPICS, list)
    assert len(DEFAULT_TOPICS) > 0
    assert all(isinstance(t, str) for t in DEFAULT_TOPICS)


def test_fetcher_method_map_resolves_all_builtin_sources():
    """FETCHER_METHOD_MAP 的所有键都能在 InfoCollectorService 上找到对应方法 —— 防止重构后映射漂移。"""
    for name, method in FETCHER_METHOD_MAP.items():
        assert hasattr(InfoCollectorService, method), f"{name} -> {method} 缺失于 InfoCollectorService"


@pytest.mark.asyncio
async def test_collect_returns_dict_with_default_topics(clean_cache):
    reg = _make_single_source_registry()
    svc = InfoCollectorService(source_registry=reg)
    with patch.object(svc, "_hn", new=AsyncMock(return_value=_fake_items(2))):
        result = await svc.collect(topics=None, generate_brief=False)
    assert set(result.keys()) >= {"brief", "items", "errors"}
    assert isinstance(result["items"], list)
    assert isinstance(result["errors"], list)
    assert len(result["items"]) == 2


@pytest.mark.asyncio
async def test_collect_custom_topics(clean_cache):
    reg = _make_single_source_registry()
    svc = InfoCollectorService(source_registry=reg)
    with patch.object(svc, "_hn", new=AsyncMock(return_value=_fake_items(1))):
        result = await svc.collect(topics=["Python"], generate_brief=False)
    assert isinstance(result["items"], list)
    assert len(result["items"]) == 1


@pytest.mark.asyncio
async def test_collect_empty_topics_falls_back_to_defaults(clean_cache):
    reg = _make_single_source_registry()
    svc = InfoCollectorService(source_registry=reg)
    with patch.object(svc, "_hn", new=AsyncMock(return_value=_fake_items(1))):
        result = await svc.collect(topics=[], generate_brief=False)
    assert isinstance(result["items"], list)
    assert len(result["items"]) == 1


@pytest.mark.asyncio
async def test_collect_no_ai_returns_empty_brief(clean_cache):
    reg = _make_single_source_registry()
    svc = InfoCollectorService(source_registry=reg)
    with patch.object(svc, "_hn", new=AsyncMock(return_value=_fake_items(1))):
        result = await svc.collect(topics=["test"], generate_brief=False)
    assert result["brief"] == ""


@pytest.mark.asyncio
async def test_collect_generates_brief_with_ai(clean_cache):
    reg = _make_single_source_registry()
    svc = InfoCollectorService(ai_client=_FakeAIClient(), source_registry=reg)
    with patch.object(svc, "_hn", new=AsyncMock(return_value=_fake_items(1))):
        result = await svc.collect(topics=["AI"], generate_brief=True)
    assert isinstance(result["brief"], str)


@pytest.mark.asyncio
async def test_collect_updates_cache_on_success(clean_cache):
    reg = _make_single_source_registry()
    svc = InfoCollectorService(source_registry=reg)
    with patch.object(svc, "_hn", new=AsyncMock(return_value=_fake_items(3))):
        await svc.collect(topics=["test"], generate_brief=False)

    cached = InfoCollectorService.get_cached()
    assert len(cached["items"]) >= 1
    assert cached["cached_at"] > 0.0


@pytest.mark.asyncio
async def test_collect_does_not_cache_on_empty(clean_cache):
    reg = _make_single_source_registry()
    svc = InfoCollectorService(source_registry=reg)
    with patch.object(svc, "_hn", new=AsyncMock(return_value=[])):
        result = await svc.collect(topics=["test"], generate_brief=False)

    assert result["items"] == []
    cached = InfoCollectorService.get_cached()
    assert cached["items"] == []
    assert cached["cached_at"] == 0.0


def test_get_cached_returns_empty_by_default():
    """get_cached 在无缓存时应返回空 items 和 cached_at=0。"""
    saved = dict(_cache)
    try:
        _cache.clear()
        _cache.update({"items": [], "brief": "", "cached_at": 0.0})
        result = InfoCollectorService.get_cached()
        assert result["items"] == []
        assert result["cached_at"] == 0.0
    finally:
        _cache.clear()
        _cache.update(saved)


def test_info_cached_items_rpc_registered():
    """info.cached_items 方法应被注册到 registry。"""
    from backend.method_registry import MethodRegistry
    from backend.handlers._inbox_data import register_inbox_data
    from unittest.mock import MagicMock

    registry = MethodRegistry()
    ctx = MagicMock()
    register_inbox_data(registry, ctx)
    assert registry.has_method("info.cached_items")


def test_collect_is_async():
    """collect 必须是 async（架构约束：内部无阻塞网络调用）。"""
    import inspect

    assert inspect.iscoroutinefunction(InfoCollectorService.collect)


# ── T6.2：真实 fetcher 解析路径（只隔离 HTTP 层，不 mock fetcher 方法）──


class _FakeResponse:
    """最小 aiohttp 响应：提供 async json()。"""

    def __init__(self, payload):
        self._payload = payload

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def json(self):
        return self._payload


class _FakeSession:
    """按 URL 返回固定响应的假 aiohttp.ClientSession（不触发真实网络）。"""

    def __init__(self, url_payloads):
        self._url_payloads = url_payloads
        self.gets: list[str] = []

    def get(self, url, timeout=None):
        self.gets.append(url)
        for prefix, payload in self._url_payloads:
            if url.startswith(prefix):
                return _FakeResponse(payload)
        raise AssertionError(f"unexpected URL: {url}")


@pytest.mark.asyncio
async def test_hn_fetcher_real_parsing_path():
    """T6.2：走真实 _hn 解析路径（只 mock HTTP 层），验证 JSON→条目格式化。

    此前 7 处测试均 patch 掉 _hn 方法本身，collect 真实抓取解析路径从未被测。
    本测试 mock aiohttp 会话，让 _hn 的 topstories→item 抓取与 _fmt_hn_items 真实执行。
    """
    from services.info_collector_service import InfoCollectorService
    from unittest.mock import patch

    svc = InfoCollectorService.__new__(InfoCollectorService)
    svc._source_health = {}

    fake_session = _FakeSession(
        [
            ("https://hacker-news.firebaseio.com/v0/topstories.json", [1, 2]),
            (
                "https://hacker-news.firebaseio.com/v0/item/1.json",
                {
                    "id": 1,
                    "title": "真实解析标题",
                    "url": "https://example.com/a",
                    "score": 42,
                    "time": 1700000000,
                    "text": "正文",
                },
            ),
            (
                "https://hacker-news.firebaseio.com/v0/item/2.json",
                {
                    "id": 2,
                    "title": "第二篇",
                    "url": "https://example.com/b",
                    "score": 7,
                    "time": 1700000001,
                },
            ),
        ]
    )

    with patch("services.info_fetchers.mixin.aiohttp.ClientSession", return_value=fake_session):
        items = await svc._hn(fake_session)

    # 真实 _fmt_hn_items 输出：source=hackernews、id 前缀 hn-、时间转毫秒
    assert len(items) == 2
    assert items[0]["source"] == "hackernews"
    assert items[0]["id"] == "hn-1"
    assert items[0]["title"] == "真实解析标题"
    assert items[0]["score"] == 42
    assert items[0]["publishedAt"] == 1700000000 * 1000
    assert items[1]["id"] == "hn-2"
    # topstories + 2 个 item = 3 次 HTTP
    assert len(fake_session.gets) == 3
