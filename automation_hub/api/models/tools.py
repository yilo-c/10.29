# -*- coding: utf-8 -*-
"""SQLAlchemy 2.0 ORM 模型 - 工具相关

tools / tool_runs 定义必须与 schema.sql 保持一致，因为所有路由
（api/tools/registry.py、api/routes/tool_runs.py）都通过 raw sqlite3 操作这些表。
"""

from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy import String, Text, Integer

from .base import Base


class Tool(Base):
    __tablename__ = "tools"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    risk_level: Mapped[str] = mapped_column(Text, nullable=False)
    executor: Mapped[str] = mapped_column(Text, nullable=False)
    args_schema_json: Mapped[str] = mapped_column(Text, nullable=False)
    command_json: Mapped[str] = mapped_column(Text, nullable=False)
    cwd: Mapped[str] = mapped_column(Text, nullable=True)
    timeout_sec: Mapped[int] = mapped_column(Integer, nullable=False, default=120)
    allowed_paths_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    created_at: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[str] = mapped_column(Text, nullable=False)
    is_enabled: Mapped[int] = mapped_column(Integer, nullable=False, default=1)


class ToolRun(Base):
    __tablename__ = "tool_runs"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    tool_id: Mapped[str] = mapped_column(Text, nullable=False)
    tool_name: Mapped[str] = mapped_column(Text, nullable=True)
    args_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    status: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[str] = mapped_column(Text, nullable=False)
    started_at: Mapped[str] = mapped_column(Text, nullable=True)
    finished_at: Mapped[str] = mapped_column(Text, nullable=True)
    completed_at: Mapped[str] = mapped_column(Text, nullable=True)
    exit_code: Mapped[int] = mapped_column(Integer, nullable=True)
    result_json: Mapped[str] = mapped_column(Text, nullable=True)
    error_msg: Mapped[str] = mapped_column(Text, nullable=True)
    stdout_path: Mapped[str] = mapped_column(Text, nullable=True)
    stderr_path: Mapped[str] = mapped_column(Text, nullable=True)
    created_by_user_id: Mapped[str] = mapped_column(Text, nullable=True)
    approval_request_id: Mapped[str] = mapped_column(Text, nullable=True)
    claimed_by: Mapped[str] = mapped_column(Text, nullable=True)
    updated_at: Mapped[str] = mapped_column(Text, nullable=True)


class ToolApprovalRule(Base):
    __tablename__ = "tool_approval_rules"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tool_name: Mapped[str] = mapped_column(String, nullable=False)
    risk_level: Mapped[str] = mapped_column(String, nullable=False)
    requires_approval: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    auto_approve_low_risk: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    approval_timeout_seconds: Mapped[int] = mapped_column(Integer, default=3600, nullable=False)
    created_at: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[str] = mapped_column(Text, nullable=False)


class ToolExecutionAuthorization(Base):
    __tablename__ = "tool_execution_authorizations"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    tool_run_id: Mapped[str] = mapped_column(String, nullable=False)
    decision: Mapped[str] = mapped_column(String, nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=True)
    decided_by_user_id: Mapped[str] = mapped_column(String, nullable=True)
    decided_at: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[str] = mapped_column(Text, nullable=False)


class ToolCategory(Base):
    __tablename__ = "tool_categories"

    name: Mapped[str] = mapped_column(String, primary_key=True)
    display_name: Mapped[str] = mapped_column(String, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
