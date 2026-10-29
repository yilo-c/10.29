"""Memory extractor subpackage — automatic memory extraction from conversations.

Enhanced implementation covering MCP-10 tasks:
Task 1: Post-conversation auto extraction with memory context
Task 2: Three-level confidence mechanism (>0.8 auto, 0.5-0.8 confirm, <0.5 discard)
Task 3: Conflict detection with history and resolution strategies
Task 4: Memory dedup and merging (semantic similarity)
Task 5: Privacy controls and extraction settings
"""
from __future__ import annotations

from ._privacy import (
    _SENSITIVE_PATTERNS,
    _SENSITIVE_KEYWORDS,
    PrivacyFilter,
)
from ._store import (
    PendingMemoryStore,
    DedupEngine,
    ConflictTracker,
)
from ._core import (
    MemoryExtractor,
)
from ..types import (
    MemoryCandidate,
    ExtractionResult,
    PendingMemory,
    ConflictRecord,
    AUTO_WRITE_THRESHOLD,
    CONFIRM_THRESHOLD,
)
from ._patterns import (
    PatternExtractor,
    _extract_time_bucket,
    _is_significant_change,
    _compute_shift_confidence,
)

__all__ = [
    "_SENSITIVE_PATTERNS",
    "_SENSITIVE_KEYWORDS",
    "PrivacyFilter",
    "PendingMemoryStore",
    "DedupEngine",
    "ConflictTracker",
    "MemoryExtractor",
    "PatternExtractor",
    "MemoryCandidate",
    "ExtractionResult",
    "PendingMemory",
    "ConflictRecord",
    "AUTO_WRITE_THRESHOLD",
    "CONFIRM_THRESHOLD",
    "_extract_time_bucket",
    "_is_significant_change",
    "_compute_shift_confidence",
]
