"""Delivery 子包 — 结果交付通道。

- ``inbox``：统一结果交付（收件箱 + 通知 + 聊天 + 悬浮 + 字幕）。

从 assistant/ 顶层迁入（原 inbox.py 保留 re-export 兼容层）。
"""
from assistant.delivery.inbox import *  # noqa: F401,F403

__all__ = ["AssistantInboxService", "InboxItem", "STAGE_INBOX", "STAGE_DAILY", "STAGE_INSIGHTS"]
