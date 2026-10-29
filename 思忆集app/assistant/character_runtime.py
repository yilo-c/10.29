from __future__ import annotations

import enum
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

# ── EmotionState ──────────────────────────────────────────


class EmotionType(enum.Enum):
    """角色情绪类型枚举。"""
    THINKING = "thinking"
    SUCCESS = "success"
    ALERT = "alert"
    NEUTRAL = "neutral"


@dataclass
class EmotionState:
    """轻量情绪与状态映射。

    角色层使用此对象表达当前情绪状态，不承载业务逻辑。
    状态映射：
    - thinking:  思考/专注/规划中
    - success:   完成/成功/积极
    - alert:     提醒/失败/异常/等待确认
    - neutral:   默认/空闲/安静
    """
    emotion: EmotionType = EmotionType.NEUTRAL
    intensity: float = 0.5
    message: str = ""
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    @classmethod
    def thinking(cls, message: str = "", intensity: float = 0.6) -> EmotionState:
        return cls(emotion=EmotionType.THINKING, intensity=intensity, message=message)

    @classmethod
    def success(cls, message: str = "", intensity: float = 0.7) -> EmotionState:
        return cls(emotion=EmotionType.SUCCESS, intensity=intensity, message=message)

    @classmethod
    def alert(cls, message: str = "", intensity: float = 0.8) -> EmotionState:
        return cls(emotion=EmotionType.ALERT, intensity=intensity, message=message)

    @classmethod
    def neutral(cls, message: str = "", intensity: float = 0.3) -> EmotionState:
        return cls(emotion=EmotionType.NEUTRAL, intensity=intensity, message=message)

    def to_dict(self) -> dict[str, Any]:
        return {
            "emotion": self.emotion.value,
            "intensity": self.intensity,
            "message": self.message,
            "timestamp": self.timestamp,
        }

    def to_tone(self) -> str:
        """转换为 subtitle tone 字符串。"""
        return self.emotion.value


# ── CharacterRenderer ────────────────────────────────────


@dataclass
class CharacterRenderer:
    """角色表现层外壳：头像/气泡/插图，不承接任何业务逻辑。

    职责：
    - 管理当前情绪状态
    - 提供角色显示信息（名称、头像、状态文本）
    - 作为情感状态与 UI 之间的轻量适配器

    不负责：
    - 任何任务规划、执行或安全逻辑
    - 直接操作 UI 组件
    - 持有长期真源数据
    """
    name: str = "小问"
    avatar_text: str = "?"
    status_text: str = ""
    emotion: EmotionState = field(default_factory=EmotionState.neutral)

    def update_emotion(self, emotion: EmotionState) -> None:
        """更新情绪状态（不可变替换）。"""
        self.emotion = emotion

    def to_display_context(self) -> dict[str, Any]:
        """构建显示上下文，供 UI 层消费。"""
        return {
            "name": self.name,
            "avatar_text": self.avatar_text,
            "status_text": self.status_text or self.emotion.message,
            "emotion": self.emotion.to_dict(),
            "tone": self.emotion.to_tone(),
        }


# ── 工具映射 ─────────────────────────────────────────────


_TONE_ALIASES: dict[str, str] = {
    "focus": "thinking", "curious": "thinking", "planning": "thinking",
    "success": "success", "proud": "success", "warm": "success", "encourage": "success",
    "warning": "alert", "error": "alert", "risk": "alert", "alert": "alert",
    "calm": "neutral", "idle": "neutral", "neutral": "neutral", "thinking": "thinking",
}


def canonical_subtitle_tone(tone: str) -> str:
    """将语气别名归约为标准情绪标识。"""
    return _TONE_ALIASES.get(str(tone or "neutral").strip().lower(), "neutral")


def map_assistant_tone_to_mood(tone: str) -> str:
    """将助手语气/情绪映射到宠物情绪。"""
    normalized = str(tone or "neutral").strip().lower()
    aliases = {
        "focus": "thinking", "curious": "thinking", "planning": "thinking",
        "success": "success", "proud": "success", "warm": "success", "encourage": "success",
        "warning": "alert", "error": "alert", "risk": "alert", "alert": "alert",
        "calm": "neutral", "idle": "neutral", "neutral": "neutral", "thinking": "thinking",
    }
    canonical = aliases.get(normalized, "neutral")
    mapping = {
        "thinking": "thinking",
        "success": "happy",
        "alert": "alert",
        "neutral": "idle",
    }
    return mapping.get(canonical, "idle")


# ── AssistantEvent ────────────────────────────────────────────


@dataclass
class AssistantEvent:
    """助手事件：来自浏览器桥接、日程触发或用户操作的统一事件。"""
    source: str = "unknown"
    event_type: str = "unknown"
    message: str = ""
    payload: dict[str, Any] = field(default_factory=dict)
    event_id: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    intercepted_by: str = ""
    matched_tools: list[str] = field(default_factory=list)


# ── AssistantToolSpec / AssistantToolRegistry ─────────────────


@dataclass
class AssistantToolSpec:
    """助手工具规格：描述一个可注册的工具/动作。"""
    tool_id: str = ""
    label: str = ""
    description: str = ""
    risk_level: str = "low"
    source: str = "local_action"
    trigger_terms: tuple[str, ...] = field(default_factory=tuple)
    requires_confirmation: bool = False


class AssistantToolRegistry:
    """助手工具注册表：管理本地动作和能力目录的注册与匹配。"""

    def __init__(self):
        self._tools: list[AssistantToolSpec] = []
        self._local_actions: set[str] = set()
        self._capabilities: list[AssistantToolSpec] = []

    def register_local_actions(self, actions: list[tuple[str, tuple[str, ...], str, str]]) -> None:
        specs: list[AssistantToolSpec] = []
        for action in actions:
            if not action or len(action) < 4:
                continue
            tool_id, trigger_terms, description, label = action
            specs.append(AssistantToolSpec(
                tool_id=f"local.{tool_id}",
                label=label,
                description=description,
                risk_level="medium",
                trigger_terms=tuple(trigger_terms) if isinstance(trigger_terms, (list, tuple)) else (str(trigger_terms),),
                requires_confirmation=True,
            ))
        self.register_batch(specs)

    def register_capability_catalog(self, catalog: list[dict[str, Any]]) -> None:
        from services.react_tool_catalog import to_step_risk

        specs: list[AssistantToolSpec] = []
        for entry in catalog:
            action_id = entry.get("action_id", "")
            if action_id:
                # 目录 risk 已统一到 read/write/modify/destructive（审计波次②
                # C-1 步骤 4），而 AssistantToolSpec.risk_level /
                # requires_confirmation 的消费者（profile/prompt_context.py）仍按
                # low/medium/high 判定 → 先回落，以免“需确认”判定静默失效。
                risk = to_step_risk(entry.get("risk", "low"))
                specs.append(AssistantToolSpec(
                    tool_id=action_id,
                    label=entry.get("description", action_id),
                    description=f"{entry.get('description', '')} payload={entry.get('payload_schema', {})}",
                    risk_level=risk,
                    requires_confirmation=risk in ("medium", "high"),
                ))
        self.register_batch(specs)

    def register_batch(self, specs: list[AssistantToolSpec]) -> None:
        for spec in specs:
            if spec.tool_id:
                existing = [t for t in self._tools if t.tool_id == spec.tool_id]
                if not existing:
                    self._tools.append(spec)

    def preview_labels(self, limit: int = 6) -> list[str]:
        return [t.label for t in self._tools[:limit]]

    def list_tools(self) -> list[AssistantToolSpec]:
        return list(self._tools)

    def match_tools(self, text: str, limit: int = 0) -> list[AssistantToolSpec]:
        if not text:
            return []
        lower = text.lower()
        matched: list[AssistantToolSpec] = []
        for tool in self._tools:
            if any(term.lower() in lower for term in tool.trigger_terms):
                matched.append(tool)
        if limit > 0:
            return matched[:limit]
        return matched

    def get_all_tools(self) -> list[AssistantToolSpec]:
        return list(self._tools)


# ── BrowserAssistantEventPolicy ───────────────────────────────


class BrowserAssistantEventPolicy:
    """浏览器桥接事件策略：路由分类和字幕构建。"""

    ROUTE_CAPTURE = "capture"
    ROUTE_TASK = "tasks"
    ROUTE_NOTE = "organize_current"
    ROUTE_INBOX = "inbox"

    _ROUTE_MAP: dict[str, str] = {
        "browser.summarized": ROUTE_CAPTURE,
        "browser.captured": ROUTE_CAPTURE,
        "browser.task_created": ROUTE_TASK,
        "browser.note_created": ROUTE_NOTE,
    }

    _TASK_KEYWORDS = ("任务", "执行", "上线", "bug", "修复", "待办", "todo", "deadline")

    _SUBTITLE_MAP: dict[str, tuple[str, str]] = {
        "browser.summarized": ("网页总结已收入收件箱", "success"),
        "browser.captured": ("网页内容已收件", "success"),
        "browser.task_created": ("浏览器任务已创建", "success"),
        "browser.note_created": ("笔记已沉淀", "proud"),
    }

    @staticmethod
    def classify_route(event_type: str, payload: dict[str, Any] | None = None) -> tuple[str, float]:
        route = BrowserAssistantEventPolicy._ROUTE_MAP.get(str(event_type or ""), BrowserAssistantEventPolicy.ROUTE_CAPTURE)
        confidence = 85
        payload = payload or {}
        summary = str(payload.get("summary", "") or "")
        if any(kw in summary for kw in BrowserAssistantEventPolicy._TASK_KEYWORDS):
            route = BrowserAssistantEventPolicy.ROUTE_TASK
            confidence = max(confidence, 75)
        return route, confidence

    @staticmethod
    def build_subtitle(event_type: str, payload: dict[str, Any] | None = None) -> tuple[str, str, str]:
        payload = payload or {}
        recommended = payload.get("recommendedActions") or []
        if recommended and isinstance(recommended, list):
            top = recommended[0]
            label = str(top.get("label", "") or "")
            if label:
                route = BrowserAssistantEventPolicy.ROUTE_INBOX
                tone = "planning"
                return label, tone, route

        subtitle, tone = BrowserAssistantEventPolicy._SUBTITLE_MAP.get(
            str(event_type or ""), ("浏览器事件已处理", "neutral")
        )
        route = BrowserAssistantEventPolicy._ROUTE_MAP.get(
            str(event_type or ""), BrowserAssistantEventPolicy.ROUTE_CAPTURE
        )
        return subtitle, tone, route
