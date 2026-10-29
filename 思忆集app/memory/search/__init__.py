"""Hybrid memory search subpackage — ChromaDB + FTS5 BM25 with RRF fusion.

Features:
- Hybrid search (vector + keyword) with configurable search_mode
- RRF (Reciprocal Rank Fusion) with k=60
- Source labels: [记忆], [知识库], [笔记], [网页]
- Natural language time range parsing
- Full backward compatibility
"""
from __future__ import annotations

from ._constants import (
    SUPERTAG_QUERY_PATTERNS,
    SOURCE_LABELS,
    SOURCE_LABEL_COLORS,
    DEFAULT_SOURCE_LABEL,
    DEFAULT_SOURCE_COLOR,
    TIME_PATTERNS,
    register_supertag_patterns,
    register_time_patterns,
    _make_part_of_day_fn,
)
from ._parsing import (
    parse_supertag_query,
    parse_time_range,
    get_source_label,
    get_source_color,
)
from ._ranking import RankingMixin
from ._filtering import FilteringMixin, _parse_datetime_safe, _compare_field_values, _parse_date_safe
from ._chroma import ChromaMixin
from ._engine import HybridMemorySearch

__all__ = [
    "SUPERTAG_QUERY_PATTERNS",
    "SOURCE_LABELS",
    "SOURCE_LABEL_COLORS",
    "DEFAULT_SOURCE_LABEL",
    "DEFAULT_SOURCE_COLOR",
    "TIME_PATTERNS",
    "register_supertag_patterns",
    "register_time_patterns",
    "_make_part_of_day_fn",
    "parse_supertag_query",
    "parse_time_range",
    "get_source_label",
    "get_source_color",
    "RankingMixin",
    "FilteringMixin",
    "_parse_datetime_safe",
    "_compare_field_values",
    "_parse_date_safe",
    "ChromaMixin",
    "HybridMemorySearch",
]
