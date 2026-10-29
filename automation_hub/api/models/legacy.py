# -*- coding: utf-8 -*-
"""SQLAlchemy ORM 模型 - 旧版 tables（scripts / tasks / runs）

这些表目前由 raw SQL CRUD 函数驱动（api/db/legacy.py），
ORM 模型仅用于 Base.metadata.create_all() 建表。
"""

from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy import String, Text, Integer

from .base import Base


class Script(Base):
    __tablename__ = "scripts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=True)
    created_at: Mapped[str] = mapped_column(Text, nullable=True)


class Task(Base):
    __tablename__ = "tasks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    script_name: Mapped[str] = mapped_column(String, nullable=False)
    parameters: Mapped[str] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String, nullable=False, default="pending")
    created_at: Mapped[str] = mapped_column(Text, nullable=True)
    scheduled_time: Mapped[str] = mapped_column(Text, nullable=True)
    completed_at: Mapped[str] = mapped_column(Text, nullable=True)


class Run(Base):
    __tablename__ = "runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    script_name: Mapped[str] = mapped_column(String, nullable=False)
    parameters: Mapped[str] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String, nullable=False, default="queued")
    created_at: Mapped[str] = mapped_column(Text, nullable=True)
    started_at: Mapped[str] = mapped_column(Text, nullable=True)
    completed_at: Mapped[str] = mapped_column(Text, nullable=True)
    log_file_path: Mapped[str] = mapped_column(Text, nullable=True)
    result: Mapped[str] = mapped_column(Text, nullable=True)
    error_msg: Mapped[str] = mapped_column(Text, nullable=True)


class RunLog(Base):
    __tablename__ = "run_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(String, nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[str] = mapped_column(Text, nullable=True)
