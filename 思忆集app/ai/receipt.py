"""ModelInvocationReceipt — 模型调用回执。

记录每次模型调用的完整元数据：模型、成本、隐私等级、token 用量、时间戳。
与 CostTracker 配合，为可观测性和审计提供结构化数据。
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


@dataclass
class ModelInvocationReceipt:
    """model_invocation_receipt — 一次模型调用的标准回执。

    记录输入上下文、调用链路、成本、隐私等级、执行结果。
    每次模型调用（包括失败、降级、缓存命中）都必须生成回执。
    """

    receipt_id: str
    model: str
    provider: str                        # primary / fallback / local / cache
    tier: str                            # local / judge / mid_cloud / high_cloud
    task_type: str                       # chat_completion / route_chat / judge / structured
    status: str                          # completed / degraded / failed / cache_hit / blocked

    # ── 成本与用量 ──
    tokens_input: int = 0
    tokens_output: int = 0
    cost_estimate: float = 0.0

    # ── 安全与隐私 ──
    privacy_level: str = ""              # local_only / shareable / model_with_consent
    privacy_overridden: bool = False     # PrivacyClassifier 是否覆盖了路由

    # ── 调用上下文 ──
    source: str = "assistant"            # assistant / judge / plugin / user
    action_id: str = ""                  # 关联的 capability action_id（若有）
    messages_summary: str = ""           # 输入消息摘要（前 200 字符）
    error: str = ""

    # ── 时间戳 ──
    started_at: str = ""
    completed_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "receipt_id": self.receipt_id,
            "model": self.model,
            "provider": self.provider,
            "tier": self.tier,
            "task_type": self.task_type,
            "status": self.status,
            "tokens_input": self.tokens_input,
            "tokens_output": self.tokens_output,
            "cost_estimate": self.cost_estimate,
            "privacy_level": self.privacy_level,
            "privacy_overridden": self.privacy_overridden,
            "source": self.source,
            "action_id": self.action_id,
            "messages_summary": self.messages_summary,
            "error": self.error,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
        }


def make_receipt(
    model: str,
    provider: str,
    tier: str,
    task_type: str = "chat_completion",
    status: str = "completed",
    tokens_input: int = 0,
    tokens_output: int = 0,
    cost_estimate: float = 0.0,
    privacy_level: str = "",
    privacy_overridden: bool = False,
    source: str = "assistant",
    action_id: str = "",
    messages: list[dict[str, str]] | None = None,
    error: str = "",
) -> ModelInvocationReceipt:
    now = datetime.now(timezone.utc).isoformat()
    summary = ""
    if messages:
        texts = [m.get("content", "") for m in messages if isinstance(m.get("content"), str)]
        full = " ".join(texts)
        summary = full[:200]

    return ModelInvocationReceipt(
        receipt_id=uuid.uuid4().hex[:12],
        model=model,
        provider=provider,
        tier=tier,
        task_type=task_type,
        status=status,
        tokens_input=tokens_input,
        tokens_output=tokens_output,
        cost_estimate=cost_estimate,
        privacy_level=privacy_level,
        privacy_overridden=privacy_overridden,
        source=source,
        action_id=action_id,
        messages_summary=summary,
        error=error,
        started_at=now,
        completed_at=now,
    )
