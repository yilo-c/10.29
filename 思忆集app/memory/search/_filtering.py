"""Search result filtering — time range and supertag filters.

Extracted from memory/search.py — _filter_by_time, _filter_by_supertag,
and helper functions _parse_datetime_safe, _compare_field_values, _parse_date_safe.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any


class FilteringMixin:
    """Mixin providing search result filtering methods.

    Expects no specific instance attributes — all inputs are passed as parameters.
    """

    @staticmethod
    def _filter_by_time(
        items: list[dict[str, Any]],
        start_time: datetime | None,
        end_time: datetime | None,
    ) -> list[dict[str, Any]]:
        """按时间范围过滤结果。"""
        if not start_time and not end_time:
            return items

        # 归一化边界为 aware local，避免与带时区的条目时间比较时偏移
        start_aware = _to_aware_local(start_time) if start_time else None
        end_aware = _to_aware_local(end_time) if end_time else None

        filtered = []
        for item in items:
            updated_str = item.get("updated_at") or ""
            if not updated_str:
                filtered.append(item)
                continue
            item_dt = _parse_datetime_safe(updated_str)
            if item_dt is None:
                filtered.append(item)
                continue
            item_aware = _to_aware_local(item_dt)
            if start_aware and item_aware < start_aware:
                continue
            if end_aware and item_aware > end_aware:
                continue
            filtered.append(item)
        return filtered

    @staticmethod
    def _filter_by_supertag(
        items: list[dict[str, Any]],
        supertag_filters: dict[str, Any],
        tag_service=None,
    ) -> list[dict[str, Any]]:
        """按超级标签字段过滤结果。"""
        if not supertag_filters:
            return items

        target_supertag = supertag_filters.get("supertag", "")
        if not target_supertag:
            return items

        field_eq: dict[str, str] = {}
        field_not: dict[str, str] = {}
        field_lte: dict[str, Any] = {}
        field_gte: dict[str, Any] = {}

        for key, value in supertag_filters.items():
            if key == "supertag" or key.startswith("_"):
                continue
            if key.endswith("_not"):
                field_not[key[:-4]] = value
            elif key.endswith("_lte"):
                field_lte[key[:-4]] = value
            elif key.endswith("_gte"):
                field_gte[key[:-4]] = value
            else:
                field_eq[key] = value

        deadline_range = supertag_filters.get("deadline_range")
        if deadline_range:
            now = datetime.now()
            if deadline_range == "this_week":
                end_of_week = now + timedelta(days=(6 - now.weekday()))
                end_of_week = end_of_week.replace(hour=23, minute=59, second=59)
                field_lte["deadline"] = end_of_week.strftime("%Y-%m-%d")
                field_gte["deadline"] = now.strftime("%Y-%m-%d")

        filtered: list[dict[str, Any]] = []
        for item in items:
            structured: dict[str, Any] = {}
            if tag_service is not None:
                entry_id = item.get("id") or item.get("entry_id")
                if entry_id:
                    structured = tag_service.get_structured_data(entry_id)

            item_data = structured.get(target_supertag, {})

            if not item_data:
                tags_text = str(item.get("tags") or item.get("tags_text") or "")
                if target_supertag not in tags_text:
                    continue

            match = True
            for field, expected_val in field_eq.items():
                actual = item_data.get(field)
                if actual is None or str(actual) != str(expected_val):
                    match = False
                    break
            if not match:
                continue

            for field, not_val in field_not.items():
                actual = item_data.get(field)
                if actual is not None and str(actual) == str(not_val):
                    match = False
                    break
            if not match:
                continue

            for field, max_val in field_lte.items():
                actual = item_data.get(field)
                if actual is None:
                    continue
                cmp = _compare_field_values(actual, max_val)
                if cmp is None or cmp > 0:
                    match = False
                    break
            if not match:
                continue

            for field, min_val in field_gte.items():
                actual = item_data.get(field)
                if actual is None:
                    continue
                cmp = _compare_field_values(actual, min_val)
                if cmp is None or cmp < 0:
                    match = False
                    break
            if not match:
                continue

            filtered.append(item)

        return filtered


# ── Helper functions ──


def _parse_datetime_safe(value: str) -> datetime | None:
    """安全解析日期时间字符串（保留时区信息，避免 UTC 被当作本地时间比较）。"""
    text = str(value or "").strip()
    if not text:
        return None
    # Python 3.11+ fromisoformat 原生支持 "Z" 与 "+00:00" 偏移，保留时区
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        pass
    for candidate in (text.replace("Z", "+00:00"), text):
        try:
            return datetime.fromisoformat(candidate)
        except ValueError:
            continue
    for fmt, length in (
        ("%Y-%m-%d %H:%M", 16),
        ("%Y-%m-%dT%H:%M:%S", 19),
        ("%Y-%m-%d", 10),
    ):
        try:
            return datetime.strptime(text[:length], fmt)
        except ValueError:
            continue
    return None


def _to_aware_local(dt: datetime) -> datetime:
    """归一化为带本地时区的 aware datetime。

    naive 视为本地时间；aware 转换为本地时区。确保条目时间（可能为 UTC）
    与时间范围（来自 ``datetime.now()``，naive 本地）可在同一基准下比较，
    避免跨日边界偏移（如中国 UTC+8 用户"昨天"过滤错位 8 小时）。
    """
    return dt.astimezone()


def _compare_field_values(actual: Any, expected: Any) -> int | None:
    """Compare two field values, returning -1/0/1 or None if incomparable.

    Supports: strings (lexicographic), numbers, dates (YYYY-MM-DD).
    """
    if isinstance(actual, (int, float)) and isinstance(expected, (int, float)):
        if actual < expected:
            return -1
        if actual == expected:
            return 0
        return 1

    if isinstance(actual, str) and isinstance(expected, str):
        a_dt = _parse_date_safe(actual)
        e_dt = _parse_date_safe(expected)
        if a_dt is not None and e_dt is not None:
            if a_dt < e_dt:
                return -1
            if a_dt == e_dt:
                return 0
            return 1

    str_actual = str(actual)
    str_expected = str(expected)
    if str_actual < str_expected:
        return -1
    if str_actual == str_expected:
        return 0
    return 1


def _parse_date_safe(value: str) -> datetime | None:
    """Parse a date-only string (YYYY-MM-DD)."""
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.strptime(text[:10], "%Y-%m-%d")
    except (ValueError, TypeError):
        return None
