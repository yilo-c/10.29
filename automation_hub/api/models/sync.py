"""Pydantic + SQLAlchemy ORM models for cross-device sync API.

Pydantic models are used in route signatures (request / response shapes),
ORM models persist the same data into SQLite via the standard
``automation_hub.api.db`` session layer.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy import Boolean, DateTime, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.sqlite import JSON
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, TimestampMixin


class SyncEnvelopeModel(BaseModel):
    """SyncEnvelope as received via POST /sync/push."""
    envelope_id: str
    device_id: str
    resource_type: str
    resource_id: str
    action: str  # create | update | delete | undelete
    payload: dict[str, Any] | None = None
    version: int = 0
    cursor: int = 0
    created_at: str = ""
    checksum: str = ""


class SyncPushRequest(BaseModel):
    envelopes: list[SyncEnvelopeModel]


class SyncPushResponse(BaseModel):
    accepted: int = 0
    rejected: int = 0
    rejected_details: list[dict[str, Any]] = Field(default_factory=list)


class ConflictSummary(BaseModel):
    conflict_id: str
    entity_type: str
    entity_id: str
    reason: str
    resolution: str
    generated_at: str


class SyncPullResponse(BaseModel):
    envelopes: list[dict[str, Any]] = Field(default_factory=list)
    latest_cursor: int = 0
    has_more: bool = False
    conflict_summaries: list[dict[str, Any]] = Field(default_factory=list)


class QuickRelayRequest(BaseModel):
    device_id: str
    relay_type: str
    payload: dict[str, Any]
    captured_at: str = ""


class AckWriteRequest(BaseModel):
    entity_type: str
    entity_id: str
    ack_type: str  # read | dismissed | confirmed
    acked_at: str = ""


class DeviceHeartbeatRequest(BaseModel):
    device_id: str
    status: str = "online"  # online | offline | away
    last_seen_at: str = ""
    extra: dict[str, Any] = Field(default_factory=dict)


# ── ORM models (P4.2-P0a) ────────────────────────────────────────────


class SyncEnvelopeRecord(Base, TimestampMixin):
    """Sync envelope persisted by /sync/push and /sync/quick-relay.

    Replaces the in-memory ``_envelope_store`` list. Each envelope is
    addressable by ``envelope_id`` (idempotency key) and ordered by
    ``cursor`` for incremental pull.
    """

    __tablename__ = "sync_envelopes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    envelope_id: Mapped[str] = mapped_column(String, nullable=False)
    device_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    resource_type: Mapped[str] = mapped_column(String, nullable=False, index=True)
    resource_id: Mapped[str] = mapped_column(String, nullable=False)
    action: Mapped[str] = mapped_column(String, nullable=False)
    payload_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cursor: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    envelope_created_at: Mapped[str] = mapped_column(String, nullable=False, default="")
    checksum: Mapped[str] = mapped_column(String, nullable=False, default="")
    received_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)

    __table_args__ = (
        UniqueConstraint("envelope_id", name="uq_sync_envelopes_envelope_id"),
        Index("ix_sync_envelopes_cursor_resource", "cursor", "resource_type"),
    )


class SyncDevice(Base, TimestampMixin):
    """Paired/known device record. Replaces in-memory ``_device_store``."""

    __tablename__ = "sync_devices"

    device_id: Mapped[str] = mapped_column(String, primary_key=True)
    friendly_name: Mapped[str] = mapped_column(String, nullable=False, default="")
    device_type: Mapped[str] = mapped_column(String, nullable=False, default="unknown")
    status: Mapped[str] = mapped_column(String, nullable=False, default="online")
    last_seen_at: Mapped[str] = mapped_column(String, nullable=False, default="")
    extra_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class SyncCursor(Base, TimestampMixin):
    """Per-resource-type cursor watermark used by sync push sequencing
    and concurrent push protection.

    Composite primary key on (device_id, resource_type). The reserved
    sentinel device_id ``__global__`` tracks the global monotonic cursor
    used by ``/sync/push`` to assign envelope ordering.
    """

    __tablename__ = "sync_cursors"

    device_id: Mapped[str] = mapped_column(String, primary_key=True)
    resource_type: Mapped[str] = mapped_column(String, primary_key=True)
    max_cursor: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class SyncConflict(Base, TimestampMixin):
    """Conflict record surfaced to a device on next pull."""

    __tablename__ = "sync_conflicts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    conflict_id: Mapped[str] = mapped_column(String, nullable=False)
    device_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    entity_type: Mapped[str] = mapped_column(String, nullable=False)
    entity_id: Mapped[str] = mapped_column(String, nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False, default="")
    resolution: Mapped[str] = mapped_column(String, nullable=False, default="")
    local_version: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    remote_version: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    local_payload: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    remote_payload: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    status: Mapped[str] = mapped_column(String, nullable=False, default="open")
    generated_at: Mapped[str] = mapped_column(String, nullable=False, default="")

    __table_args__ = (
        UniqueConstraint("conflict_id", name="uq_sync_conflicts_conflict_id"),
    )
