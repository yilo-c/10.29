"""SuggestionItem — 低打扰建议的模型定义。

每个建议包含：
- 显示文本
- 建议原因（为什么现在展示）
- 关联的动作键
- 可转换的动作列表（如转笔记、转任务）
- 状态（活跃/已关闭）
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any

# ── 状态常量 ─────────────────────────────────────────────
SUGGESTION_STATUS_ACTIVE = "active"
SUGGESTION_STATUS_DISMISSED = "dismissed"

# ── 可转换的动作常量 ─────────────────────────────────────
CONVERT_TO_NOTE = "notes"
CONVERT_TO_TASK = "tasks"
CONVERT_TO_INBOX = "capture"
CONVERT_TO_TRANSLATE = "translate"
CONVERT_TO_NEWS = "news"


@dataclass
class SuggestionItem:
    """一条建议条目。

    Fields
    ------
    suggestion_id : str
        唯一标识，用于关闭跟踪。
    text : str
        展示给用户的建议文本。
    reason : str
        建议被触发的简短原因，用小字显示。
    action_key : str
        点击建议主动作时触发的操作键。
    status : str
        active 或 dismissed。
    convertible_actions : list[dict[str, str]]
        可转换的动作列表，每项含 label 和 action。
    metadata : dict
        扩展信息。
    """

    suggestion_id: str = ""
    text: str = ""
    reason: str = ""
    action_key: str = ""
    status: str = SUGGESTION_STATUS_ACTIVE
    convertible_actions: list[dict[str, str]] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        if not self.suggestion_id:
            self.suggestion_id = uuid.uuid4().hex[:12]
        if not self.convertible_actions:
            self.convertible_actions = self._default_convertible_actions()

    @staticmethod
    def _default_convertible_actions() -> list[dict[str, str]]:
        return [
            {"label": "转笔记", "action": CONVERT_TO_NOTE},
            {"label": "转任务", "action": CONVERT_TO_TASK},
        ]

    def to_dict(self) -> dict[str, Any]:
        return {
            "suggestion_id": self.suggestion_id,
            "text": self.text,
            "reason": self.reason,
            "action_key": self.action_key,
            "status": self.status,
            "convertible_actions": list(self.convertible_actions or []),
            "metadata": dict(self.metadata or {}),
        }

    @staticmethod
    def from_dict(data: dict[str, Any]) -> SuggestionItem:
        return SuggestionItem(
            suggestion_id=str(data.get("suggestion_id") or uuid.uuid4().hex[:12]),
            text=str(data.get("text") or ""),
            reason=str(data.get("reason") or ""),
            action_key=str(data.get("action_key") or ""),
            status=str(data.get("status") or SUGGESTION_STATUS_ACTIVE),
            convertible_actions=list(data.get("convertible_actions") or []),
            metadata=dict(data.get("metadata") or {}),
        )

    def dismiss(self) -> None:
        """关闭本条建议。"""
        self.status = SUGGESTION_STATUS_DISMISSED
