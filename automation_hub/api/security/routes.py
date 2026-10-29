# -*- coding: utf-8 -*-
"""Security-related API routes.

Provides endpoints for:
- Data export and delete (Layer 3 data protection)
- Device identity verification (Layer 4)
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from automation_hub.api.auth.deps import require_scope, get_current_token
from automation_hub.api.security.export_service import export_user_data, delete_user_data
from automation_hub.api.security.device_identity import (
    verify_device_identity,
    list_user_devices,
    revoke_device_tokens,
    get_device_info,
)

router = APIRouter(prefix="/security", tags=["security"])


# ==================== Data Export / Delete (Layer 3) ====================


@router.get("/data/export")
def export_data(token=Depends(require_scope("user:admin"))):
    """Export all user data.

    Returns a complete export of all personal data for the authenticated user.
    Requires user:admin scope.
    """
    user_id = token.get("user_id")
    result = export_user_data(user_id)
    return {
        "export_id": result["export_id"],
        "exported_at": result["exported_at"],
        "resource_counts": result["resource_counts"],
        "data": result["data"],
    }


@router.delete("/data")
def delete_data(
    resource_types: str | None = None,
    token=Depends(require_scope("user:admin")),
):
    """Delete user data.

    Args:
        resource_types: Comma-separated list of resource types to delete
                       (notes,tasks,translations,vocabulary,news).
                       If omitted, all user data is deleted.

    Requires user:admin scope.
    """
    user_id = token.get("user_id")
    types_list = None
    if resource_types:
        types_list = [t.strip() for t in resource_types.split(",") if t.strip()]
    result = delete_user_data(user_id, resource_types=types_list)
    return result


# ==================== Device Management (Layer 4) ====================


@router.get("/devices")
def list_devices(token=Depends(require_scope("device:manage"))):
    """List all devices registered to the current user.

    Requires device:manage scope.
    """
    user_id = token.get("user_id")
    devices = list_user_devices(user_id)
    return {"devices": devices, "count": len(devices)}


@router.get("/devices/{device_id}")
def get_device(device_id: str, token=Depends(require_scope("device:manage"))):
    """Get device details and verify it belongs to the current user.

    Requires device:manage scope.
    """
    user_id = token.get("user_id")
    if not verify_device_identity(device_id, user_id):
        raise HTTPException(status_code=404, detail="Device not found")
    info = get_device_info(device_id)
    if not info:
        raise HTTPException(status_code=404, detail="Device not found")
    return info


@router.post("/devices/{device_id}/revoke")
def revoke_device(device_id: str, token=Depends(require_scope("device:manage"))):
    """Revoke all tokens for a device.

    This effectively de-authorizes the device. All associated tokens
    are revoked and the device can no longer access the API.

    Requires device:manage scope.
    """
    user_id = token.get("user_id")
    if not verify_device_identity(device_id, user_id):
        raise HTTPException(status_code=404, detail="Device not found")

    count = revoke_device_tokens(device_id, user_id)
    return {"revoked": True, "device_id": device_id, "tokens_revoked": count}
