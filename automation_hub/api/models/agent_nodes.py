# -*- coding: utf-8 -*-
"""SQLAlchemy 2.0 ORM 模型 - Agent 节点与运行产物"""

from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy import String, Text, Integer, REAL

from .base import Base


class AgentNode(Base):
    __tablename__ = "agent_nodes"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    hostname: Mapped[str] = mapped_column(String, nullable=False)
    labels_json: Mapped[str] = mapped_column(String, nullable=False, default="{}")
    capabilities_json: Mapped[str] = mapped_column(String, nullable=False, default="[]")
    issued_token_hash: Mapped[str] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String, nullable=False, default="offline")
    policy_version: Mapped[str] = mapped_column(String, nullable=False, default="v0")
    cpu_percent: Mapped[float] = mapped_column(REAL, default=0.0)
    mem_percent: Mapped[float] = mapped_column(REAL, default=0.0)
    active_runs: Mapped[int] = mapped_column(Integer, default=0)
    drain_reason: Mapped[str] = mapped_column(Text, nullable=True)
    revoke_reason: Mapped[str] = mapped_column(Text, nullable=True)
    registered_at: Mapped[str] = mapped_column(String, nullable=False)
    last_heartbeat_at: Mapped[str] = mapped_column(Text, nullable=True)
    updated_at: Mapped[str] = mapped_column(Text, nullable=True)
