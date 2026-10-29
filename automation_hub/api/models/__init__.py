# -*- coding: utf-8 -*-
"""SQLAlchemy 2.0 ORM 模型包。

所有表均有对应 ORM 模型，alembic upgrade head 即可完成全部建表。
"""

from .base import Base, TimestampMixin
from .engine import create_engine_from_db_path

# 用户与设备（被多数表 FK 引用，必须最先导入）
from .users import User, Device

# API Token（依赖 users + devices）
from .auth import ApiToken

# Agent 节点（被 ExecutionAuthorization FK 引用）
from .agent_nodes import AgentNode
from .runtime import AgentNodeSessionEvent

# 用户角色（RBAC）
from .roles import UserRole

# 旧表（scripts / tasks / runs）—— 被旧路由依赖
from .legacy import Script, Task, Run, RunLog

# 审批相关
from .approvals import ApprovalRequest, ExecutionAuthorization

# 审核
from .audit import AuditEvent

# 工具相关（定义与 schema.sql 一致）
from .tools import Tool, ToolRun, ToolApprovalRule, ToolExecutionAuthorization, ToolCategory

# 运行时
from .runtime import RunEvent, RunArtifact, ToolVersion

# 提案 / 仓库
from .repos import Proposal, Repo, RepoFile

# 个人数据
from .personal import (
    PersonalNote,
    PersonalTask,
    PersonalNewsItem,
    PersonalTranslationHistory,
    PersonalWordbook,
    PersonalWord,
)

# RSS / 日报（scripts 目录引用）
from .rss import RssSource, RssItem, DailyBrief

# 跨设备同步（P4.2-P0a）
from .sync import SyncEnvelopeRecord, SyncDevice, SyncCursor, SyncConflict
