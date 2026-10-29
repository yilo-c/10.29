# -*- coding: utf-8 -*-
"""SQLAlchemy ORM 模型 - 运行时表（事件 / 产物 / 版本 / 会话）"""

from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy import String, Text, Integer, REAL, LargeBinary

from .base import Base


class RunEvent(Base):
    __tablename__ = "run_events"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    run_id: Mapped[str] = mapped_column(String, nullable=False)
    event_type: Mapped[str] = mapped_column(String, nullable=False)
    data: Mapped[str] = mapped_column(Text, nullable=True)
    created_at: Mapped[str] = mapped_column(String, nullable=False)


class RunArtifact(Base):
    __tablename__ = "run_artifacts"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    run_id: Mapped[str] = mapped_column(String, nullable=False)
    filename: Mapped[str] = mapped_column(String, nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    content_type: Mapped[str] = mapped_column(Text, nullable=True)
    data: Mapped[bytes] = mapped_column(LargeBinary, nullable=True)
    created_at: Mapped[str] = mapped_column(String, nullable=False)


class ToolVersion(Base):
    __tablename__ = "tool_versions"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    tool_id: Mapped[str] = mapped_column(String, nullable=False)
    version: Mapped[str] = mapped_column(String, nullable=False)
    spec_json: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[str] = mapped_column(String, nullable=False)


class AgentNodeSessionEvent(Base):
    __tablename__ = "agent_node_session_events"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    node_id: Mapped[str] = mapped_column(String, nullable=False)
    session_id: Mapped[str] = mapped_column(String, nullable=False)
    event_type: Mapped[str] = mapped_column(String, nullable=False)
    data_json: Mapped[str] = mapped_column(String, nullable=False, default="{}")
    created_at: Mapped[str] = mapped_column(String, nullable=False)
