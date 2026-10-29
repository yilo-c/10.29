# -*- coding: utf-8 -*-
"""SQLAlchemy ORM 模型 - RSS / 日报表（scripts 目录引用，原仅定义于 db/schema.sql）"""

from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy import String, Text, Integer

from .base import Base


class RssSource(Base):
    __tablename__ = "rss_sources"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    url: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=True)
    tags: Mapped[str] = mapped_column(Text, nullable=True)
    is_enabled: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[str] = mapped_column(Text, nullable=False)


class RssItem(Base):
    __tablename__ = "rss_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    canonical_id: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    source_id: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=True)
    link: Mapped[str] = mapped_column(Text, nullable=True)
    published_at: Mapped[str] = mapped_column(Text, nullable=True)
    summary: Mapped[str] = mapped_column(Text, nullable=True)
    raw_json: Mapped[str] = mapped_column(Text, nullable=True)
    fetched_at: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[str] = mapped_column(Text, nullable=False)


class DailyBrief(Base):
    __tablename__ = "daily_briefs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    brief_date: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    stats_json: Mapped[str] = mapped_column(Text, nullable=False)
    md_path: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[str] = mapped_column(Text, nullable=False)
    source_counts_json: Mapped[str] = mapped_column(Text, nullable=False)
