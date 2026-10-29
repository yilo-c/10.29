"""执行回执领域模型。

对应 assistant/action_broker.py 中 ExecutionReceipt 的 Pydantic 化版本，
记录每次能力执行的输入、输出、状态和审计信息。
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field


def _now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


class ExecutionReceipt(BaseModel):
    """执行回执 — 一次能力执行的标准记录。

    每次执行（包括失败、拒绝、超时、用户取消）都必须生成回执。
    用于审计、调试和回滚决策。
    """

    receipt_id: str = ""
    capability_id: str = ""
    invocation_id: str = ""
    status: str = "completed"  # completed | failed | blocked | cancelled | timeout
    input: dict[str, Any] = Field(default_factory=dict)
    output: dict[str, Any] = Field(default_factory=dict)
    risk_level: str = "read"
    confirmation: str = "auto"
    error: str = ""
    blocked_reason: str = ""
    rollback_policy: str = ""
    rollback_info: dict[str, Any] = Field(default_factory=dict)
    audit_level: str = ""
    privacy_egress: str = ""
    source: str = ""
    started_at: str = Field(default_factory=_now_utc)
    completed_at: str = Field(default_factory=_now_utc)

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump()
