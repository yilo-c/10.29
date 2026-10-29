# -*- coding: utf-8 -*-
"""SQLAlchemy 2.0 ORM 基础层 - 基类定义"""

from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy import DateTime, func
from datetime import datetime


class Base(DeclarativeBase):
    """SQLAlchemy 2.0 声明式基类"""
    pass


class TimestampMixin:
    """时间戳混入类，提供 created_at 和 updated_at 字段"""
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now(), default=datetime.utcnow)