"""统一交付路由抽象：连接任务执行结果到各可见通道。

所有任务结果必须通过此模块路由到具体交付通道（聊天区、收件箱卡片、
悬浮入口、系统通知、字幕提示），不允许能力层直接弹窗或绕过路由。

交付通道常量与 assistant_inbox 保持一致：
- inbox:           桌面内收件箱（默认，低优先级结果）
- notification:    桌面系统通知（高优提醒）
- both:            同时进收件箱 + 桌面通知
- chat_output:     聊天面板输出（对话式回复）
- floating_output: 悬浮助手气泡（快捷操作结果）
- subtitle_output: 底部字幕条（短时提示，自动消失）
"""
from __future__ import annotations

from typing import Any

from assistant.inbox import (
    CHANNEL_BOTH,
    CHANNEL_CHAT,
    CHANNEL_FLOATING,
    CHANNEL_INBOX,
    CHANNEL_NOTIFICATION,
    CHANNEL_SUBTITLE,
    ALL_CHANNELS,
)

# ── 规范通道名 ─────────────────────────────────────────────

CANONICAL_CHANNEL_ALIASES: dict[str, str] = {
    "chat": CHANNEL_CHAT,
    "chat_output": CHANNEL_CHAT,
    "inbox": CHANNEL_INBOX,
    "notification": CHANNEL_NOTIFICATION,
    "both": CHANNEL_BOTH,
    "floating": CHANNEL_FLOATING,
    "floating_output": CHANNEL_FLOATING,
    "subtitle": CHANNEL_SUBTITLE,
    "subtitle_output": CHANNEL_SUBTITLE,
}

DELIVERY_CHANNEL_LABELS: dict[str, str] = {
    CHANNEL_INBOX: "收件箱",
    CHANNEL_NOTIFICATION: "系统通知",
    CHANNEL_BOTH: "收件箱+通知",
    CHANNEL_CHAT: "聊天面板",
    CHANNEL_FLOATING: "悬浮助手",
    CHANNEL_SUBTITLE: "字幕提示",
}


def resolve_channel(raw: str | None) -> str:
    """将用户/任务指定的通道名规范化为标准通道常量。"""
    key = str(raw or "").strip().lower()
    return CANONICAL_CHANNEL_ALIASES.get(key, CHANNEL_INBOX)


def is_visible_channel(channel: str) -> bool:
    """是否属于用户可见的弹出式通道（非仅收件箱）。"""
    return channel in (CHANNEL_NOTIFICATION, CHANNEL_BOTH, CHANNEL_FLOATING, CHANNEL_SUBTITLE)


# ── 交付结果记录 ────────────────────────────────────────────


class DeliveryRecord:
    """轻量交付结果记录，描述一次交付路由的最终去向。"""

    __slots__ = ("channel", "label", "inbox_item_id", "subtitle_text", "delivered_to")

    def __init__(
        self,
        channel: str,
        *,
        label: str = "",
        inbox_item_id: str = "",
        subtitle_text: str = "",
        delivered_to: list[str] | None = None,
    ):
        self.channel = channel
        self.label = label or DELIVERY_CHANNEL_LABELS.get(channel, channel)
        self.inbox_item_id = inbox_item_id
        self.subtitle_text = subtitle_text
        self.delivered_to = delivered_to or []

    def to_dict(self) -> dict[str, Any]:
        return {
            "channel": self.channel,
            "label": self.label,
            "inbox_item_id": self.inbox_item_id,
            "subtitle_text": self.subtitle_text,
            "delivered_to": self.delivered_to,
        }


# ── 字幕文案构建 ────────────────────────────────────────────


def build_task_progress_subtitle(
    *,
    goal: str,
    total_steps: int = 0,
    ok_steps: int = 0,
    failed_steps: int = 0,
    blocked_steps: int = 0,
    delivery_channel: str = "",
) -> str:
    """构建任务进度/完成摘要的字幕文案。

    返回一行简短的中文字幕，用于 subtitle 通道展示。
    包含任务目标、执行步数和最终去向。
    """
    goal_text = str(goal or "").strip() or "任务"

    # 全部成功
    if failed_steps == 0 and blocked_steps == 0:
        if total_steps == 0:
            return f"✅ {goal_text} 已完成。"
        return f"✅ {goal_text}：{ok_steps}/{total_steps} 步全部成功。"

    # 部分失败
    parts = [f"{goal_text}：{ok_steps}/{total_steps} 步成功"]
    if failed_steps:
        parts.append(f"{failed_steps} 步失败")
    if blocked_steps:
        parts.append(f"{blocked_steps} 步被拦截")

    channel_label = DELIVERY_CHANNEL_LABELS.get(delivery_channel, "") if delivery_channel else ""
    if channel_label:
        parts.append(f"结果进入{channel_label}")

    return "⚠️ " + "，".join(parts) + "。"


def build_subtitle_tone(summary: dict[str, Any]) -> str:
    """根据执行摘要推断字幕情绪。"""
    failed = int(summary.get("failed") or 0)
    blocked = int(summary.get("blocked") or 0)
    degraded = int(summary.get("degraded") or 0)
    if failed or blocked:
        return "alert"
    if degraded:
        return "thinking"
    return "success"
