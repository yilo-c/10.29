"""Tests for 思忆集app.ai.client._cache.AiResponseCache."""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

import pytest

# Ensure imports resolve when pytest is invoked from the repository root.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ai.client._cache import AiResponseCache


@pytest.fixture
def cache(tmp_path: Path) -> AiResponseCache:
    return AiResponseCache(cache_path=tmp_path / "ai_cache.json")


@pytest.fixture
def short_ttl_cache(tmp_path: Path) -> AiResponseCache:
    return AiResponseCache(
        cache_path=tmp_path / "ai_cache_short.json",
        ttl_seconds=0.05,
    )


def test_make_key_differs_for_different_inputs(cache: AiResponseCache) -> None:
    base_messages = [{"role": "user", "content": "hello"}]
    key_base = cache.make_key(base_messages)

    key_temp = cache.make_key(base_messages, temperature=0.5)
    key_max = cache.make_key(base_messages, max_tokens=100)
    key_tools = cache.make_key(base_messages, tools=[{"type": "function", "name": "f"}])
    key_tool_choice = cache.make_key(base_messages, tool_choice="auto")
    key_messages = cache.make_key([{"role": "user", "content": "world"}])

    assert key_temp != key_base
    assert key_max != key_base
    assert key_tools != key_base
    assert key_tool_choice != key_base
    assert key_messages != key_base


@pytest.mark.asyncio
async def test_async_set_get_roundtrip(cache: AiResponseCache) -> None:
    key = "key1"
    text = "cached response"
    await cache.set(key, text)

    entry = await cache.get(key)
    assert entry is not None
    assert entry["text"] == text
    assert "ts" in entry


@pytest.mark.asyncio
async def test_get_missing_key_returns_none(cache: AiResponseCache) -> None:
    assert await cache.get("missing") is None


def test_sync_set_get_roundtrip(tmp_path: Path) -> None:
    cache = AiResponseCache(cache_path=tmp_path / "sync_cache.json")
    key = "sync_key"
    text = "sync cached response"
    cache.set_sync(key, text)

    entry = cache.get_sync(key)
    assert entry is not None
    assert entry["text"] == text
    assert "ts" in entry


def test_get_sync_missing_key_returns_none(tmp_path: Path) -> None:
    cache = AiResponseCache(cache_path=tmp_path / "sync_cache.json")
    assert cache.get_sync("missing") is None


@pytest.mark.asyncio
async def test_ttl_expiration_removes_entry(short_ttl_cache: AiResponseCache) -> None:
    await short_ttl_cache.set("expire", "old data")
    await asyncio.sleep(0.1)

    assert await short_ttl_cache.get("expire") is None
    assert short_ttl_cache.size == 0


@pytest.mark.asyncio
async def test_prune_keeps_newest_entries(tmp_path: Path) -> None:
    cache = AiResponseCache(
        cache_path=tmp_path / "prune_cache.json",
        ttl_seconds=3600,
        max_entries=3,
    )

    for i in range(5):
        await cache.set(f"key{i}", f"value{i}")
        await asyncio.sleep(0.01)

    await cache.prune()

    assert cache.size == 3
    assert await cache.get("key0") is None
    assert await cache.get("key1") is None
    for i in range(2, 5):
        entry = await cache.get(f"key{i}")
        assert entry is not None
        assert entry["text"] == f"value{i}"


@pytest.mark.asyncio
async def test_get_many_returns_multiple_entries(cache: AiResponseCache) -> None:
    await cache.set("a", "alpha")
    await cache.set("b", "beta")
    await cache.set("c", "gamma")

    result = await cache.get_many(["a", "c", "missing"])

    assert set(result.keys()) == {"a", "c"}
    assert result["a"]["text"] == "alpha"
    assert result["c"]["text"] == "gamma"


@pytest.mark.asyncio
async def test_clear_empties_cache(cache: AiResponseCache) -> None:
    await cache.set("x", "data")
    assert cache.size == 1

    cache.clear()

    assert cache.size == 0
    assert await cache.get("x") is None


@pytest.mark.asyncio
async def test_cache_survives_file_reload(tmp_path: Path) -> None:
    path = tmp_path / "reload_cache.json"
    cache1 = AiResponseCache(cache_path=path)
    await cache1.set("persisted", "value")

    cache2 = AiResponseCache(cache_path=path)
    entry = await cache2.get("persisted")
    assert entry is not None
    assert entry["text"] == "value"
