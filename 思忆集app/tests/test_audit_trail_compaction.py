# -*- coding: utf-8 -*-
"""T1.1 回归测试：审计轨迹快照截断与逻辑条数上限。

背景：审计写入从「整列表读改写」迁移到独立 audit_trail.db 单写线程（audit_writer）。
本测试覆盖：快照截断、条数逻辑上限（list_entries 合并上限）、intent→complete/failed 流程。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.audit_rollback import AuditTrail  # noqa: E402


@pytest.fixture
def trail():
    return AuditTrail()


# ── 快照截断 ──────────────────────────────────────────────


def test_safe_copy_truncates_long_strings(trail):
    big = {"content": "x" * 10000, "title": "短标题", "tags": ["a" * 5000]}
    snap = trail._safe_copy(big)
    assert len(snap["content"]) <= AuditTrail.MAX_SNAPSHOT_CHARS + 32
    assert snap["content"].endswith("…[truncated]")
    assert snap["title"] == "短标题"
    assert snap["tags"][0].endswith("…[truncated]")


def test_safe_copy_keeps_small_values(trail):
    snap = trail._safe_copy({"a": 1, "b": "ok", "c": [1, 2], "d": None})
    assert snap == {"a": 1, "b": "ok", "c": [1, 2], "d": None}


def test_record_write_intent_truncates_snapshot(trail):
    entry_id = trail.record_write_intent(
        actor="user", action_id="notes.update", target_type="note",
        target_id="1", operation="update",
        before_snapshot={"content": "y" * 20000},
    )
    assert entry_id
    entry = trail.get_entry(entry_id)
    assert entry is not None
    assert len(entry["before_snapshot"]["content"]) <= AuditTrail.MAX_SNAPSHOT_CHARS + 32


# ── 条数逻辑上限（list_entries 合并上限）─────────────────


def test_list_entries_caps_at_max_trail_size(trail):
    n = AuditTrail.MAX_TRAIL_SIZE + 300
    for i in range(n):
        trail.record_write_intent(
            actor="u", action_id=f"a.{i}", target_type="note",
            target_id=str(i), operation="update", before_snapshot=None,
        )
    entries = trail.list_entries(limit=n)
    assert len(entries) == AuditTrail.MAX_TRAIL_SIZE


def test_list_entries_logical_cap_small_override(trail):
    trail.MAX_TRAIL_SIZE = 10
    for i in range(15):
        eid = trail.record_write_intent(actor="u", action_id=f"a.{i}", target_type="note")
        trail.record_write_complete(eid)
    entries = trail.list_entries(limit=100)
    assert len(entries) == 10


# ── 端到端：intent → complete / failed 不回归 ────────────


def test_intent_then_complete_flow(trail):
    entry_id = trail.record_write_intent(
        actor="user", action_id="notes.update", target_type="note",
        target_id="42", operation="update", before_snapshot={"v": 1},
    )
    ok = trail.record_write_complete(entry_id, after_snapshot={"v": 2})
    assert ok is True

    entries = trail.list_entries(limit=10)
    assert len(entries) == 1
    assert entries[0]["status"] == "completed"
    assert entries[0]["after_snapshot"] == {"v": 2}


def test_write_failed_flow(trail):
    entry_id = trail.record_write_intent(
        actor="user", action_id="notes.delete", target_type="note",
        target_id="7", operation="delete", before_snapshot=None,
    )
    ok = trail.record_write_failed(entry_id, error="boom")
    assert ok is True
    entry = trail.get_entry(entry_id)
    assert entry["status"] == "failed"
    assert entry["error"] == "boom"
