"""WeeklyNarrativeGenerator — 周度"你的轨迹"生成器。

每周从记忆/笔记/里程碑/情绪数据中生成一篇叙事总结。
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from memory.narrative.emotion_tracker import EmotionTracker
from memory.narrative.milestone_detector import MilestoneDetector
from memory.narrative.narrative_store import NarrativeStore

logger = logging.getLogger("siyi.memory.narrative.weekly")


class WeeklyNarrativeGenerator:
    """周度叙事生成器。"""

    def __init__(
        self,
        narrative_store: NarrativeStore,
        milestone_detector: MilestoneDetector | None = None,
        emotion_tracker: EmotionTracker | None = None,
        ai_text_service: Any = None,
    ):
        self._store = narrative_store
        # ai_text_service：S3-1 弱词里程碑候选的批量 LLM 判评端口（可空）。
        self._milestone_detector = milestone_detector or MilestoneDetector(ai_text_service=ai_text_service)
        self._emotion_tracker = emotion_tracker or EmotionTracker()

    async def generate(self, memories: list[dict[str, Any]], notes: list[dict[str, Any]] = None) -> dict[str, Any]:
        """生成一篇周度叙事。

        Args:
            memories: 本周的记忆片段列表
            notes: 本周的笔记列表（可选）

        Returns:
            叙事条目
        """
        now = datetime.now(timezone.utc)
        week_start = now - timedelta(days=7)
        week_label = now.strftime("%Y-W%V")

        all_items = list(memories)
        if notes:
            all_items.extend(notes)

        # 检测里程碑
        milestones = self._milestone_detector.detect_from_memories(all_items)

        # 情绪分析
        emotion_result = self._emotion_tracker.analyze_memories(all_items)

        # 构建叙事
        title = self._build_title(week_label, emotion_result, milestones)
        summary = self._build_summary(all_items, milestones, emotion_result)

        narrative: dict[str, Any] = {
            "narrative_type": "weekly",
            "title": title,
            "content": summary,
            "start_date": week_start.isoformat(),
            "end_date": now.isoformat(),
            "week_label": week_label,
            "milestones": milestones,
            "emotion_summary": emotion_result,
            "item_count": len(all_items),
            "source_memory_ids": [m.get("id") for m in memories if m.get("id")],
        }

        # 保存
        self._store.save(narrative)
        logger.info("Weekly narrative generated: %s", title)
        return narrative

    def _build_title(self, week_label: str, emotion: dict[str, Any], milestones: list) -> str:
        """从数据构建周度标题。"""
        if milestones:
            return f"{week_label} · {len(milestones)} 件事值得记住"
        mood = emotion.get("overall_mood", "neutral")
        if mood == "positive":
            return f"{week_label} · 这一周还不错"
        elif mood == "negative":
            return f"{week_label} · 有点难，但你撑过来了"
        return f"{week_label} · 你的一周回顾"

    def _build_summary(self, items: list[dict[str, Any]], milestones: list, emotion: dict[str, Any]) -> str:
        """构建叙事正文。"""
        parts: list[str] = []

        # 数据概览
        parts.append(f"这周你留下了 {len(items)} 条记录。")

        # 里程碑
        if milestones:
            parts.append("\n\n值得记住的事：")
            for m in milestones[:5]:
                preview = m.get("preview", "")[:100]
                parts.append(f"- {preview}")

        # 情绪
        top_emotions = emotion.get("top_emotions", [])
        if top_emotions:
            top_names = [e["emotion"] for e in top_emotions[:3]]
            parts.append(f"\n\n情绪关键词：{'、'.join(top_names)}")

        # 关注点
        if emotion.get("overall_mood") == "negative":
            parts.append("\n\n这周似乎有些压力。记得照顾自己——休息和努力同样重要。")
        elif emotion.get("overall_mood") == "positive":
            parts.append("\n\n这周状态不错。这样的时刻值得珍惜。")

        return "".join(parts)
