"""Judge 路由决策数据模型与成本常量。

v2 架构：
- DeepSeek（云端）做决策和执行大脑
- 本地 qwen（Ollama）做手脚：简单任务 + 隐私任务
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any


def extract_json_block(text: str) -> str | None:
    """从 LLM 响应中提取 JSON 块（处理 markdown 代码包裹）。"""
    match = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", text, re.DOTALL)
    if match:
        return match.group(1).strip()
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match:
        return match.group(0).strip()
    return None


# ── 执行器常量（定义在 ai/routing_constants.py，此处 re-export 保持向后兼容）──
from ai.routing_constants import (  # noqa: F401 — re-export for backward compat
    ROUTE_EXECUTOR_DEEPSEEK,
    ROUTE_EXECUTOR_LOCAL,
    ROUTE_EXECUTOR_MCP,
    ROUTE_EXECUTOR_KIRO,
)

JUDGE_DEFAULT_MODEL = "deepseek-chat"
JUDGE_PREFERRED_MODEL = "deepseek-v4-pro"
JUDGE_FALLBACK_MODEL = "claude-sonnet-4-6"
JUDGE_ALT_MODEL = "gpt-4.1"

JUDGE_SYSTEM_PROMPT = (
    "你是思忆集的路由器 (Judge)。你的任务：判断用户请求该由谁执行。\n"
    "你只输出 JSON 路由决策，不回答问题。\n\n"
    "输出 JSON 格式：\n"
    '{"route": "local|deepseek|mcp",\n'
    ' "reason": "简短原因",\n'
    ' "privacy": true|false,\n'
    ' "confidence": 0.0-1.0}\n\n'
    "路由规则：\n"
    '1. "local" — 本地 qwen 执行，适合：\n'
    "   - 简单问候、闲聊、基本问答\n"
    "   - 文本分类、格式化、简单摘要\n"
    "   - **涉及隐私的内容**（照片、个人文件、通讯录、本地数据等）\n"
    "   - 不需要联网、不需要深度推理的任务\n"
    '2. "deepseek" — DeepSeek 执行，适合：\n'
    "   - 复杂推理、分析、决策\n"
    "   - 写代码、Debug、代码审查\n"
    "   - 联网搜索、研究\n"
    "   - 多步规划、编排\n"
    "   - 翻译（高质量的）\n"
    '3. "mcp" — MCP 工具执行，适合：\n'
    "   - 有明确 MCP 工具匹配的操作"
)

# 隐私敏感关键词 — 规则 fallback 用（必须与 lower() 后的文本匹配，因此全部小写）
PRIVACY_KEYWORDS: tuple[str, ...] = (
    "照片", "图片", "相册", "截图", "拍照",
    "通讯录", "联系人", "短信", "通话记录",
    "私密", "私人", "个人文件", "我的文档",
    "我的照片", "家庭照片",
    "密码", "账号", "私钥",
)

# 简单任务关键词 — 规则 fallback 用
SIMPLE_TASK_KEYWORDS: tuple[str, ...] = (
    "你好", "hi", "hello", "嗨", "早上好", "下午好", "晚上好",
    "谢谢", "thanks", "再见", "拜拜", "bye",
    "你是谁", "你叫什么", "嗯", "哦", "好的", "ok",
)

# 成本常量（保留向后兼容）
MODEL_COST_PER_1K = {
    "deepseek-chat": 0.00015,
    "deepseek-v4-pro": 0.0005,
    "claude-sonnet-4-6": 0.003,
    "gpt-4.1": 0.002,
}

MODEL_RECOMMENDATIONS: dict[str, list[str]] = {
    "code": ["deepseek-v4-pro", "claude-sonnet-4-6"],
    "analysis": ["deepseek-v4-pro", "gpt-4.1"],
    "decision": ["deepseek-v4-pro"],
    "translation": ["deepseek-chat", "deepseek-v4-pro"],
    "creative": ["deepseek-v4-pro", "claude-sonnet-4-6"],
    "research": ["deepseek-v4-pro", "gpt-4.1"],
}

COST_ESTIMATES = {
    "moderate": 500,
    "complex": 1500,
    "very_complex": 3000,
}


@dataclass
class JudgeDecision:
    """Judge 路由决策结果。

    v2 核心字段：
    - executor: "local" | "deepseek" | "mcp" — 谁执行
    - privacy: True 表示涉及隐私，必须本地执行
    """

    executor: str = ROUTE_EXECUTOR_DEEPSEEK
    reason: str = ""
    privacy: bool = False
    confidence: float = 0.0

    # 保留字段（向后兼容）
    task_type: str = "analysis"
    complexity: str = "moderate"
    required_capabilities: list[str] = field(default_factory=list)
    route_to: dict[str, str] = field(default_factory=dict)
    estimated_tokens: int = 0
    estimated_cost_usd: float = 0.0
    reasoning: str = ""
    mcp_tool: dict[str, Any] | None = None
    intent_depth: str = "L2"

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "executor": self.executor,
            "reason": self.reason,
            "privacy": self.privacy,
            "confidence": self.confidence,
            "task_type": self.task_type,
            "complexity": self.complexity,
            "route_to": dict(self.route_to) if self.route_to else {"executor": self.executor},
            "reasoning": self.reasoning or self.reason,
            "intent_depth": self.intent_depth,
        }
        if self.mcp_tool is not None:
            result["mcp_tool"] = dict(self.mcp_tool)
        return result
