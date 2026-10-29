"""提案数据模型。"""

from __future__ import annotations
from pydantic import BaseModel, Field
from typing import List, Optional, Dict, Any


class ProposalRepoContextRequest(BaseModel):
    repo_id: str = Field(min_length=1)
    query: str = Field(min_length=1, max_length=500)
    limit: int = Field(default=10, ge=1, le=50)
    offset: int = Field(default=0, ge=0)


class ProposalRepoContextSnapshot(BaseModel):
    repo_id: str
    repo_name: Optional[str] = None
    query: str
    engine: str
    total: int
    injected_files: List[str] = Field(default_factory=list)
    items: List[Dict[str, Any]] = Field(default_factory=list)


class VerifyToolRef(BaseModel):
    tool_id: str = Field(min_length=1)
    args: Dict[str, Any] = Field(default_factory=dict)


class ProposalCreate(BaseModel):
    title: str = Field(min_length=1)
    summary: str = Field(min_length=1)
    plan_md: str = Field(min_length=1)
    patch_diff: Optional[str] = None
    # P0: 只允许 tool_id 白名单 verify（不允许 shell 字符串命令）
    verify_tools: List[VerifyToolRef] = Field(default_factory=list)
    # 兼容字段：仅用于展示旧数据（不会被 apply 执行）
    verify_commands: List[str] = Field(default_factory=list)
    risk_level: str = Field(default="write")
    repo_context: Optional[ProposalRepoContextRequest] = None


class ProposalResponse(BaseModel):
    id: str
    title: str
    summary: str
    plan_md: str
    patch_diff: Optional[str] = None
    verify_tools: List[VerifyToolRef] = Field(default_factory=list)
    verify_commands: List[str] = Field(default_factory=list)
    risk_level: str
    status: str
    created_at: str
    updated_at: str
    applied_at: Optional[str] = None
    repo_context: Optional[ProposalRepoContextSnapshot] = None
