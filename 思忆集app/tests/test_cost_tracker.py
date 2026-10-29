from __future__ import annotations

import sys
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from assistant.cost_tracker import (
    TIER_LOCAL,
    TIER_JUDGE,
    TIER_MID_CLOUD,
    TIER_HIGH_CLOUD,
    CostTracker,
)


def _make_tracker():
    return CostTracker(data_store=None)


def test_record_local_tier_zero_cost():
    tracker = _make_tracker()
    tracker.record(tier=TIER_LOCAL, task_type="chat")
    assert tracker.get_today_total() == 0.0
    assert tracker.get_tier_totals()[TIER_LOCAL] == 0.0


def test_record_judge_tier():
    tracker = _make_tracker()
    tracker.record(tier=TIER_JUDGE, task_type="routing", model="deepseek-flash")
    assert tracker.get_today_total() > 0
    assert tracker.get_tier_totals()[TIER_JUDGE] > 0


def test_record_mid_cloud():
    tracker = _make_tracker()
    tracker.record(tier=TIER_MID_CLOUD, task_type="translation", model="deepseek-chat")
    assert tracker.get_today_total() > 0


def test_record_high_cloud():
    tracker = _make_tracker()
    tracker.record(tier=TIER_HIGH_CLOUD, task_type="complex_code", model="claude-sonnet-4-6")
    assert tracker.get_today_total() > 0


def test_multiple_records_accumulate():
    tracker = _make_tracker()
    tracker.record(tier=TIER_LOCAL, task_type="chat")
    tracker.record(tier=TIER_LOCAL, task_type="note")
    tracker.record(tier=TIER_JUDGE, task_type="routing")
    assert tracker.get_today_total() > 0
    assert tracker.get_tier_totals()[TIER_LOCAL] == 0.0
    assert tracker.get_tier_totals()[TIER_JUDGE] > 0


def test_daily_summary_structure():
    tracker = _make_tracker()
    tracker.record(tier=TIER_LOCAL, task_type="chat")
    tracker.record(tier=TIER_JUDGE, task_type="routing")
    summary = tracker.get_daily_summary()
    assert "date" in summary
    assert "total_cost" in summary
    assert "total_calls" in summary
    assert "tier_breakdown" in summary
    assert summary["total_calls"] == 2


def test_monthly_summary():
    tracker = _make_tracker()
    tracker.record(tier=TIER_LOCAL, task_type="chat")
    summary = tracker.get_monthly_summary()
    assert "year_month" in summary
    assert "total_cost" in summary
    assert "total_calls" in summary
    assert summary["total_calls"] >= 1


def test_saving_estimate():
    tracker = _make_tracker()
    tracker.record(tier=TIER_LOCAL, task_type="chat")
    tracker.record(tier=TIER_JUDGE, task_type="routing")
    saving = tracker._estimate_saving()
    assert "pure_cloud_estimated" in saving
    assert "actual_cost" in saving
    assert "savings_percent" in saving


def test_unknown_tier_falls_back():
    tracker = _make_tracker()
    tracker.record(tier="unknown_tier", task_type="test")
    assert tracker.get_tier_totals().get("unknown_tier", 0) == 0.0


def test_status_snapshot():
    tracker = _make_tracker()
    tracker.record(tier=TIER_LOCAL, task_type="chat")
    snapshot = tracker.get_status_snapshot()
    assert "today_cost" in snapshot
    assert "month_cost" in snapshot
    assert "total_calls" in snapshot
    assert snapshot["total_calls"] >= 1


def test_custom_cost():
    tracker = _make_tracker()
    tracker.record(tier=TIER_HIGH_CLOUD, cost_usd=0.05, task_type="big_task", tokens=5000)
    assert tracker.get_today_total() == 0.05
