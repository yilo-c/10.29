"""NarrativeInjector — 将记忆叙事注入对话上下文。

在调用 inject_xiaowen_persona() 之前，先通过 NarrativeInjector 获取
最近的记忆叙事，用于填充人格 system prompt 中的 memory_narrative 占位符。
"""
from __future__ import annotations

import logging
from typing import Any

from memory.narrative.narrative_store import NarrativeStore

logger = logging.getLogger("siyi.memory.narrative.injector")


class NarrativeInjector:
    """叙事注入器：提供对话上下文所需的记忆叙事。"""

    def __init__(self, narrative_store: NarrativeStore | None = None):
        self._store = narrative_store

    async def get_memory_narrative(self, limit: int = 3) -> str:
        """获取最近的记忆叙事文本，供人格注入使用。

        Args:
            limit: 最多包含的叙事条数

        Returns:
            格式化的叙事文本（空字符串表示无可用叙事）
        """
        if not self._store:
            return ""

        try:
            recent = self._store.list_recent(limit=limit)
            if not recent:
                return ""

            parts: list[str] = []
            for n in recent:
                title = n.get("title", "")
                content = n.get("content", "")
                created = (n.get("created_at", "") or "")[:10]
                if title:
                    parts.append(f"[{created}] {title}")
                if content:
                    parts.append(content[:300])

            return "\n\n".join(parts) if parts else ""
        except Exception as exc:
            logger.debug("Failed to get memory narrative: %s", exc)
            return ""

    async def get_recent_milestones(self, limit: int = 5) -> list[dict[str, Any]]:
        """获取最近的里程碑事件。"""
        if not self._store:
            return []
        return self._store.list_by_type("milestone", limit=limit)

    async def get_mood_trend(self, weeks: int = 4) -> str:
        """获取情绪趋势摘要。"""
        if not self._store:
            return ""
        weeklies = self._store.list_by_type("weekly", limit=weeks)
        if not weeklies:
            return ""
        moods: list[str] = []
        for w in weeklies:
            es = w.get("emotion_summary", {})
            mood = es.get("overall_mood", "?")
            label = w.get("week_label", "")
            moods.append(f"{label}: {mood}")
        return " → ".join(moods) if moods else ""
