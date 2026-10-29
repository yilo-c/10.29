"""MonthlyLetterGenerator — 月度"你的一月"信件生成器（T7.1）。

聚合上月记忆/笔记/里程碑/情绪数据，生成一篇月度叙事（narrative_type=monthly），
语义与周度周报（WeeklyNarrativeGenerator）对齐，但覆盖整月：
- 按月去重（month_label = YYYY-MM）
- 复用 MilestoneDetector / EmotionTracker 聚合
- 正文侧重"月度回顾"（里程碑 / 情绪趋势 / 数据概览）
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from memory.narrative.emotion_tracker import EmotionTracker
from memory.narrative.milestone_detector import MilestoneDetector
from memory.narrative.narrative_store import NarrativeStore

logger = logging.getLogger("siyi.memory.narrative.monthly")


class MonthlyLetterGenerator:
    """月度信件生成器。"""

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

    async def generate(
        self,
        memories: list[dict[str, Any]],
        notes: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any] | None:
        """生成一篇月度叙事。

        Args:
            memories: 上月记忆片段列表
            notes: 上月笔记列表（可选）

        Returns:
            叙事条目；无数据时返回 None（不写入存储）。
        """
        now = datetime.now(timezone.utc)
        month_start = now.replace(day=1) - timedelta(days=1)
        month_start = month_start.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        month_label = now.strftime("%Y-%m")

        all_items = list(memories)
        if notes:
            all_items.extend(notes)
        if not all_items:
            return None

        # 检测里程碑
        milestones = self._milestone_detector.detect_from_memories(all_items)

        # 情绪分析
        emotion_result = self._emotion_tracker.analyze_memories(all_items)

        # 构建月度信
        title = self._build_title(month_label, emotion_result, milestones)
        summary = self._build_summary(all_items, milestones, emotion_result)

        narrative: dict[str, Any] = {
            "narrative_type": "monthly",
            "title": title,
            "content": summary,
            "start_date": month_start.isoformat(),
            "end_date": now.isoformat(),
            "month_label": month_label,
            "milestones": milestones,
            "emotion_summary": emotion_result,
            "item_count": len(all_items),
            "source_memory_ids": [m.get("id") for m in memories if m.get("id")],
        }

        # 保存（NarrativeStore 支持 monthly 类型存取）
        self._store.save(narrative)
        logger.info("Monthly letter generated: %s", title)
        return narrative

    def _build_title(self, month_label: str, emotion: dict[str, Any], milestones: list) -> str:
        """从数据构建月度标题。"""
        if milestones:
            return f"{month_label} · {len(milestones)} 件值得记住的事"
        mood = emotion.get("overall_mood", "neutral")
        if mood == "positive":
            return f"{month_label} · 这个月过得不错"
        elif mood == "negative":
            return f"{month_label} · 不容易，但坚持过来了"
        return f"{month_label} · 你的一月回顾"

    def _build_summary(
        self,
        items: list[dict[str, Any]],
        milestones: list,
        emotion: dict[str, Any],
    ) -> str:
        """构建月度信正文。"""
        parts: list[str] = []

        parts.append(f"这个月你留下了 {len(items)} 条记录。")

        if milestones:
            parts.append("\n\n值得记住的事：")
            for m in milestones[:6]:
                preview = m.get("preview", "")[:100]
                if preview:
                    parts.append(f"- {preview}")

        top_emotions = emotion.get("top_emotions", [])
        if top_emotions:
            top_names = [e["emotion"] for e in top_emotions[:3]]
            parts.append(f"\n\n情绪关键词：{'、'.join(top_names)}")

        if emotion.get("overall_mood") == "negative":
            parts.append("\n\n这个月似乎有压力。记得照顾好自己——休息和努力同样重要。")
        elif emotion.get("overall_mood") == "positive":
            parts.append("\n\n这个月状态不错。这样的时刻值得珍惜。")

        parts.append("\n\n—— 来自你的小问，月度回望。")
        return "".join(parts)
