"""Mobile companion API pydantic models."""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class PairingInitiateResponse(BaseModel):
    setup_token: str
    pairing_url: str
    pin_code: str = ""
    expires_in_seconds: int = 300


class PairingCompleteRequest(BaseModel):
    setup_token: str = ""
    pin_code: str = ""
    device_name: str = "Android Phone"
    platform: str = "android"


class PairingCompleteResponse(BaseModel):
    api_token: str
    device_id: str
    user_id: str
    scopes: list[str]


class CaptureItem(BaseModel):
    capture_type: str  # text | voice | photo
    content: str = ""
    media_url: str | None = None
    source: str = "phone"
    captured_at: str = ""


class DashboardResponse(BaseModel):
    recent_notes: list[dict[str, Any]] = Field(default_factory=list)
    active_tasks: list[dict[str, Any]] = Field(default_factory=list)
    sync_status: dict[str, Any] = Field(default_factory=dict)
    pending_count: int = 0


class MobilePairingStatus(BaseModel):
    paired_devices: list[dict[str, Any]] = Field(default_factory=list)
    is_paired: bool = False
