# -*- coding: utf-8 -*-
"""Data types for memory extraction system."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


# ── Constants ────────────────────────────────────────────────────────────

AUTO_WRITE_THRESHOLD = 0.8
CONFIRM_THRESHOLD = 0.5
CONFIDENCE_MIN = 0.0
CONFIDENCE_MAX = 1.0

# ── Memory type taxonomy ────────────────────────────────────────────────

MEMORY_TYPE_FACTUAL = "factual"
MEMORY_TYPE_PREFERENCE = "preference"
MEMORY_TYPE_DECISION = "decision"
MEMORY_TYPE_PROJECT = "project"
MEMORY_TYPE_RELATIONSHIP = "relationship"
MEMORY_TYPE_INTENT = "intent"
MEMORY_TYPE_PROCEDURAL = "procedural"

MEMORY_TYPE_LABELS: dict[str, str] = {
    MEMORY_TYPE_FACTUAL: "事实记忆",
    MEMORY_TYPE_PREFERENCE: "偏好记忆",
    MEMORY_TYPE_DECISION: "决策记忆",
    MEMORY_TYPE_PROJECT: "项目记忆",
    MEMORY_TYPE_RELATIONSHIP: "关系记忆",
    MEMORY_TYPE_INTENT: "意图记忆",
    MEMORY_TYPE_PROCEDURAL: "过程记忆",
}

VALID_MEMORY_TYPES = frozenset(MEMORY_TYPE_LABELS)

# ── Privacy level constants ─────────────────────────────────────────────

PRIVACY_LOCAL_ONLY = "local_only"
PRIVACY_SHAREABLE = "shareable"
PRIVACY_CONSENT_REQUIRED = "consent_required"

# Dedup thresholds
SIMILARITY_DUPLICATE = 0.9   # >0.9: discard new
SIMILARITY_MERGE = 0.75       # 0.75-0.9: merge details

# Conflict resolution
CONFLICT_CONFIDENCE_GAP = 0.2  # new must exceed old by this much to auto-replace


@dataclass
class MemoryCandidate:
    """A candidate memory entry extracted from conversation."""
    type: str
    content: str
    confidence: float
    source: str
    tags: list[str] = field(default_factory=list)
    reason: str = ""
    privacy_level: str = ""  # local_only / shareable / consent_required / ""=unchecked

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": self.type,
            "content": self.content,
            "confidence": self.confidence,
            "source": self.source,
            "tags": list(self.tags),
            "reason": self.reason,
            "privacy_level": self.privacy_level,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> MemoryCandidate:
        return cls(
            type=str(data.get("type", "factual")),
            content=str(data.get("content", "")),
            confidence=min(CONFIDENCE_MAX, max(CONFIDENCE_MIN, float(data.get("confidence", 0.0)))),
            source=str(data.get("source", "user")),
            tags=[str(t).strip() for t in (data.get("tags") or []) if str(t).strip()],
            reason=str(data.get("reason") or ""),
            privacy_level=str(data.get("privacy_level") or ""),
        )


@dataclass
class ExtractionResult:
    """Result of a single extraction operation."""
    candidates: list[MemoryCandidate] = field(default_factory=list)
    auto_write: list[MemoryCandidate] = field(default_factory=list)
    needs_confirm: list[MemoryCandidate] = field(default_factory=list)
    discarded: list[MemoryCandidate] = field(default_factory=list)
    conflicts: list[dict[str, Any]] = field(default_factory=list)
    duplicates: list[MemoryCandidate] = field(default_factory=list)
    merged: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidates": [c.to_dict() for c in self.candidates],
            "auto_write": [c.to_dict() for c in self.auto_write],
            "needs_confirm": [c.to_dict() for c in self.needs_confirm],
            "discarded": [c.to_dict() for c in self.discarded],
            "conflicts": list(self.conflicts),
            "duplicates": [c.to_dict() for c in self.duplicates],
            "merged": list(self.merged),
        }


@dataclass
class PendingMemory:
    """Memory awaiting user confirmation (medium confidence queue)."""
    candidate: MemoryCandidate
    created_at: str
    conversation_id: str = ""
    confirm_count: int = 0
    last_prompted_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidate": self.candidate.to_dict(),
            "created_at": self.created_at,
            "conversation_id": self.conversation_id,
            "confirm_count": self.confirm_count,
            "last_prompted_at": self.last_prompted_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PendingMemory:
        return cls(
            candidate=MemoryCandidate.from_dict(data.get("candidate") or {}),
            created_at=str(data.get("created_at") or ""),
            conversation_id=str(data.get("conversation_id") or ""),
            confirm_count=int(data.get("confirm_count", 0)),
            last_prompted_at=str(data.get("last_prompted_at") or ""),
        )


@dataclass
class ConflictRecord:
    """Record of a memory conflict with history and resolution strategies."""
    new_content: str
    existing_content: str
    new_confidence: float
    existing_confidence: float
    detected_at: str
    resolved: bool = False
    resolution: str = ""  # keep_new | keep_old | keep_both | user_resolved
    resolved_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "new_content": self.new_content,
            "existing_content": self.existing_content,
            "new_confidence": self.new_confidence,
            "existing_confidence": self.existing_confidence,
            "detected_at": self.detected_at,
            "resolved": self.resolved,
            "resolution": self.resolution,
            "resolved_at": self.resolved_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ConflictRecord:
        return cls(
            new_content=str(data.get("new_content", "")),
            existing_content=str(data.get("existing_content", "")),
            new_confidence=float(data.get("new_confidence", 0.0)),
            existing_confidence=float(data.get("existing_confidence", 0.0)),
            detected_at=str(data.get("detected_at", "")),
            resolved=bool(data.get("resolved", False)),
            resolution=str(data.get("resolution", "")),
            resolved_at=str(data.get("resolved_at", "")),
        )
