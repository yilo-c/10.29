# -*- coding: utf-8 -*-
"""SQLAlchemy ORM 模型 - 提案 / 仓库 / 仓库文件"""

from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy import String, Text, Integer

from .base import Base


class Proposal(Base):
    __tablename__ = "proposals"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    title: Mapped[str] = mapped_column(String, nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    plan_md: Mapped[str] = mapped_column(Text, nullable=False)
    patch_diff: Mapped[str] = mapped_column(Text, nullable=True)
    verify_commands_json: Mapped[str] = mapped_column(String, nullable=False, default="[]")
    risk_level: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False)
    target_repo_id: Mapped[str] = mapped_column(Text, nullable=True)
    created_by_user_id: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[str] = mapped_column(String, nullable=False)
    updated_at: Mapped[str] = mapped_column(String, nullable=False)
    applied_at: Mapped[str] = mapped_column(Text, nullable=True)


class Repo(Base):
    __tablename__ = "repos"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    root_path: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[str] = mapped_column(String, nullable=False)
    last_indexed_at: Mapped[str] = mapped_column(Text, nullable=True)


class RepoFile(Base):
    __tablename__ = "repo_files"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    repo_id: Mapped[str] = mapped_column(String, nullable=False)
    path: Mapped[str] = mapped_column(String, nullable=False)
    mtime: Mapped[int] = mapped_column(Integer, nullable=False)
    size: Mapped[int] = mapped_column(Integer, nullable=False)
    sha256: Mapped[str] = mapped_column(String, nullable=False)
