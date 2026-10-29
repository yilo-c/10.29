# -*- coding: utf-8 -*-
"""SQLAlchemy ORM 模型 - API Token"""

from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy import String, Text

from .base import Base


class ApiToken(Base):
    __tablename__ = "api_tokens"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    user_id: Mapped[str] = mapped_column(String, nullable=False)
    device_id: Mapped[str] = mapped_column(Text, nullable=True)
    token_hash: Mapped[str] = mapped_column(String, nullable=False)
    scopes: Mapped[str] = mapped_column(String, nullable=False, default="[]")
    expires_at: Mapped[str] = mapped_column(Text, nullable=True)
    created_at: Mapped[str] = mapped_column(String, nullable=False)
    revoked_at: Mapped[str] = mapped_column(Text, nullable=True)
