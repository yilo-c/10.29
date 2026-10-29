# -*- coding: utf-8 -*-
"""Device identity verification service (Layer 4).

Provides device identity binding and verification functions to
ensure that device tokens are used only by their registered devices.
"""

from __future__ import annotations

import hashlib
import logging
from typing import Any

from automation_hub.api.db import get_db_connection
from automation_hub.api.audit.service import log_event

logger = logging.getLogger(__name__)


def verify_device_identity(device_id: str, user_id: str) -> bool:
    """Verify that a device_id belongs to the specified user.

    Args:
        device_id: The device identifier to verify.
        user_id: The user who should own the device.

    Returns:
        True if the device belongs to the user, False otherwise.
    """
    with get_db_connection() as conn:
        row = conn.execute(
            "SELECT id FROM devices WHERE id=? AND user_id=?",
            (device_id, user_id),
        ).fetchone()
        return row is not None


def get_device_fingerprint(device_id: str) -> str | None:
    """Get the stored device fingerprint for a device.

    Args:
        device_id: The device identifier.

    Returns:
        The stored fingerprint hash, or None if not found.
    """
    with get_db_connection() as conn:
        # Check if the devices table has a fingerprint column
        try:
            row = conn.execute(
                "SELECT fingerprint FROM devices WHERE id=?",
                (device_id,),
            ).fetchone()
            if row:
                return row["fingerprint"]
        except Exception:
            logger.debug("fingerprint column not available in devices table")
    return None


def verify_device_fingerprint(device_id: str, expected_fingerprint: str) -> bool:
    """Verify a device's fingerprint against its stored fingerprint.

    Args:
        device_id: The device identifier.
        expected_fingerprint: The fingerprint to verify.

    Returns:
        True if the fingerprint matches or no fingerprint is stored.
    """
    stored = get_device_fingerprint(device_id)
    if stored is None:
        # No fingerprint stored — allow (backward compatible)
        return True
    return stored == hashlib.sha256(expected_fingerprint.encode()).hexdigest()


def get_device_info(device_id: str) -> dict[str, Any] | None:
    """Get device info including registration time and last used.

    Args:
        device_id: The device identifier.

    Returns:
        Dict with device info, or None if not found.
    """
    with get_db_connection() as conn:
        row = conn.execute(
            """SELECT id, user_id, name, platform, created_at
               FROM devices WHERE id=?""",
            (device_id,),
        ).fetchone()
        if not row:
            return None
        return dict(row)


def list_user_devices(user_id: str) -> list[dict[str, Any]]:
    """List all devices registered to a user.

    Args:
        user_id: The user identifier.

    Returns:
        List of device info dicts.
    """
    with get_db_connection() as conn:
        rows = conn.execute(
            """SELECT id, name, platform, created_at
               FROM devices WHERE user_id=?
               ORDER BY created_at DESC""",
            (user_id,),
        ).fetchall()
        return [dict(r) for r in rows]


def revoke_device_tokens(device_id: str, user_id: str) -> int:
    """Revoke all tokens associated with a device.

    Args:
        device_id: The device identifier.
        user_id: The owning user.

    Returns:
        Number of tokens revoked.
    """
    with get_db_connection() as conn:
        cursor = conn.execute(
            """UPDATE api_tokens SET revoked_at=?
               WHERE device_id=? AND user_id=? AND revoked_at IS NULL""",
            (__import__("datetime").datetime.now(timezone=__import__("datetime").timezone.utc).isoformat(),
             device_id, user_id),
        )
        conn.commit()
        count = cursor.rowcount

    log_event(
        event_type="auth.device_tokens_revoked",
        action="revoke_device",
        status="success",
        actor_user_id=user_id,
        actor_device_id=device_id,
        resource_type="device",
        resource_id=device_id,
        message=f"revoked {count} tokens for device",
        meta={"token_count": count},
    )
    return count
