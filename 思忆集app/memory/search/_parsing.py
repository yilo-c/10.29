"""Query parsing — supertag, time range, source label parsing.

Extracted from memory/search.py — parse_supertag_query, parse_time_range,
get_source_label, get_source_color.
"""
from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from ._constants import (
    register_supertag_patterns,
    register_time_patterns,
    TIME_PATTERNS,
    SUPERTAG_QUERY_PATTERNS,
    SOURCE_LABELS,
    SOURCE_LABEL_COLORS,
    DEFAULT_SOURCE_LABEL,
    DEFAULT_SOURCE_COLOR,
)


def parse_supertag_query(query: str) -> tuple[str | None, dict[str, Any], str]:
    """Parse natural language query for supertag filters.

    Returns:
        (supertag_type, filter_dict, cleaned_query)
    """
    if not query or not query.strip():
        return None, {}, query

    register_supertag_patterns()
    cleaned = query.strip()

    for pattern, st_type, filters in SUPERTAG_QUERY_PATTERNS:
        match = pattern.search(cleaned)
        if match:
            cleaned = pattern.sub("", cleaned).strip()
            cleaned = re.sub(r"\s+", " ", cleaned).strip()
            return st_type, dict(filters), cleaned

    return None, {}, cleaned


def parse_time_range(query: str) -> tuple[datetime | None, datetime | None, str]:
    """从查询文本中解析自然语言时间范围。

    Returns:
        (start_time, end_time, cleaned_query)
    """
    if not query:
        return None, None, query

    register_time_patterns()
    cleaned = query
    start_time: datetime | None = None
    end_time: datetime | None = None
    matched_any = False

    for pattern, factory in TIME_PATTERNS:
        match = pattern.search(cleaned)
        if match:
            matched_any = True
            result = factory(match) if hasattr(factory, "__call__") and not isinstance(factory, (datetime, type(None))) else factory
            if isinstance(result, tuple) and len(result) == 2:
                s, e = result
                start_time = s if start_time is None else min(start_time, s)
                end_time = e if end_time is None else max(end_time, e)
            cleaned = pattern.sub("", cleaned).strip()

    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    if matched_any:
        cleaned = re.sub(r"^的\s*", "", cleaned).strip()
        cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return start_time, end_time, cleaned


def get_source_label(source_type: str) -> str:
    """根据 source_type 返回带颜色标记的中文来源标签。"""
    label = SOURCE_LABELS.get(source_type, DEFAULT_SOURCE_LABEL)
    return f"[{label}]"


def get_source_color(source_type: str) -> str:
    """根据 source_type 返回对应的颜色代码。"""
    return SOURCE_LABEL_COLORS.get(source_type, DEFAULT_SOURCE_COLOR)
