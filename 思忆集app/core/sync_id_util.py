"""Unified ID Strategy — 64-char hex entity ID generation.

Design (from cross-device-sync-spec.md 搂2):
    {entity_type}_{timestamp_ms}_{random_16_hex}

Rules:
    - Entity types must match siyi_model_contract.yaml object type names.
    - Desktop is the sole authoritative ID generator.
    - Phone references desktop-generated IDs only.
    - Phone local cache uses (id, synced_at) as composite primary key.
"""
from __future__ import annotations

import secrets
import time
from typing import Final

# Max length for entity_type prefix to stay within 64-char limit
_MAX_TYPE_LEN: Final[int] = 20
_TIMESTAMP_DIGITS: Final[int] = 13  # ms timestamp fits in 13 digits
_RANDOM_HEX_DIGITS: Final[int] = 16
_SEPARATOR: Final[str] = "_"

# Overhead: 2 x "_" = 2 chars
_OVERHEAD: Final[int] = 2
_MAX_ID_LEN: Final[int] = _MAX_TYPE_LEN + _OVERHEAD + _TIMESTAMP_DIGITS + _RANDOM_HEX_DIGITS


def generate_entity_id(entity_type: str) -> str:
    """Generate a 64-char hex entity ID.

    Args:
        entity_type: Object type name matching model contract (e.g. 'note', 'task').

    Returns:
        64-char hex string: {entity_type}_{timestamp_ms}_{random_16_hex}
    """
    if not entity_type or not isinstance(entity_type, str):
        raise ValueError(f"entity_type must be a non-empty string, got {entity_type!r}")

    # Truncate type if too long, to maintain total <= 64 chars
    if len(entity_type) > _MAX_TYPE_LEN:
        entity_type = entity_type[:_MAX_TYPE_LEN]

    timestamp_ms = int(time.time() * 1000)
    random_hex = secrets.token_hex(8)  # 8 bytes → 16 hex chars

    raw = f"{entity_type}{_SEPARATOR}{timestamp_ms}{_SEPARATOR}{random_hex}"
    if len(raw) > _MAX_ID_LEN:
        raise RuntimeError(
            f"Generated ID {raw!r} exceeds max length {_MAX_ID_LEN}: "
            f"type={entity_type!r} ts={timestamp_ms}"
        )
    return raw


def validate_entity_id(entity_id: str) -> bool:
    """Validate that an entity ID matches the expected format.

    Returns True if valid, False otherwise.
    """
    if not entity_id or not isinstance(entity_id, str):
        return False
    parts = entity_id.split(_SEPARATOR)
    if len(parts) < 3:
        return False
    # Last part must be 16 hex chars
    return len(parts[-1]) == _RANDOM_HEX_DIGITS and all(c in "0123456789abcdef" for c in parts[-1])


def extract_entity_type(entity_id: str) -> str | None:
    """Extract the entity type prefix from a valid entity ID."""
    if not validate_entity_id(entity_id):
        return None
    parts = entity_id.split(_SEPARATOR)
    return parts[0]


def extract_timestamp_ms(entity_id: str) -> int | None:
    """Extract the Unix ms timestamp from a valid entity ID."""
    if not validate_entity_id(entity_id):
        return None
    parts = entity_id.split(_SEPARATOR)
    try:
        return int(parts[1])
    except (ValueError, IndexError):
        return None
