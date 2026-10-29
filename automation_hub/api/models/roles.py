# -*- coding: utf-8 -*-
"""SQLAlchemy ORM model - User roles."""

from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy import String, Text

from .base import Base


class UserRole(Base):
    __tablename__ = "user_roles"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    user_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    role: Mapped[str] = mapped_column(String, nullable=False)  # admin, agent, cronjob, readonly
    assigned_by: Mapped[str] = mapped_column(Text, nullable=True)
    created_at: Mapped[str] = mapped_column(String, nullable=False)
