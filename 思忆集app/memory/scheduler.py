"""MemoryScheduler — 基于 SM-2 间隔重复的记忆回顾调度 (MCP-11).

对标 Reflect.app 的间隔重复记忆回顾：
- SM-2 简化算法决定每条记忆的回访间隔
- 每日回顾推送：昨日摘要 + 本周重点 + 待巩固的旧记忆
- 历史上的今天：展示往年今日的记忆
- 回顾统计：连续打卡天数、已巩固数量、待复习数量
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any

logger = logging.getLogger("siyi.memory.scheduler")

# SM-2 默认参数
INITIAL_INTERVAL_DAYS = 1        # 首次复习间隔（天）
SECOND_INTERVAL_DAYS = 3         # 第二次复习间隔
THIRD_INTERVAL_DAYS = 7          # 第三次复习间隔
DEFAULT_EASINESS = 2.5           # 默认难易度因子
MIN_EASINESS = 1.3               # 最小难易度因子
DEFAULT_MAX_INTERVAL = 90        # 稳定期最大间隔（天）
STREAK_GRACE_HOURS = 36          # 连续打卡宽限时长（小时）


@dataclass
class ScheduledMemory:
    """带 SM-2 调度信息的记忆条目。"""
    memory_id: str
    content: str
    memory_type: str = "factual"
    tags: list[str] = field(default_factory=list)
    created_at: str = ""
    last_reviewed_at: str = ""
    next_review_at: str = ""
    interval_days: int = INITIAL_INTERVAL_DAYS
    easiness: float = DEFAULT_EASINESS
    repetitions: int = 0          # 连续正确次数（>=4）
    review_count: int = 0         # 总复习次数
    last_quality: int = 0         # 上次评分 (0-5)

    def to_dict(self) -> dict[str, Any]:
        return {
            "memory_id": self.memory_id,
            "content": self.content,
            "memory_type": self.memory_type,
            "tags": list(self.tags),
            "created_at": self.created_at,
            "last_reviewed_at": self.last_reviewed_at,
            "next_review_at": self.next_review_at,
            "interval_days": self.interval_days,
            "easiness": self.easiness,
            "repetitions": self.repetitions,
            "review_count": self.review_count,
            "last_quality": self.last_quality,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ScheduledMemory:
        return cls(
            memory_id=str(data.get("memory_id", "")),
            content=str(data.get("content", "")),
            memory_type=str(data.get("memory_type", "factual")),
            tags=list(data.get("tags") or []),
            created_at=str(data.get("created_at") or ""),
            last_reviewed_at=str(data.get("last_reviewed_at") or ""),
            next_review_at=str(data.get("next_review_at") or ""),
            interval_days=int(data.get("interval_days", INITIAL_INTERVAL_DAYS)),
            easiness=float(data.get("easiness", DEFAULT_EASINESS)),
            repetitions=int(data.get("repetitions", 0)),
            review_count=int(data.get("review_count", 0)),
            last_quality=int(data.get("last_quality", 0)),
        )


@dataclass
class ReviewSettings:
    """回顾设置。"""
    review_hour: int = 9              # 每日回顾推送时间（小时, 24h）
    review_minute: int = 0            # 每日回顾推送时间（分钟）
    daily_max_items: int = 15         # 每日最大回顾条数
    quiet_days: list[int] = field(default_factory=list)  # 安静日（0=周一, 6=周日）
    enabled: bool = True              # 是否启用每日回顾

    def to_dict(self) -> dict[str, Any]:
        return {
            "review_hour": self.review_hour,
            "review_minute": self.review_minute,
            "daily_max_items": self.daily_max_items,
            "quiet_days": list(self.quiet_days),
            "enabled": self.enabled,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ReviewSettings:
        return cls(
            review_hour=int(data.get("review_hour", 9)),
            review_minute=int(data.get("review_minute", 0)),
            daily_max_items=int(data.get("daily_max_items", 15)),
            quiet_days=list(data.get("quiet_days") or []),
            enabled=bool(data.get("enabled", True)),
        )

    def is_quiet_today(self) -> bool:
        """判断今天是否为安静日。"""
        weekday = datetime.now(timezone.utc).weekday()
        return weekday in self.quiet_days


class MemoryScheduler:
    """基于 SM-2 间隔重复算法的记忆回顾调度器。

    用法:
        scheduler = MemoryScheduler(data_store)
        scheduler.add_memory(memory_id, content)
        due = scheduler.get_due_memories()       # 今日待复习
        scheduler.review(memory_id, quality)      # 用户评分后更新
        review = scheduler.get_daily_review()     # 每日回顾内容
    """

    def __init__(self, data_store, max_interval_days: int = DEFAULT_MAX_INTERVAL):
        self.data_store = data_store
        self.max_interval_days = max_interval_days
        self._today_cache: list[ScheduledMemory] | None = None
        self._storage_key = "assistant_memory_schedules"

    # ── 持久化 ──

    def _load_all(self) -> dict[str, ScheduledMemory]:
        raw = self.data_store.load_json(self._storage_key, {})
        memories = {}
        for key, value in (raw.items() if isinstance(raw, dict) else {}):
            if isinstance(value, dict):
                memories[key] = ScheduledMemory.from_dict(value)
        return memories

    def _save_all(self, memories: dict[str, ScheduledMemory]) -> None:
        raw = {k: v.to_dict() for k, v in memories.items()}
        self.data_store.save_json(self._storage_key, raw)
        self._today_cache = None

    # ── 记忆管理 ──

    def add_memory(self, memory_id: str, content: str, memory_type: str = "factual",
                   tags: list[str] | None = None) -> ScheduledMemory:
        """添加新记忆到调度系统。"""
        now = datetime.now(timezone.utc).isoformat()
        memory = ScheduledMemory(
            memory_id=memory_id,
            content=content,
            memory_type=memory_type,
            tags=tags or [],
            created_at=now,
            next_review_at=(datetime.now(timezone.utc) + timedelta(days=INITIAL_INTERVAL_DAYS)).isoformat(),
        )
        memories = self._load_all()
        memories[memory_id] = memory
        self._save_all(memories)
        return memory

    def get_memory(self, memory_id: str) -> ScheduledMemory | None:
        """获取指定记忆的调度信息。"""
        memories = self._load_all()
        return memories.get(memory_id)

    def remove_memory(self, memory_id: str) -> bool:
        memories = self._load_all()
        if memory_id not in memories:
            return False
        del memories[memory_id]
        self._save_all(memories)
        return True

    # ── SM-2 算法 ──

    def _sm2_update(self, memory: ScheduledMemory, quality: int) -> None:
        """SM-2 核心算法：根据评分更新记忆调度参数。

        评分标准（SM-2 六分制）：
          5 = 完全记得，轻松
          4 = 记得，稍有犹豫（通过线）
          3 = 回忆困难
          2 = 不记得，但看到后能想起来
          1 = 完全不记得
          0 = 完全忘记

        根据 MCP-11 规范：
          - 评分 >= 4：通过，增加间隔
          - 评分 < 4：未通过，重置
          - 初始间隔：1 天 → 3 天 → 7 天
          - 稳定期后：interval *= EF
          - 稳定期最大间隔：可配置（默认 90 天）
        """
        memory.last_quality = quality
        memory.review_count += 1

        if quality >= 4:
            # 通过：计算新间隔
            if memory.repetitions == 0:
                memory.interval_days = INITIAL_INTERVAL_DAYS
            elif memory.repetitions == 1:
                memory.interval_days = SECOND_INTERVAL_DAYS
            elif memory.repetitions == 2:
                memory.interval_days = THIRD_INTERVAL_DAYS
            else:
                # 稳定期：interval *= EF（不超过最大间隔）
                memory.interval_days = min(
                    self.max_interval_days,
                    max(1, round(memory.interval_days * memory.easiness)),
                )
            memory.repetitions += 1
        else:
            # 未通过：重置
            memory.repetitions = 0
            memory.interval_days = INITIAL_INTERVAL_DAYS

        # 更新难易度因子（EF）
        # SM-2 标准公式：EF' = EF + (0.1 - (5-q) * (0.08 + (5-q) * 0.02))
        memory.easiness = max(
            MIN_EASINESS,
            memory.easiness + (0.1 - (5 - quality) * (0.08 + (5 - quality) * 0.02)),
        )

    def review(self, memory_id: str, quality: int) -> ScheduledMemory | None:
        """用户复习记忆，应用 SM-2 算法更新调度参数。

        Args:
            memory_id: 记忆 ID
            quality: 用户评分 0-5

        Returns:
            更新后的记忆，若不存在则返回 None
        """
        quality = max(0, min(5, quality))
        memories = self._load_all()
        memory = memories.get(memory_id)
        if memory is None:
            return None

        now = datetime.now(timezone.utc)
        memory.last_reviewed_at = now.isoformat()
        self._sm2_update(memory, quality)
        memory.next_review_at = (now + timedelta(days=memory.interval_days)).isoformat()

        memories[memory_id] = memory
        self._save_all(memories)
        return memory

    # ── 获取待复习记忆 ──

    def get_due_memories(self, limit: int = 20) -> list[ScheduledMemory]:
        """获取今日待复习的记忆。"""
        if self._today_cache is not None:
            return self._today_cache[:limit]

        memories = self._load_all()
        now = datetime.now(timezone.utc)
        due = []
        for m in memories.values():
            if not m.next_review_at:
                continue
            try:
                review_time = datetime.fromisoformat(m.next_review_at)
            except (ValueError, TypeError):
                continue
            if review_time <= now:
                due.append(m)

        due.sort(key=lambda m: (
            m.next_review_at or "",
            -(m.last_quality if m.review_count > 0 else 3),
            m.repetitions,
        ))

        self._today_cache = due
        return due[:limit]

    def get_new_memories(self, days: int = 1) -> list[ScheduledMemory]:
        """获取最近创建的记忆（用于昨日摘要等）。"""
        memories = self._load_all()
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        result = []
        for m in memories.values():
            if not m.created_at:
                continue
            try:
                created = datetime.fromisoformat(m.created_at)
            except (ValueError, TypeError):
                continue
            if created >= cutoff:
                result.append(m)
        result.sort(key=lambda m: m.created_at or "", reverse=True)
        return result

    def get_by_creation_date(self, target_date: date) -> list[ScheduledMemory]:
        """获取指定日期创建的记忆（用于历史上的今天）。"""
        memories = self._load_all()
        result = []
        for m in memories.values():
            if not m.created_at:
                continue
            try:
                created = datetime.fromisoformat(m.created_at)
            except (ValueError, TypeError):
                continue
            if created.date() == target_date:
                result.append(m)
        result.sort(key=lambda m: m.created_at or "", reverse=True)
        return result

    def get_by_anniversary(self, month: int, day: int) -> list[ScheduledMemory]:
        """获取往年今日（月/日相同但年份不同）的记忆。"""
        memories = self._load_all()
        result = []
        for m in memories.values():
            if not m.created_at:
                continue
            try:
                created = datetime.fromisoformat(m.created_at)
            except (ValueError, TypeError):
                continue
            # 月份和日期匹配，但年份不同（排除今年）
            if created.month == month and created.day == day and created.year != datetime.now(timezone.utc).year:
                result.append(m)
        result.sort(key=lambda m: m.created_at or "", reverse=True)
        return result

    # ── 快速评分入口 ──

    def quick_review(self, memory_id: str, remembered: bool) -> ScheduledMemory | None:
        """快速评分入口：记住了（评分 4）或需要复习（评分 2）。

        Args:
            memory_id: 记忆 ID
            remembered: True = 记住了（评分 4），False = 需要复习（评分 2）

        Returns:
            更新后的记忆，若不存在则返回 None
        """
        return self.review(memory_id, quality=4 if remembered else 2)

    # ── 每日回顾 ──

    def get_daily_review(self, limit: int = 15) -> dict[str, Any]:
        """生成每日回顾内容（三部分：昨日摘要 + 本周重点 + 待巩固旧记忆）。"""
        now = datetime.now(timezone.utc)

        # 待巩固旧记忆（今天到期的 SM-2 复习）
        due = self.get_due_memories(limit=limit)

        # 昨日摘要：昨天创建的记忆
        yesterday = now - timedelta(days=1)
        yesterday_memories = self.get_by_creation_date(yesterday.date())

        # 本周重点：本周创建的重要记忆
        week_start = now - timedelta(days=now.weekday())
        week_memories = self.get_new_memories(days=now.weekday() + 1)

        return {
            "count": len(due),
            "items": [m.to_dict() for m in due],
            "categories": self._categorize(due),
            "yesterday_summary": {
                "count": len(yesterday_memories),
                "items": [m.to_dict() for m in yesterday_memories[:3]],
            },
            "this_week_highlights": {
                "count": len(week_memories),
                "items": [m.to_dict() for m in week_memories[:5]],
            },
            "due_reviews": {
                "count": len(due),
                "items": [m.to_dict() for m in due],
            },
            "generated_at": now.isoformat(),
        }

    # ── 历史上的今天 ──

    def get_on_this_day(self, limit: int = 20) -> dict[str, Any]:
        """获取往年今日的记忆，按年份分组为时间线格式。"""
        now = datetime.now(timezone.utc)
        anniversaries = self.get_by_anniversary(now.month, now.day)

        # 按年份分组
        by_year: dict[int, list[dict[str, Any]]] = {}
        for m in anniversaries:
            try:
                created = datetime.fromisoformat(m.created_at)
                year = created.year
            except (ValueError, TypeError):
                continue
            by_year.setdefault(year, []).append(m.to_dict())

        # 降序排列年份
        timeline = [
            {"year": year, "items": items}
            for year, items in sorted(by_year.items(), reverse=True)
        ]

        return {
            "date": f"{now.month:02d}-{now.day:02d}",
            "total": len(anniversaries),
            "timeline": timeline[:limit],
        }

    # ── 统计 ──

    def get_stats(self) -> dict[str, Any]:
        """获取完整调度统计。"""
        memories = self._load_all()
        if not memories:
            return {
                "total": 0,
                "due_today": 0,
                "avg_easiness": 0.0,
                "avg_repetitions": 0.0,
                "consolidated_count": 0,
                "pending_count": 0,
                "review_streak": 0,
                "total_reviews": 0,
            }

        total = len(memories)
        now = datetime.now(timezone.utc)
        due_count = 0
        total_easiness = 0.0
        consolidated = 0         # 已巩固：repetitions >= 3
        pending = 0               # 待复习：从未评分或重置后从未通过
        total_reviews = 0
        today = now.date()

        for m in memories.values():
            if m.next_review_at:
                try:
                    if datetime.fromisoformat(m.next_review_at) <= now:
                        due_count += 1
                except (ValueError, TypeError):
                    logger.debug("Invalid next_review_at format: %s", m.next_review_at, exc_info=True)
            total_easiness += m.easiness
            total_reviews += m.review_count

            if m.repetitions >= 3:
                consolidated += 1
            if m.repetitions == 0 and m.review_count == 0:
                pending += 1

        streak = self._calculate_review_streak()

        return {
            "total": total,
            "due_today": due_count,
            "avg_easiness": round(total_easiness / total, 2) if total else 0.0,
            "avg_repetitions": round(total_reviews / total, 1) if total else 0.0,
            "consolidated_count": consolidated,
            "pending_count": pending,
            "review_streak": streak,
            "total_reviews": total_reviews,
        }

    def _calculate_review_streak(self) -> int:
        """计算连续复习天数（最大回溯 365 天）。"""
        memories = self._load_all()
        today = datetime.now(timezone.utc).date()
        review_dates: set[date] = set()

        for m in memories.values():
            if not m.last_reviewed_at:
                continue
            try:
                reviewed = datetime.fromisoformat(m.last_reviewed_at).date()
            except (ValueError, TypeError):
                continue
            review_dates.add(reviewed)

        if not review_dates:
            return 0

        streak = 0
        current = today
        max_lookback = 365
        while max_lookback > 0:
            max_lookback -= 1
            if current in review_dates:
                streak += 1
                current -= timedelta(days=1)
            else:
                if streak == 0:
                    current -= timedelta(days=1)
                    continue
                break

        return streak

    # ── 回顾设置 ──

    def load_settings(self) -> ReviewSettings:
        raw = self.data_store.load_json("assistant_memory_schedules_settings", {})
        if isinstance(raw, dict) and raw:
            return ReviewSettings.from_dict(raw)
        return ReviewSettings()

    def save_settings(self, settings: ReviewSettings) -> None:
        self.data_store.save_json("assistant_memory_schedules_settings", settings.to_dict())

    # ── 触发器集成 ──

    def to_review_event(self) -> dict[str, Any]:
        """生成每日回顾事件的 payload（供触发器引擎 / n8n 消费）。"""
        review = self.get_daily_review()
        stats = self.get_stats()
        return {
            "event_type": "daily_review_ready",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "review": {
                "due_count": review.get("count", 0),
                "yesterday_count": review.get("yesterday_summary", {}).get("count", 0),
                "this_week_count": review.get("this_week_highlights", {}).get("count", 0),
            },
            "stats": stats,
        }

    @staticmethod
    def _categorize(due: list[ScheduledMemory]) -> dict[str, int]:
        cats: dict[str, int] = {}
        for m in due:
            cats[m.memory_type] = cats.get(m.memory_type, 0) + 1
        return cats
