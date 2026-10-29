"""仓库索引数据模型。"""

from __future__ import annotations
from pydantic import BaseModel, Field
from typing import Optional, List


class RepoCreate(BaseModel):
    name: str = Field(min_length=1)
    root_path: str = Field(min_length=1)


class RepoResponse(BaseModel):
    id: str
    name: str
    root_path: str
    created_at: str
    last_indexed_at: Optional[str] = None


class RepoSearchMatch(BaseModel):
    path: str
    line: int
    text: str


class RepoSearchResponse(BaseModel):
    query: str
    engine: str
    total: int
    limit: int
    offset: int
    matches: List[str]
    items: List[RepoSearchMatch]
