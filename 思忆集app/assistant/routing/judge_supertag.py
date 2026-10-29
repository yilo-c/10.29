"""Judge Supertag 建议：从对话文本中检测实体并建议超标签。"""
from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Any


def suggest_supertag(
    text: str,
    tag_service=None,
) -> dict[str, Any] | None:
    """Detect if conversation text contains entities that suggest a supertag.

    Analyzes text for task, contact, or project patterns and returns
    a suggestion with detected supertag type, confidence, and any
    extracted field values.

    Args:
        text: User conversation text.
        tag_service: Optional AssistantTagService for schema lookup.

    Returns:
        { "supertag_type", "confidence", "suggestion_text",
          "extracted_fields", "trigger_phrase" } or None.
    """
    if not text or not text.strip():
        return None

    lower = text.strip().lower()

    # ── Task detection ──
    task_triggers = [
        "需要做", "要完成", "待办", "任务", "todo", "截止",
        "deadline", "优先级", "做完", "完成", "记得", "别忘了",
        "安排", "计划", "需要处理", "该做了", "要处理",
    ]
    task_score = sum(1 for t in task_triggers if t in lower)

    if task_score >= 2:
        fields = _extract_task_fields(text)
        return {
            "supertag_type": "@任务",
            "confidence": min(0.5 + task_score * 0.1, 0.95),
            "suggestion_text": "检测到任务信息，是否保存为 @任务？",
            "extracted_fields": fields,
            "trigger_phrase": "任务",
        }

    # ── Contact detection ──
    contact_triggers = [
        "电话", "手机", "邮箱", "email", "微信", "联系",
        "地址", "住在", "关系", "朋友", "同事", "家人",
    ]
    contact_score = sum(1 for t in contact_triggers if t in lower)

    has_phone = bool(re.search(r"1[3-9]\d{9}", text))
    has_email = bool(re.search(r"[\w.+-]+@[\w-]+\.[\w.]+", text))

    if (has_phone or has_email) and contact_score >= 1:
        fields = _extract_contact_fields(text)
        return {
            "supertag_type": "@联系人",
            "confidence": min(0.5 + (contact_score + int(has_phone) + int(has_email)) * 0.1, 0.95),
            "suggestion_text": "检测到联系人信息，是否保存为 @联系人？",
            "extracted_fields": fields,
            "trigger_phrase": "联系人",
        }
    if contact_score >= 3:
        fields = _extract_contact_fields(text)
        return {
            "supertag_type": "@联系人",
            "confidence": min(0.5 + contact_score * 0.1, 0.85),
            "suggestion_text": "检测到联系人信息，是否保存为 @联系人？",
            "extracted_fields": fields,
            "trigger_phrase": "联系人",
        }

    # ── Project detection ──
    project_triggers = [
        "项目", "启动", "进行中", "完成", "暂停", "归档",
        "负责人", "owner", "里程碑", "迭代", "sprint",
    ]
    project_score = sum(1 for t in project_triggers if t in lower)

    if project_score >= 2:
        fields = _extract_project_fields(text)
        return {
            "supertag_type": "@项目",
            "confidence": min(0.5 + project_score * 0.1, 0.9),
            "suggestion_text": "检测到项目信息，是否保存为 @项目？",
            "extracted_fields": fields,
            "trigger_phrase": "项目",
        }

    # ── Single high-confidence trigger ──
    if task_score == 1 and any(t in lower for t in ("deadline", "截止", "优先级")):
        fields = _extract_task_fields(text)
        return {
            "supertag_type": "@任务",
            "confidence": 0.6,
            "suggestion_text": "检测到任务信息，是否保存为 @任务？",
            "extracted_fields": fields,
            "trigger_phrase": "任务",
        }

    return None


def _extract_task_fields(text: str) -> dict[str, Any]:
    """Extract task-related fields from text using pattern matching."""
    fields: dict[str, Any] = {}
    lower = text.lower()

    priority_map = {
        "urgent": "urgent", "紧急": "urgent", "加急": "urgent",
        "high": "high", "高": "high", "重要": "high",
        "medium": "medium", "中": "medium",
        "low": "low", "低": "low",
    }
    for keyword, value in priority_map.items():
        if keyword in lower:
            fields["priority"] = value
            break

    status_map = {
        "已完成": "done", "完成了": "done", "做完": "done",
        "进行中": "in_progress", "正在做": "in_progress",
        "阻塞": "blocked", "卡住了": "blocked", "blocked": "blocked",
    }
    for keyword, value in status_map.items():
        if keyword in lower:
            fields["status"] = value
            break

    date_match = re.search(
        r"(?:截止|deadline|到期|完成)[^\d]*?(\d{4}[-/]\d{1,2}[-/]\d{1,2})",
        text,
    )
    if date_match:
        fields["deadline"] = date_match.group(1).replace("/", "-")

    if "deadline" not in fields:
        now = datetime.now()
        if "明天" in lower:
            fields["deadline"] = (now + timedelta(days=1)).strftime("%Y-%m-%d")
        elif "这周" in lower or "本周" in lower:
            fields["deadline"] = (now + timedelta(days=(6 - now.weekday()))).strftime("%Y-%m-%d")
        elif "下周" in lower:
            fields["deadline"] = (now + timedelta(days=(6 - now.weekday()) + 7)).strftime("%Y-%m-%d")

    return fields


def _extract_contact_fields(text: str) -> dict[str, Any]:
    """Extract contact-related fields from text."""
    fields: dict[str, Any] = {}

    phone_match = re.search(r"1[3-9]\d{9}", text)
    if phone_match:
        fields["phone"] = phone_match.group()

    email_match = re.search(r"[\w.+-]+@[\w-]+\.[\w.]+", text)
    if email_match:
        fields["email"] = email_match.group()

    name_match = re.search(r"(?:我叫|我是|姓名[：:])\s*(\S{2,4})", text)
    if name_match:
        fields["relation"] = name_match.group(1)

    relation_map = {
        "朋友": "朋友", "同事": "同事", "家人": "家人",
        "同学": "同学", "亲戚": "亲戚", "客户": "客户",
    }
    lower = text.lower()
    for keyword, value in relation_map.items():
        if keyword in lower:
            fields["relation"] = value
            break

    return fields


def _extract_project_fields(text: str) -> dict[str, Any]:
    """Extract project-related fields from text."""
    fields: dict[str, Any] = {}
    lower = text.lower()

    status_map = {
        "已归档": "archived", "归档": "archived",
        "已完成": "completed", "完成": "completed",
        "暂停": "paused",
        "进行中": "active", "启动": "active",
    }
    for keyword, value in status_map.items():
        if keyword in lower:
            fields["status"] = value
            break

    owner_match = re.search(r"(?:负责人|owner|由谁)[：:\s]*(\S+)", text)
    if owner_match:
        fields["owner"] = owner_match.group(1).strip()

    date_match = re.search(
        r"(?:截止|deadline|到期)[^\d]*?(\d{4}[-/]\d{1,2}[-/]\d{1,2})",
        text,
    )
    if date_match:
        fields["deadline"] = date_match.group(1).replace("/", "-")

    return fields
