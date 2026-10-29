# -*- coding: utf-8 -*-
"""Regression tests for sync.summary_view.is_stale (P3-10).

Before the fix, ``is_stale`` only checked ``last_sync_time is None`` despite
its docstring claiming a 24h threshold. Now it actually compares the parsed
timestamp against ``SYNC_STALE_THRESHOLD`` (default 24h).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sync.summary_view import SYNC_STALE_THRESHOLD, SyncSummaryView


def _iso(dt: datetime) -> str:
    return dt.isoformat()


def test_is_stale_when_no_sync_time() -> None:
    assert SyncSummaryView(last_sync_time=None).is_stale is True
    assert SyncSummaryView(last_sync_time="").is_stale is True


def test_is_stale_when_older_than_threshold() -> None:
    far_past = datetime.now(timezone.utc) - SYNC_STALE_THRESHOLD - timedelta(minutes=1)
    assert SyncSummaryView(last_sync_time=_iso(far_past)).is_stale is True


def test_not_stale_when_recent() -> None:
    recent = datetime.now(timezone.utc) - timedelta(minutes=5)
    assert SyncSummaryView(last_sync_time=_iso(recent)).is_stale is False


def test_is_stale_with_invalid_string_is_conservative() -> None:
    assert SyncSummaryView(last_sync_time="not-an-iso").is_stale is True


def test_handles_z_suffix_iso() -> None:
    """带 Z 后缀的 UTC ISO 时间应被正确解析。"""
    recent = datetime.now(timezone.utc) - timedelta(minutes=10)
    # 模拟某些客户端返回的 Z 风格
    z_form = recent.strftime("%Y-%m-%dT%H:%M:%SZ")
    assert SyncSummaryView(last_sync_time=z_form).is_stale is False


def test_naive_datetime_assumed_utc() -> None:
    """无时区的 ISO 字符串按 UTC 处理而不是抛错。"""
    now = datetime.now(timezone.utc) - timedelta(hours=23, minutes=30)
    naive = now.replace(tzinfo=None).isoformat()
    assert SyncSummaryView(last_sync_time=naive).is_stale is False
