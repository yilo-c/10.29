"""Tests for supertag query features — NL parsing, filtering in memory_search."""
from __future__ import annotations

import sys
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

import pytest
from memory.search import (
    parse_supertag_query,
    _compare_field_values,
    _parse_date_safe,
)


class TestSupertagQueryParsing:
    """Natural language supertag query parsing."""

    def test_detect_task_query(self):
        """'任务' query detects @任务 supertag."""
        st_type, filters, cleaned = parse_supertag_query("我的任务")
        assert st_type == "@任务"
        assert isinstance(filters, dict)

    def test_detect_task_todo_query(self):
        """'待办事项' query detects @任务 supertag."""
        st_type, filters, cleaned = parse_supertag_query("今天的待办事项")
        assert st_type == "@任务"
        assert cleaned == "今天的" or "待办" not in cleaned

    def test_detect_high_priority_task(self):
        """'高优先级任务' detects @任务 with priority filter."""
        st_type, filters, cleaned = parse_supertag_query("高优先级任务")
        assert st_type == "@任务"
        assert filters.get("priority") == "high"

    def test_detect_urgent_task(self):
        """'紧急任务' detects @任务 with priority filter."""
        st_type, filters, cleaned = parse_supertag_query("紧急任务")
        assert st_type == "@任务"
        assert filters.get("priority") == "high"

    def test_detect_in_progress_task(self):
        """'进行中的任务' detects @任务 with status filter."""
        st_type, filters, cleaned = parse_supertag_query("进行中的任务")
        assert st_type == "@任务"
        assert filters.get("status") == "in_progress"

    def test_done_task_query(self):
        """'已完成的任务' detects @任务 with done status."""
        st_type, filters, cleaned = parse_supertag_query("已完成的任务")
        assert st_type == "@任务"
        assert filters.get("status") == "done"

    def test_active_project_query(self):
        """'进行中的项目' detects @项目 with active status."""
        st_type, filters, cleaned = parse_supertag_query("进行中的项目")
        assert st_type == "@项目"
        assert filters.get("status") == "active"

    def test_contact_query(self):
        """'联系人' query detects @联系人."""
        st_type, filters, cleaned = parse_supertag_query("北京的联系人")
        assert st_type == "@联系人"

    def test_no_supertag_query(self):
        """Query without supertag keywords returns None."""
        st_type, filters, cleaned = parse_supertag_query("今天天气怎么样")
        assert st_type is None
        assert filters == {}

    def test_archived_project_query(self):
        """'已归档的项目' detects @项目 with archived status."""
        st_type, filters, cleaned = parse_supertag_query("已归档的项目")
        assert st_type == "@项目"
        assert filters.get("status") == "archived"

    def test_empty_query(self):
        """Empty query returns no supertag."""
        st_type, filters, cleaned = parse_supertag_query("")
        assert st_type is None
        assert filters == {}


class TestFieldComparison:
    """Field value comparison utilities."""

    def test_number_compare_less(self):
        """Number less than."""
        assert _compare_field_values(1, 5) == -1

    def test_number_compare_equal(self):
        """Number equal."""
        assert _compare_field_values(3, 3) == 0

    def test_number_compare_greater(self):
        """Number greater than."""
        assert _compare_field_values(10, 3) == 1

    def test_date_string_compare(self):
        """Date string comparison."""
        assert _compare_field_values("2026-06-01", "2026-07-01") == -1
        assert _compare_field_values("2026-06-01", "2026-06-01") == 0
        assert _compare_field_values("2026-08-01", "2026-06-01") == 1


class TestJudgeSupertagSuggestion:
    """Judge supertag suggestion logic (tested via module-level functions)."""

    def test_task_extraction_priority(self):
        """Extract task priority from text."""
        from assistant.routing.judge_supertag import _extract_task_fields

        fields = _extract_task_fields("这个任务优先级很高，需要紧急处理")
        # "紧急" maps to "urgent" (not "high") because it appears as a stronger signal
        assert fields.get("priority") == "urgent"

    def test_task_extraction_deadline(self):
        """Extract task deadline from text with explicit date."""
        from assistant.routing.judge_supertag import _extract_task_fields

        fields = _extract_task_fields("截止日期：2026-06-15")
        assert fields.get("deadline") == "2026-06-15"

    def test_contact_extraction_phone(self):
        """Extract phone number from text."""
        from assistant.routing.judge_supertag import _extract_contact_fields

        fields = _extract_contact_fields("他的电话是 13800138000")
        assert fields.get("phone") == "13800138000"

    def test_contact_extraction_email(self):
        """Extract email from text."""
        from assistant.routing.judge_supertag import _extract_contact_fields

        fields = _extract_contact_fields("邮箱：test@example.com")
        assert fields.get("email") == "test@example.com"

    def test_project_extraction_owner(self):
        """Extract project owner from text."""
        from assistant.routing.judge_supertag import _extract_project_fields

        fields = _extract_project_fields("负责人：张三")
        assert fields.get("owner") == "张三"

    def test_project_extraction_status(self):
        """Extract project status from text."""
        from assistant.routing.judge_supertag import _extract_project_fields

        fields = _extract_project_fields("这个项目已经完成了")
        assert fields.get("status") == "completed"
