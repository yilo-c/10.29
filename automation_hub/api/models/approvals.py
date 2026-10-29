# -*- coding: utf-8 -*-
"""SQLAlchemy 2.0 ORM 模型 - 审批相关"""

from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy import String, Text, DateTime, ForeignKey
from sqlalchemy.dialects.sqlite import JSON

from .base import Base, TimestampMixin


class ApprovalRequest(Base, TimestampMixin):
    """审批请求模型"""
    __tablename__ = "approval_requests"
    
    id: Mapped[str] = mapped_column(String, primary_key=True)
    created_by_user_id: Mapped[str] = mapped_column(String, ForeignKey("users.id"), nullable=False)
    created_by_device_id: Mapped[str] = mapped_column(String, ForeignKey("devices.id"), nullable=True)
    resource_type: Mapped[str] = mapped_column(String, nullable=False)
    resource_id: Mapped[str] = mapped_column(String, nullable=False)
    action: Mapped[str] = mapped_column(String, nullable=False)
    risk_level: Mapped[str] = mapped_column(String, nullable=False)
    request_reason: Mapped[str] = mapped_column(Text, nullable=False)
    payload_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False)
    decided_at: Mapped[DateTime] = mapped_column(DateTime, nullable=True)
    decided_by_user_id: Mapped[str] = mapped_column(String, ForeignKey("users.id"), nullable=True)
    decision_note: Mapped[str] = mapped_column(Text, nullable=True)


class ExecutionAuthorization(Base, TimestampMixin):
    """执行授权模型"""
    __tablename__ = "execution_authorizations"
    
    id: Mapped[str] = mapped_column(String, primary_key=True)
    node_id: Mapped[str] = mapped_column(String, ForeignKey("agent_nodes.id"), nullable=False)
    tool_name: Mapped[str] = mapped_column(String, nullable=False)
    args_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    risk_level: Mapped[str] = mapped_column(String, nullable=False, default="low")
    decision: Mapped[str] = mapped_column(String, nullable=False, default="pending")
    reason: Mapped[str] = mapped_column(Text, nullable=True)
    decided_at: Mapped[DateTime] = mapped_column(DateTime, nullable=True)
    decided_by_user_id: Mapped[str] = mapped_column(String, ForeignKey("users.id"), nullable=True)