"""Chain node definitions, constants, and helpers.

Extracted from agent_chain.py — constants, data models, JSON extraction helper.
"""
from __future__ import annotations

import re
from typing import Any

# ── 常量 ──

MAX_RETRIES_DEFAULT = 2
MAX_REFLECTION_LOG = 100
# P1-3: 单链最大重规划次数（超过即终止，避免无限循环）
MAX_REPLAN_ATTEMPTS = 2
TIMEOUT_CONFIRMATION_LOW = 30
TIMEOUT_CONFIRMATION_MEDIUM = 60
TIMEOUT_CONFIRMATION_HIGH = 120

DELEGATE_LOCAL = "local"
DELEGATE_CLOUD = "cloud"
DELEGATE_MCP = "mcp"
DELEGATE_AUTOMATION = "automation"

NODE_STATUS_PENDING = "pending"
NODE_STATUS_RUNNING = "running"
NODE_STATUS_COMPLETED = "completed"
NODE_STATUS_FAILED = "failed"
NODE_STATUS_SKIPPED = "skipped"

CHAIN_STATUS_PENDING = "pending"
CHAIN_STATUS_RUNNING = "running"
CHAIN_STATUS_PAUSED = "paused"
CHAIN_STATUS_COMPLETED = "completed"
CHAIN_STATUS_FAILED = "failed"
CHAIN_STATUS_CANCELLED = "cancelled"


def extract_json_block(text: str) -> str | None:
    """从 LLM 响应中提取 JSON 块（处理 markdown 代码包裹）。"""
    match = re.search(r'```(?:json)?\s*\n?(.*?)\n?```', text, re.DOTALL)
    if match:
        return match.group(1).strip()
    match = re.search(r'\{.*\}', text, re.DOTALL)
    if match:
        return match.group(0).strip()
    return None


class ConfirmationLevel(str):
    """Human-in-the-loop 确认级别。"""
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class ReflectionAction(str):
    """自我反思后的决策动作。"""
    CONTINUE = "continue"
    RETRY = "retry"
    ADJUST = "adjust"
    STOP = "stop"


__all__ = [
    "MAX_RETRIES_DEFAULT", "MAX_REFLECTION_LOG", "MAX_REPLAN_ATTEMPTS",
    "TIMEOUT_CONFIRMATION_LOW", "TIMEOUT_CONFIRMATION_MEDIUM", "TIMEOUT_CONFIRMATION_HIGH",
    "DELEGATE_LOCAL", "DELEGATE_CLOUD", "DELEGATE_MCP", "DELEGATE_AUTOMATION",
    "NODE_STATUS_PENDING", "NODE_STATUS_RUNNING", "NODE_STATUS_COMPLETED",
    "NODE_STATUS_FAILED", "NODE_STATUS_SKIPPED",
    "CHAIN_STATUS_PENDING", "CHAIN_STATUS_RUNNING", "CHAIN_STATUS_PAUSED",
    "CHAIN_STATUS_COMPLETED", "CHAIN_STATUS_FAILED", "CHAIN_STATUS_CANCELLED",
    "extract_json_block",
    "ConfirmationLevel",
    "ReflectionAction",
]
