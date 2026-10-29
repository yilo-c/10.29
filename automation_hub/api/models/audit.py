# -*- coding: utf-8 -*-
"""SQLAlchemy ORM 模型 - 审计事件"""

from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy import String, Text

from .base import Base


class AuditEvent(Base):
    __tablename__ = "audit_events"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    actor_user_id: Mapped[str] = mapped_column(Text, nullable=True)
    actor_device_id: Mapped[str] = mapped_column(Text, nullable=True)
    event_type: Mapped[str] = mapped_column(String, nullable=False)
    resource_type: Mapped[str] = mapped_column(Text, nullable=True)
    resource_id: Mapped[str] = mapped_column(Text, nullable=True)
    action: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=True)
    meta_json: Mapped[str] = mapped_column(String, nullable=False, default="{}")
    created_at: Mapped[str] = mapped_column(String, nullable=False)
