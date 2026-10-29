"""记忆叙事引擎 — 从碎片记忆重组为你的个人轨迹。

设计文档：docs/companion-agent-design.md 搂四

能力：
- weekly_narrative: 周度"你的轨迹"生成
- milestone_detector: 里程碑检测
- emotion_tracker: 情绪曲线生成
- narrative_injector: 将叙事注入对话上下文
"""
from __future__ import annotations

from .narrative_store import NarrativeStore
from .weekly_narrative import WeeklyNarrativeGenerator
from .milestone_detector import MilestoneDetector
from .emotion_tracker import EmotionTracker
from .narrative_injector import NarrativeInjector

__all__ = [
    "NarrativeStore",
    "WeeklyNarrativeGenerator",
    "MilestoneDetector",
    "EmotionTracker",
    "NarrativeInjector",
]
