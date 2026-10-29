# -*- coding: utf-8 -*-
"""Regression tests for backend.weekly_report_scheduler (P3-9).

Before the fix:
- The hourly loop only ran when ``now.hour == 1``, so a restart inside
  01:00–02:00 UTC could miss the whole week.
- ``_last_run_date`` lived only in memory; restarts could re-fire the
  same week.

After the fix the trigger condition is ``now.weekday() == 0 and last_run != today``
and the date is persisted through ``context.local_data_store``.
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from backend import weekly_report_scheduler as wrs


class _MemoryStore:
    def __init__(self) -> None:
        self.data: dict[str, object] = {}

    def save_json(self, key: str, value: object) -> None:
        self.data[key] = value

    def load_json(self, key: str, default: object) -> object:
        return self.data.get(key, default)


@pytest.fixture(autouse=True)
def _reset_module_state():
    wrs._enabled = False
    wrs._context_ref = None
    wrs._last_run_date = None
    yield
    wrs._enabled = False
    wrs._context_ref = None
    wrs._last_run_date = None


def test_set_context_loads_persisted_last_run_date() -> None:
    store = _MemoryStore()
    store.data[wrs._PERSIST_KEY] = {"date": "2026-06-22"}
    ctx = MagicMock()
    ctx.local_data_store = store
    wrs.set_context(ctx)
    assert wrs._last_run_date == "2026-06-22"


def test_set_context_missing_persist_key_keeps_none() -> None:
    store = _MemoryStore()
    ctx = MagicMock()
    ctx.local_data_store = store
    wrs.set_context(ctx)
    assert wrs._last_run_date is None


def test_persist_last_run_date_writes_through_store() -> None:
    store = _MemoryStore()
    ctx = MagicMock()
    ctx.local_data_store = store
    wrs.set_context(ctx)
    wrs._persist_last_run_date("2026-06-23")
    assert store.data[wrs._PERSIST_KEY] == {"date": "2026-06-23"}


def test_persist_without_store_is_silent() -> None:
    """No data store should not raise."""
    wrs._context_ref = None
    wrs._persist_last_run_date("2026-06-23")  # should not raise
    wrs._context_ref = MagicMock(spec=[])  # no local_data_store attr
    wrs._persist_last_run_date("2026-06-23")  # should not raise


def test_load_handles_corrupt_payload_gracefully() -> None:
    store = _MemoryStore()
    store.data[wrs._PERSIST_KEY] = "not-a-dict"
    ctx = MagicMock()
    ctx.local_data_store = store
    wrs.set_context(ctx)
    assert wrs._last_run_date is None


# ── P3-6: inbox stats + fallback formatter regression ──

import asyncio
from datetime import datetime, timezone


def _iso_days_ago(days: int) -> str:
    ts = datetime.now(timezone.utc).timestamp() - days * 86400
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()


def test_collect_inbox_stats_buckets_by_kind() -> None:
    inbox_repo = MagicMock()
    inbox_repo.list_entries.return_value = [
        {"id": 1, "kind": "todo", "status": "new", "created_at": _iso_days_ago(1)},
        {"id": 2, "kind": "todo", "status": "new", "created_at": _iso_days_ago(2)},
        {"id": 3, "kind": "note", "status": "new", "created_at": _iso_days_ago(3)},
        {"id": 4, "kind": "note", "status": "archived", "created_at": _iso_days_ago(10)},
        {"id": 5, "kind": "idea", "status": "done", "created_at": _iso_days_ago(0)},
    ]
    ctx = MagicMock()
    ctx.personal_inbox_repository = inbox_repo
    # Disable other collectors
    ctx.cost_tracker = None
    ctx.personal_note_repository = None
    ctx.personal_task_repository = None

    stats = asyncio.run(wrs._collect_weekly_stats(ctx))
    inbox = stats.get("inbox")
    assert inbox is not None, "inbox stats missing"
    assert inbox["total"] == 5
    # 4 entries created within last week (id 1,2,3,5); id 4 is 10 days old
    assert inbox["new_this_week"] == 4
    # archived/done are excluded from pending → 3 pending (id 1,2,3)
    assert inbox["pending"] == 3
    # by_kind only counts new_this_week: todo=2, note=1, idea=1
    assert inbox["by_kind"] == {"todo": 2, "note": 1, "idea": 1}


def test_collect_inbox_stats_repo_missing_is_silent() -> None:
    ctx = MagicMock(spec=[])  # no personal_inbox_repository attr
    stats = asyncio.run(wrs._collect_weekly_stats(ctx))
    assert "inbox" not in stats


def test_collect_inbox_stats_repo_raises_is_silent() -> None:
    inbox_repo = MagicMock()
    inbox_repo.list_entries.side_effect = RuntimeError("db down")
    ctx = MagicMock()
    ctx.personal_inbox_repository = inbox_repo
    ctx.cost_tracker = None
    ctx.personal_note_repository = None
    ctx.personal_task_repository = None
    stats = asyncio.run(wrs._collect_weekly_stats(ctx))
    assert "inbox" not in stats


def test_fallback_report_includes_inbox_line() -> None:
    stats = {
        "inbox": {
            "total": 12,
            "new_this_week": 5,
            "pending": 7,
            "by_kind": {"todo": 3, "note": 2},
        },
    }
    text = wrs._format_fallback_report(stats)
    assert "📊" in text
    assert "本周小结" in text
    assert "· 收件箱12条" in text
    assert "本周+5" in text
    assert "待处理7" in text
    assert "todo3" in text
    assert "note2" in text


def test_fallback_report_uses_clean_unicode_no_mojibake() -> None:
    """No 馃 (U+99C3) or 路-as-bullet (U+8DEF) survives in the formatter."""
    stats = {
        "ai": {"total_calls": 1, "month_cost": 0.5},
        "notes": {"total": 1, "recent_count": 1},
        "tasks": {"total": 1, "done": 0, "pending": 1, "done_this_week": 0},
        "inbox": {"total": 1, "new_this_week": 1, "pending": 1, "by_kind": {}},
    }
    text = wrs._format_fallback_report(stats)
    assert "\u99c3" not in text  # 馃
    # 路 may legitimately appear elsewhere, but our bullet should be · (U+00B7)
    assert "· AI调用" in text
    assert "· 共" in text
    assert "· 任务" in text
    assert "· 收件箱" in text


def test_fallback_report_omits_missing_sections() -> None:
    """Empty stats yields only the header — no crash on absent sections."""
    text = wrs._format_fallback_report({})
    assert "📊 本周小结" in text
    assert "AI调用" not in text
    assert "收件箱" not in text

