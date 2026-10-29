# -*- coding: utf-8 -*-
"""Data Classification Service (Layer 3).

Four-level data classification:
- local_only: Never leaves device (private notes, API keys, device private keys)
- syncable: Synced between devices (notes, tasks, wordbook)
- shareable: Can be shared with others (public notes, shared links)
- model_sendable: Can be sent as LLM context (preferences, non-sensitive memory)

Usage:
    from api.security.data_classification import (
        DataLevel, classify_resource, enforce_export_rule
    )

    level = classify_resource("note", {"tags": ["private"]})
    enforce_export_rule(level, target="sync")
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any


class DataLevel(Enum):
    """Four levels of data classification."""
    LOCAL_ONLY = "local_only"
    SYNCABLE = "syncable"
    SHAREABLE = "shareable"
    MODEL_SENDABLE = "model_sendable"


# Level ordering for comparison (0 = most restricted)
_LEVEL_ORDER = [
    DataLevel.LOCAL_ONLY,
    DataLevel.SYNCABLE,
    DataLevel.SHAREABLE,
    DataLevel.MODEL_SENDABLE,
]

_LEVEL_RANK = {level: i for i, level in enumerate(_LEVEL_ORDER)}


def level_rank(level: DataLevel) -> int:
    """Return numeric rank of a data level (0 = most restricted)."""
    return _LEVEL_RANK.get(level, 0)


def is_at_least(minimum: DataLevel, actual: DataLevel) -> bool:
    """Check if `actual` meets or exceeds `minimum` level."""
    return level_rank(actual) >= level_rank(minimum)


# Classification rules for resource types
_DEFAULT_LEVELS: dict[str, DataLevel] = {
    # Local-only: never leaves the device
    "api_key": DataLevel.LOCAL_ONLY,
    "device_private_key": DataLevel.LOCAL_ONLY,
    "secret": DataLevel.LOCAL_ONLY,
    "local_draft": DataLevel.LOCAL_ONLY,
    "private_vault": DataLevel.LOCAL_ONLY,

    # Syncable: device-to-device sync
    "note": DataLevel.SYNCABLE,
    "task": DataLevel.SYNCABLE,
    "wordbook": DataLevel.SYNCABLE,
    "inbox_item": DataLevel.SYNCABLE,
    "translation": DataLevel.SYNCABLE,
    "news_item": DataLevel.SYNCABLE,

    # Shareable: can be shared with other users
    "public_note": DataLevel.SHAREABLE,
    "shared_link": DataLevel.SHAREABLE,
    "published_content": DataLevel.SHAREABLE,

    # Model-sendable: can be LLM context
    "preference": DataLevel.MODEL_SENDABLE,
    "memory": DataLevel.MODEL_SENDABLE,
    "usage_stat": DataLevel.MODEL_SENDABLE,
    "cached_query": DataLevel.MODEL_SENDABLE,
}

# Tags that override or refine classification
_TAG_OVERRIDES: dict[str, DataLevel] = {
    "private": DataLevel.LOCAL_ONLY,
    "confidential": DataLevel.LOCAL_ONLY,
    "secret": DataLevel.LOCAL_ONLY,
    "draft": DataLevel.LOCAL_ONLY,
    "sync_disabled": DataLevel.LOCAL_ONLY,
    "public": DataLevel.SHAREABLE,
    "shared": DataLevel.SHAREABLE,
    "llm_context": DataLevel.MODEL_SENDABLE,
}


def classify_resource(resource_type: str, metadata: dict[str, Any] | None = None) -> DataLevel:
    """Determine the data classification level for a resource.

    Args:
        resource_type: Type of the resource (e.g. "note", "task", "api_key").
        metadata: Optional dict that may include "tags", "classification" keys.

    Returns:
        The determined DataLevel.
    """
    metadata = metadata or {}

    # Explicit classification in metadata takes highest precedence
    explicit = metadata.get("classification")
    if explicit and isinstance(explicit, str):
        try:
            return DataLevel(explicit)
        except ValueError:
            pass

    # Tag-based overrides (most restrictive tag wins)
    tags = metadata.get("tags") or []
    if isinstance(tags, list):
        most_restrictive = None
        for tag in tags:
            tag_lower = str(tag).lower().strip()
            override = _TAG_OVERRIDES.get(tag_lower)
            if override is not None:
                if most_restrictive is None or level_rank(override) < level_rank(most_restrictive):
                    most_restrictive = override
        if most_restrictive is not None:
            return most_restrictive

    # Default level by resource type
    return _DEFAULT_LEVELS.get(resource_type, DataLevel.SYNCABLE)


def enforce_export_rule(level: DataLevel, target: str) -> None:
    """Enforce data classification export rules.

    Args:
        level: The classification level of the data.
        target: The export/sync target ("sync", "share", "model", "local").

    Raises:
        PermissionError: If the target is not allowed for this classification level.
    """
    _TARGET_MIN_LEVEL = {
        "local": DataLevel.LOCAL_ONLY,
        "sync": DataLevel.SYNCABLE,
        "share": DataLevel.SHAREABLE,
        "model": DataLevel.MODEL_SENDABLE,
    }

    required = _TARGET_MIN_LEVEL.get(target)
    if required is None:
        raise ValueError(f"unknown export target: {target}")

    if not is_at_least(required, level):
        raise PermissionError(
            f"Data classified as '{level.value}' cannot be exported to '{target}' "
            f"(requires at least '{required.value}')"
        )


def is_export_allowed(level: DataLevel, target: str) -> bool:
    """Check if data can be exported to the given target without raising."""
    try:
        enforce_export_rule(level, target)
        return True
    except (PermissionError, ValueError):
        return False


@dataclass
class ClassificationCheckResult:
    """Result of a classification enforcement check."""
    allowed: bool
    reason: str
    level: DataLevel


def check_resource_export(resource_type: str, target: str, metadata: dict[str, Any] | None = None) -> ClassificationCheckResult:
    """Check whether a resource can be exported to the given target.

    Args:
        resource_type: Resource type string.
        target: Export target ("sync", "share", "model", "local").
        metadata: Optional resource metadata for classification.

    Returns:
        ClassificationCheckResult with decision and reason.
    """
    level = classify_resource(resource_type, metadata)
    if is_export_allowed(level, target):
        return ClassificationCheckResult(
            allowed=True,
            reason=f"Resource '{resource_type}' classified as '{level.value}' — allowed for '{target}'",
            level=level,
        )
    return ClassificationCheckResult(
        allowed=False,
        reason=f"Resource '{resource_type}' classified as '{level.value}' — blocked for '{target}'",
        level=level,
    )
