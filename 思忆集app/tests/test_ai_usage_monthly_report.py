"""usage.monthly_report 测试 — 月度成本摘要 + 投递收件箱（T7.5）。

覆盖：月度摘要生成（stats/budget 聚合）、post_to_inbox 投递、tracker 缺失降级、注册齐全。
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from backend.handlers._ai_usage import register_ai_usage


class _Registry:
    def __init__(self):
        self.registered: dict[str, object] = {}

    def register(self, name: str, fn) -> None:
        self.registered[name] = fn


class _FakeTracker:
    def get_stats(self, days=7, provider=None):
        return {
            "total_cost": 1.2345,
            "total_calls": 42,
            "total_tokens": 50000,
            "by_provider": [
                {"provider": "deepseek", "tokens": 40000, "cost": 1.0, "calls": 35},
                {"provider": "moonshot", "tokens": 10000, "cost": 0.2345, "calls": 7},
            ],
        }

    def check_budget(self):
        return {"under_budget": True, "budget": 50.0, "spent": 1.2345}


class _FakeInboxRepo:
    def __init__(self):
        self.entries = []

    def add_entry(self, entry):
        self.entries.append(entry)
        return len(self.entries)


def _make_context():
    inbox = _FakeInboxRepo()
    ctx = SimpleNamespace(personal_inbox_repository=inbox)
    ctx._inbox = inbox
    return ctx


# ── usage.monthly_report ──


def test_monthly_report_generates_summary():
    reg = _Registry()
    ctx = _make_context()
    with patch("ai.usage_tracker.get_usage_tracker", return_value=_FakeTracker()):
        register_ai_usage(reg, ctx)
        result = asyncio.run(reg.registered["usage.monthly_report"]())

    assert result["enabled"] is True
    assert result["month_label"]
    assert "1.2345" in result["summary"]
    assert "42 次" in result["summary"]
    assert "deepseek" in result["summary"]
    assert result["posted"] is False  # 默认不投递


def test_monthly_report_post_to_inbox():
    reg = _Registry()
    ctx = _make_context()
    with patch("ai.usage_tracker.get_usage_tracker", return_value=_FakeTracker()):
        register_ai_usage(reg, ctx)
        result = asyncio.run(reg.registered["usage.monthly_report"](post_to_inbox=True))

    assert result["posted"] is True
    assert len(ctx._inbox.entries) == 1
    entry = ctx._inbox.entries[0]
    assert entry["title"].startswith("💸")
    assert "成本月报" in entry["title"]
    assert entry["kind"] == "narrative"


def test_monthly_report_tracker_missing():
    reg = _Registry()
    ctx = _make_context()
    with patch("ai.usage_tracker.get_usage_tracker", return_value=None):
        register_ai_usage(reg, ctx)
        result = asyncio.run(reg.registered["usage.monthly_report"]())

    assert result["enabled"] is False
    assert "not initialized" in result["error"]


def test_register_monthly_report_method():
    reg = _Registry()
    register_ai_usage(reg, _make_context())
    assert "usage.monthly_report" in reg.registered
