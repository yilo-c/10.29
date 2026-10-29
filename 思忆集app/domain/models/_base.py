"""共享领域基类 — 所有 sync 实体继承此 mixin。"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, Field


def _now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def _default_device_id() -> str:
    """调用方应在应用启动时覆盖此默认值。"""
    return "desktop-unknown"


SyncState = Literal["local_only", "dirty", "synced"]


class SyncMetadataMixin(BaseModel):
    """同步元数据字段 — 被所有 sync 实体混入。"""

    remote_id: str | None = None
    version: int = 1
    device_id: str = Field(default_factory=_default_device_id)
    sync_state: SyncState = "local_only"
    last_synced_at: str | None = None
    created_at: str = Field(default_factory=_now_utc)
    updated_at: str = Field(default_factory=_now_utc)
    deleted_at: str | None = None
