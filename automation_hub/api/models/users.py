# -*- coding: utf-8 -*-
"""SQLAlchemy 2.0 ORM 模型 - 用户与设备"""

from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy import String, Text

from .base import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[str] = mapped_column(String, nullable=False)


class Device(Base):
    __tablename__ = "devices"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    user_id: Mapped[str] = mapped_column(String, nullable=False)
    name: Mapped[str] = mapped_column(String, nullable=False)
    platform: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[str] = mapped_column(String, nullable=False)
    last_seen_at: Mapped[str] = mapped_column(Text, nullable=True)
