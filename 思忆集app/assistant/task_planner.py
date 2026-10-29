"""Task planning layer — classify intent, decompose into steps."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger("siyi.assistant.task_planner")

_CHAT_ONLY_KEYWORDS: frozenset[str] = frozenset(
    {
        "晚安",
        "早上好",
        "晚上好",
        "在吗",
        "hello",
        "下午好",
        "嗯",
        "拜拜",
        "你好",
        "嗨",
        "再见",
        "hi",
        "好",
        "在不在",
    }
)


def is_chat_only(text: str) -> bool:
    """快速判断纯闲聊（短+问候），避免不必要的 LLM 分类调用。"""
    if not text or len(text) < 3:
        return True
    return text.strip().lower() in _CHAT_ONLY_KEYWORDS


_CLASSIFY_SYSTEM_PROMPT: str = (
    "你是一个任务分类器。判断用户意图是 \"chat\" 还是 \"action\"。\n"
    "- \"chat\": 问候、闲聊、观点问题、感谢、简单问答（不需要操作文件/网络/系统）\n"
    "- \"action\": 涉及文件操作、网络搜索、笔记管理、系统操作、执行代码、启动应用等\n\n"
    "只输出 JSON，不要其他内容：\n"
    '{"intent": "chat"|"action", "reason": "简要原因"}\n'
)


def classify_intent(client: Any, user_message: str) -> dict[str, str]:
    """用 LLM 分类意图，返回 {"intent": ..., "reason": ...}。"""
    try:
        resp = client.chat_completion(
            [
                {"role": "system", "content": _CLASSIFY_SYSTEM_PROMPT},
                {"role": "user", "content": user_message[:500]},
            ],
            temperature=0,
            max_tokens=200,
        )
        content = (resp.get("choices") or [{}])[0].get("message", {}).get("content", "")
        result = json.loads(content)
        if result.get("intent") in ("chat", "action"):
            return result
    except Exception as exc:
        logger.debug("LLM 分类失败: %s", exc)
    return {"intent": "action", "reason": "分类失败，默认走 action"}


@dataclass
class Step:
    """Step(description: 'str', tool_hint: 'str' = '', status: 'str' = 'pending')"""

    description: str
    tool_hint: str = ""
    status: str = "pending"

    def to_dict(self) -> dict[str, str]:
        return {
            "description": self.description,
            "tool_hint": self.tool_hint,
            "status": self.status,
        }


@dataclass
class Plan:
    """Plan(task: 'str', steps: 'list[Step]' = <factory>)"""

    task: str
    steps: list[Step] = field(default_factory=list)

    def to_dict(self) -> list[dict[str, str]]:
        return [s.to_dict() for s in self.steps]


_PLAN_SYSTEM_PROMPT: str = (
    "分析用户任务，拆解为可执行的步骤列表。\n\n"
    "根据任务的复杂性：\n"
    "- 简单任务（如\"打开微信\"）→ 1 步\n"
    "- 中等任务（如\"查看桌面文件\"）→ 2-3 步\n"
    "- 复杂任务（如\"搜索AI新闻并整理报告\"）→ 3-5 步\n\n"
    "输出 JSON 数组：\n"
    '[{"description": "步骤描述", "tool_hint": "python_execute|工具名"}]\n'
)


def plan_task(client: Any, user_message: str) -> Plan:
    """用 LLM 将任务拆解为步骤计划。"""
    plan = Plan(task=user_message)
    try:
        resp = client.chat_completion(
            [
                {"role": "system", "content": _PLAN_SYSTEM_PROMPT},
                {"role": "user", "content": user_message},
            ],
            temperature=0.2,
            max_tokens=1024,
        )
        content = (resp.get("choices") or [{}])[0].get("message", {}).get("content", "")
        steps_data = json.loads(content)
        if isinstance(steps_data, list):
            for s in steps_data:
                plan.steps.append(
                    Step(
                        description=s.get("description", ""),
                        tool_hint=s.get("tool_hint", ""),
                    )
                )
    except Exception as exc:
        logger.debug("任务规划失败: %s", exc)
        plan.steps.append(Step(description=user_message, tool_hint=""))
    return plan


__all__ = ["is_chat_only", "classify_intent", "plan_task", "Plan", "Step"]
