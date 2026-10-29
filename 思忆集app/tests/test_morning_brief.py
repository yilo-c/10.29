# -*- coding: utf-8 -*-
"""P2-8：晨报卡测试 —— 默认启用 07:00 + proactive.triggered 双通道投递。"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from core.events import event_bus


def test_daily_summary_defaults_enabled_at_0700():
    from backend import weekly_report_scheduler as wrs

    assert wrs._daily_enabled is True, "晨报卡应默认启用"
    assert wrs._daily_summary_time == "07:00"
    cfg = wrs.get_config()
    assert cfg["daily_enabled"] is True
    assert cfg["daily_summary_time"] == "07:00"


def test_daily_summary_ready_emits_both_channels():
    from backend import weekly_report_scheduler as wrs

    received: list[tuple[str, dict]] = []

    def collector(event):
        received.append((event.event_type, event.payload or {}))

    event_bus.on("daily_summary.ready", collector)
    event_bus.on("proactive.triggered", collector)
    try:
        wrs._emit_daily_summary_ready("今日完成 3 项任务", 42, {})
    finally:
        event_bus.off("daily_summary.ready", collector)
        event_bus.off("proactive.triggered", collector)

    types = [t for t, _ in received]
    assert "daily_summary.ready" in types
    assert "proactive.triggered" in types

    proactive = next(p for t, p in received if t == "proactive.triggered")
    assert proactive["trigger"] == "morning_brief"
    assert proactive["channel"] == "inbox"
    assert proactive["metadata"]["inbox_id"] == 42
    assert "今日完成" in proactive["content"]


def test_configure_daily_time_fallback_is_0700():
    """configure_daily 未传时间时回退 07:00 而非旧默认 09:00。"""
    from backend import weekly_report_scheduler as wrs

    original_enabled = wrs._daily_enabled
    original_time = wrs._daily_summary_time
    try:
        result = wrs.configure_daily(enabled=True, time="")
        assert result["time"] == "07:00"
    finally:
        wrs._daily_enabled = original_enabled
        wrs._daily_summary_time = original_time
