# -*- coding: utf-8 -*-
"""Data types for memory linker."""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


# 三级关联阈值
STRONG_THRESHOLD = 0.85
MEDIUM_THRESHOLD = 0.70
WEAK_THRESHOLD = 0.55

# 关联类型
LINK_TYPE_STRONG = "strong"
LINK_TYPE_MEDIUM = "medium"
LINK_TYPE_WEAK = "weak"


@dataclass
class MemoryLink:
    """记忆关联数据。"""
    source_id: str
    target_id: str
    similarity_score: float
    link_type: str = "medium"
    link_id: str = ""
    created_at: str = ""

    def __post_init__(self):
        if not self.link_id:
            self.link_id = uuid.uuid4().hex[:12]
        if not self.created_at:
            self.created_at = datetime.now(timezone.utc).isoformat()

    def to_dict(self) -> dict[str, Any]:
        return {
            "link_id": self.link_id,
            "source_id": self.source_id,
            "target_id": self.target_id,
            "similarity_score": self.similarity_score,
            "link_type": self.link_type,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> MemoryLink:
        return cls(
            link_id=str(data.get("link_id", "")),
            source_id=str(data.get("source_id", "")),
            target_id=str(data.get("target_id", "")),
            similarity_score=float(data.get("similarity_score", 0.0)),
            link_type=str(data.get("link_type", "medium")),
            created_at=str(data.get("created_at", "")),
        )


@dataclass
class MergeSuggestion:
    """合并建议。"""
    memory_a: dict[str, Any]
    memory_b: dict[str, Any]
    similarity_score: float
    differences: list[str] = field(default_factory=list)
    suggested_action: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "memory_a": dict(self.memory_a),
            "memory_b": dict(self.memory_b),
            "similarity_score": self.similarity_score,
            "differences": list(self.differences),
            "suggested_action": self.suggested_action,
        }
