from __future__ import annotations

import sys
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from workbench.viewmodel import WorkbenchViewModel


def _make_snapshot(**overrides):
    defaults = {
        "tasks": [],
        "pending_tasks": [],
        "notes": [],
        "news_records": [],
        "news_cache_items": [],
        "translations": [],
        "alarms": [],
        "favorite_news_keys": [],
        "inbox_entries": [],
        "improvement_proposals": [],
    }
    defaults.update(overrides)
    return defaults


def test_empty_snapshot_produces_fallback_suggestions():
    vm = WorkbenchViewModel()
    suggestions = vm.build_suggestions(_make_snapshot())
    assert len(suggestions) >= 1
    # No tasks + no notes: should suggest "write a note" + "capture"
    actions = [s["action"] for s in suggestions]
    assert "capture" in actions
    assert "notes" in actions


def test_pending_tasks_produce_task_suggestion():
    vm = WorkbenchViewModel()
    snapshot = _make_snapshot(pending_tasks=[{"name": "task1"}, {"name": "task2"}])
    suggestions = vm.build_suggestions(snapshot)
    actions = [s["action"] for s in suggestions]
    assert "tasks" in actions


def test_notes_exist_suggests_organize():
    vm = WorkbenchViewModel()
    snapshot = _make_snapshot(notes=[{"title": "note1"}])
    suggestions = vm.build_suggestions(snapshot)
    actions = [s["action"] for s in suggestions]
    assert "notes" in actions
    text = suggestions[0]["text"]
    assert "整理" in text


def test_pending_proposal_pinned_first():
    vm = WorkbenchViewModel()
    snapshot = _make_snapshot(
        improvement_proposals=[{"title": "urgent fix", "status": "pending_review"}],
    )
    suggestions = vm.build_suggestions(snapshot)
    assert suggestions[0]["action"] == "proposals"
    assert "urgent fix" in suggestions[0]["text"]


def test_suggestions_capped_at_max():
    vm = WorkbenchViewModel()
    many_proposals = [
        {"title": f"proposal {i}", "status": "pending_review"}
        for i in range(10)
    ]
    snapshot = _make_snapshot(improvement_proposals=many_proposals)
    suggestions = vm.build_suggestions(snapshot)
    assert len(suggestions) <= 6


def test_suggestions_meta_text():
    vm = WorkbenchViewModel()
    suggestions = [{"text": "a", "action": "tasks"}, {"text": "b", "action": "notes"}]
    proposals = [{"title": "p1", "status": "pending_review"}]
    meta = vm.build_suggestions_meta_text(suggestions, proposals)
    assert "当前建议 2 条" in meta
    assert "待审提案 1 条" in meta


def test_suggestions_meta_text_no_proposals():
    vm = WorkbenchViewModel()
    meta = vm.build_suggestions_meta_text([], [])
    assert "待审提案" not in meta


def test_recent_lists_sorts_and_limits():
    vm = WorkbenchViewModel()
    tasks = [
        {"name": "old", "updated_at": "2020-01-01"},
        {"name": "new", "updated_at": "2025-01-01"},
        {"name": "middle", "updated_at": "2023-01-01"},
        {"name": "extra", "updated_at": "2024-01-01"},
    ]
    snapshot = _make_snapshot(tasks=tasks)
    recent = vm.build_recent_lists(snapshot)
    assert len(recent["tasks"]) == 3
    assert recent["tasks"][0]["name"] == "new"
    assert recent["tasks"][1]["name"] == "extra"


def test_card_summary():
    vm = WorkbenchViewModel()
    snapshot = _make_snapshot(
        pending_tasks=[{"name": "t1"}],
        notes=[{"title": "n1"}, {"title": "n2"}],
        favorite_news_keys=["k1"],
        translations=[{"source_text": "hello"}],
        alarms=[{"id": 1}],
    )
    summary = vm.build_card_summary(snapshot)
    assert summary["tasks_pending"] == 1
    assert summary["notes_total"] == 2
    assert summary["news_favorites"] == 1
    assert summary["translations_total"] == 1
    assert summary["alarms_total"] == 1


def test_extract_item_title():
    vm = WorkbenchViewModel()
    assert vm.extract_item_title({"title": "hello"}) == "hello"
    assert vm.extract_item_title({"name": "world"}) == "world"
    assert vm.extract_item_title({"source_text": "text"}) == "text"
    assert vm.extract_item_title({"news_title": "news"}) == "news"
    assert vm.extract_item_title({}) == "未命名"


def test_extra_suggestions_prepended():
    vm = WorkbenchViewModel()
    extra = [{"text": "Doctor: API key missing", "action": "open_doctor"}]
    snapshot = _make_snapshot()
    suggestions = vm.build_suggestions(snapshot, extra_suggestions=extra)
    assert suggestions[0]["text"] == "Doctor: API key missing"
    assert suggestions[0]["action"] == "open_doctor"


def test_extra_suggestions_empty_list_no_effect():
    vm = WorkbenchViewModel()
    snapshot = _make_snapshot()
    without = vm.build_suggestions(snapshot)
    with_empty = vm.build_suggestions(snapshot, extra_suggestions=[])
    assert len(with_empty) == len(without)
    assert with_empty[0]["action"] == without[0]["action"]


def test_extra_suggestions_combined_with_regular():
    vm = WorkbenchViewModel()
    extra = [{"text": "Doctor check", "action": "open_doctor"}]
    snapshot = _make_snapshot(notes=[{"title": "n1"}])
    suggestions = vm.build_suggestions(snapshot, extra_suggestions=extra)
    assert suggestions[0]["action"] == "open_doctor"
    assert any(s["action"] == "notes" for s in suggestions)


def test_news_cache_items_preferred():
    vm = WorkbenchViewModel()
    snapshot = _make_snapshot(
        news_records=[{"title": "from_records", "updated_at": "2025-01-01"}],
        news_cache_items=[{"title": "from_cache", "updated_at": "2025-01-01"}],
    )
    recent = vm.build_recent_lists(snapshot)
    assert recent["news"][0]["title"] == "from_cache"
