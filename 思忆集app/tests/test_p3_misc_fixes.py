# -*- coding: utf-8 -*-
"""Regression tests for P3-3 / P3-4 / P3-5 fixes.

- P3-3: FallbackOpenAIClient 构造时不再重复赋值 cache_ttl/cache_max_entries
- P3-4: AiResponseCache.set_sync 在事件循环内调度的后台任务必须被强引用，
        避免被 GC 回收导致写入丢失
- P3-5: AIUsageStats.get_monthly_stats(months=N) 的 cutoff 必须按精确的
        年-月-1 号回退，而不是 days=months*31 的近似值
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest


# ── P3-3 ──────────────────────────────────────────────────────────────


def test_fallback_client_cache_attrs_no_duplicate_assignment(tmp_path) -> None:
    """构造时 cache_ttl_seconds / cache_max_entries 仅赋值一次，且与参数一致。"""
    from ai.client.fallback import FallbackOpenAIClient

    cache_path = tmp_path / "cache.json"
    client = FallbackOpenAIClient(
        primary=None,
        fallback=None,
        local=None,
        local_coder=None,
        cache_path=str(cache_path),
        cache_ttl_seconds=1234,
        cache_max_entries=42,
    )
    assert client.cache_ttl_seconds == 1234
    assert client.cache_max_entries == 42


# ── P3-4 ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_aicache_set_sync_keeps_strong_reference(tmp_path) -> None:
    """在事件循环内调用 set_sync 时，后台任务应被 _pending_set_tasks 引用。"""
    from ai.client._cache import AiResponseCache

    cache = AiResponseCache(cache_path=tmp_path / "c.json")
    cache.set_sync("k1", "v1")

    # 立刻应至少有一个 pending 任务持有强引用
    assert len(cache._pending_set_tasks) >= 1
    pending = list(cache._pending_set_tasks)

    # 让事件循环把后台任务跑完
    await asyncio.gather(*pending)

    # 完成后会自动从集合中移除（done_callback）
    assert all(t.done() for t in pending)
    assert len(cache._pending_set_tasks) == 0

    # 数据应已写入
    data = json.loads(Path(cache.cache_path).read_text(encoding="utf-8"))
    assert "k1" in data


def test_aicache_set_sync_no_loop_uses_threading_path(tmp_path) -> None:
    """没有事件循环时走 threading.Lock 路径，不应抛出。"""
    from ai.client._cache import AiResponseCache

    cache = AiResponseCache(cache_path=tmp_path / "c.json")
    cache.set_sync("ksync", "vsync")
    data = json.loads(Path(cache.cache_path).read_text(encoding="utf-8"))
    assert "ksync" in data


# ── P3-5 ──────────────────────────────────────────────────────────────


def test_monthly_stats_cutoff_is_calendar_aligned(tmp_path, monkeypatch) -> None:
    """months=2 时 cutoff 应是 2 个完整自然月之前的 1 号 0 点，而非 62 天前。"""
    from ai import usage_stats as us

    # 构造一个完全可控的 AIUsageStats：用类内部 _load_records 的桩
    class _Fake(us.AIUsageStats):
        def __init__(self) -> None:  # 不调用父类构造避免依赖
            self._records: list[dict] = []

        def _load_records(self):
            return list(self._records)

    fake = _Fake()
    # 当前时间固定到 2026-03-15 12:00 UTC（now 由 datetime.now 决定）
    fixed_now = datetime(2026, 3, 15, 12, 0, tzinfo=timezone.utc)

    class _DT(datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed_now if tz is None else fixed_now.astimezone(tz)

    monkeypatch.setattr(us, "datetime", _DT)

    # 三条记录：2025-12-31（应被裁掉），2026-01-01（在 months=2 窗口），2026-03-10（窗口内）
    fake._records = [
        {"timestamp": "2025-12-31T23:59:59+00:00", "tokens": 100, "cost": 1.0, "model": "m"},
        {"timestamp": "2026-01-01T00:00:00+00:00", "tokens": 200, "cost": 2.0, "model": "m"},
        {"timestamp": "2026-03-10T00:00:00+00:00", "tokens": 300, "cost": 3.0, "model": "m"},
    ]

    # months=2 → cutoff 应为 2026-01-01；2025-12-31 在 cutoff 之前被裁掉
    result = fake.get_monthly_stats(months=2)
    assert "2025-12" not in result
    assert "2026-01" in result
    assert "2026-03" in result
    assert result["2026-01"]["tokens"] == 200
    assert result["2026-03"]["tokens"] == 300


def test_monthly_stats_handles_year_boundary(tmp_path, monkeypatch) -> None:
    """跨年场景：now=2026-01-05, months=13 → cutoff 应为 2024-12-01。"""
    from ai import usage_stats as us

    class _Fake(us.AIUsageStats):
        def __init__(self) -> None:
            self._records: list[dict] = []

        def _load_records(self):
            return list(self._records)

    fake = _Fake()
    fixed_now = datetime(2026, 1, 5, tzinfo=timezone.utc)

    class _DT(datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed_now if tz is None else fixed_now.astimezone(tz)

    monkeypatch.setattr(us, "datetime", _DT)

    fake._records = [
        {"timestamp": "2024-11-30T00:00:00+00:00", "tokens": 1, "cost": 0.0, "model": "m"},
        {"timestamp": "2024-12-01T00:00:00+00:00", "tokens": 2, "cost": 0.0, "model": "m"},
        {"timestamp": "2025-06-15T00:00:00+00:00", "tokens": 3, "cost": 0.0, "model": "m"},
    ]
    result = fake.get_monthly_stats(months=13)
    assert "2024-11" not in result
    assert "2024-12" in result
    assert "2025-06" in result
