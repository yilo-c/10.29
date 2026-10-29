"""智能提醒系统 — 基于用户行为模式推荐最佳提醒时间。

功能：
1. 分析用户历史任务完成时间模式
2. 根据任务类型和优先级推荐提醒时间
3. 学习用户偏好，动态调整提醒策略
4. 支持自然语言时间解析（如"明天下午"、"周末"）
5. 分析内容紧急程度，智能调整提醒时间
6. 识别重复任务模式
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Optional
from collections import defaultdict

logger = logging.getLogger("siyi.smart_reminder")


# ── 紧急程度关键词配置 ──────────────────────────────

URGENT_KEYWORDS = [
    "紧急", "立即", "马上", "ASAP", "立刻", "赶快",
    "deadline", "截止", "到期", "urgent", "immediately",
    "火急", "刻不容缓", "迫在眉睫", "十万火急",
]

HIGH_PRIORITY_KEYWORDS = [
    "重要", "必须", "应该", "需要", "务必", "一定",
    "关键", "核心", "主要", "important", "must", "should",
    "需要完成", "不能拖延",
]

MEDIUM_PRIORITY_KEYWORDS = [
    "建议", "最好", "尽量", "可以", "有空",
    "suggest", "better", "when possible",
]


@dataclass
class TimePattern:
    """用户时间模式。"""
    hour: int  # 0-23
    day_of_week: int  # 0-6, 0=Monday
    completion_rate: float  # 0-1
    avg_duration_minutes: int = 30


@dataclass
class ReminderSuggestion:
    """提醒建议。"""
    suggested_time: datetime
    confidence: float  # 0-1
    reason: str
    alternative_times: list[datetime] = field(default_factory=list)
    
    def to_dict(self) -> dict[str, Any]:
        return {
            "suggested_time": self.suggested_time.isoformat(),
            "confidence": self.confidence,
            "reason": self.reason,
            "alternative_times": [t.isoformat() for t in self.alternative_times],
        }


@dataclass
class TaskRecord:
    """任务记录（用于学习）。"""
    task_type: str
    priority: int
    created_at: datetime
    completed_at: Optional[datetime] = None
    due_date: Optional[datetime] = None
    duration_minutes: int = 0


class SmartReminder:
    """智能提醒系统。
    
    用法：
        reminder = SmartReminder()
        
        # 记录任务完成（用于学习）
        reminder.record_task_completion(task_record)
        
        # 获取提醒建议
        suggestion = reminder.suggest_reminder_time(
            task_type="meeting",
            priority=80,
            user_hint="明天下午"
        )
    """
    
    # 任务类型到默认时长的映射
    DEFAULT_DURATIONS = {
        "meeting": 60,
        "review": 30,
        "coding": 120,
        "writing": 90,
        "email": 15,
        "call": 30,
        "task": 30,
        "default": 30,
    }
    
    # 优先级到提前时间的映射（分钟）
    PRIORITY_LEAD_TIME = {
        100: 5,     # 紧急：提前5分钟
        80: 15,     # 高：提前15分钟
        60: 30,     # 中：提前30分钟
        40: 60,     # 低：提前1小时
        20: 180,    # 很低：提前3小时
        0: 1440,    # 无优先级：提前1天
    }
    
    def __init__(self):
        self._task_history: list[TaskRecord] = []
        self._time_patterns: dict[str, list[TimePattern]] = defaultdict(list)
        self._user_preferences: dict[str, Any] = {}
        
        # 默认工作时间
        self._work_hours = (9, 18)  # 9:00 - 18:00
        self._work_days = (0, 1, 2, 3, 4)  # 周一到周五
    
    def record_task_completion(self, record: TaskRecord) -> None:
        """记录任务完成（用于学习用户模式）。"""
        self._task_history.append(record)
        
        # 更新时间模式
        if record.completed_at:
            hour = record.completed_at.hour
            dow = record.completed_at.weekday()
            
            # 查找或创建模式
            patterns = self._time_patterns[record.task_type]
            found = False
            for p in patterns:
                if p.hour == hour and p.day_of_week == dow:
                    # 更新现有模式
                    total = p.completion_rate + 1
                    p.completion_rate = (p.completion_rate * (total - 1) + 1) / total
                    p.avg_duration_minutes = int(
                        (p.avg_duration_minutes * (total - 1) + record.duration_minutes) / total
                    )
                    found = True
                    break
            
            if not found:
                patterns.append(TimePattern(
                    hour=hour,
                    day_of_week=dow,
                    completion_rate=1.0,
                    avg_duration_minutes=record.duration_minutes or 30,
                ))
        
        logger.debug(
            "Recorded task completion: type=%s, duration=%d min",
            record.task_type,
            record.duration_minutes,
        )
    
    def suggest_reminder_time(
        self,
        task_type: str = "default",
        priority: int = 50,
        user_hint: str = "",
        reference_time: Optional[datetime] = None,
    ) -> ReminderSuggestion:
        """基于用户模式推荐提醒时间。
        
        Args:
            task_type: 任务类型（meeting, review, coding 等）
            priority: 优先级 0-100
            user_hint: 用户时间提示（如"明天下午"、"周末"）
            reference_time: 参考时间（默认当前时间）
            
        Returns:
            ReminderSuggestion 包含推荐时间和置信度
        """
        now = reference_time or datetime.now()
        
        # 1. 解析用户提示
        if user_hint:
            parsed_time = self._parse_time_hint(user_hint, now)
            if parsed_time:
                return ReminderSuggestion(
                    suggested_time=parsed_time,
                    confidence=0.9,
                    reason=f"根据用户提示「{user_hint}」",
                    alternative_times=self._get_alternatives(parsed_time, task_type),
                )
        
        # 2. 基于历史模式推荐
        pattern_suggestion = self._suggest_from_patterns(task_type, priority, now)
        if pattern_suggestion:
            return pattern_suggestion
        
        # 3. 基于优先级推荐
        return self._suggest_from_priority(priority, now)
    
    def _parse_time_hint(self, hint: str, now: datetime) -> Optional[datetime]:
        """解析自然语言时间提示。"""
        hint = hint.strip().lower()
        
        # 今天
        if "今天" in hint or "今日" in hint:
            return self._parse_time_of_day(hint, now.date())
        
        # 明天
        if "明天" in hint or "明日" in hint:
            tomorrow = now.date() + timedelta(days=1)
            return self._parse_time_of_day(hint, tomorrow)
        
        # 后天
        if "后天" in hint:
            day_after = now.date() + timedelta(days=2)
            return self._parse_time_of_day(hint, day_after)
        
        # 周末
        if "周末" in hint or "周六" in hint or "周日" in hint:
            days_until_saturday = (5 - now.weekday()) % 7
            if days_until_saturday == 0:
                days_until_saturday = 7
            saturday = now.date() + timedelta(days=days_until_saturday)
            return self._parse_time_of_day(hint, saturday)
        
        # 下周
        if "下周" in hint:
            next_week = now.date() + timedelta(days=7)
            return self._parse_time_of_day(hint, next_week)
        
        # X小时后
        match = re.search(r"(\d+)\s*(?:小时|个小时|h)", hint)
        if match:
            hours = int(match.group(1))
            return now + timedelta(hours=hours)
        
        # X分钟后
        match = re.search(r"(\d+)\s*(?:分钟|分|min)", hint)
        if match:
            minutes = int(match.group(1))
            return now + timedelta(minutes=minutes)
        
        # 具体时间（如"下午3点"、"15:00"）
        match = re.search(r"(?:下午|晚上)?\s*(\d{1,2})\s*[点时:：]\s*(\d{0,2})", hint)
        if match:
            hour = int(match.group(1))
            minute = int(match.group(2)) if match.group(2) else 0
            if "下午" in hint or "晚上" in hint:
                if hour < 12:
                    hour += 12
            target_date = now.date()
            result = datetime.combine(target_date, datetime.min.time()).replace(
                hour=hour, minute=minute
            )
            if result < now:
                result += timedelta(days=1)
            return result
        
        return None
    
    def _parse_time_of_day(self, hint: str, target_date) -> datetime:
        """解析一天中的时间。"""
        now = datetime.now()
        
        # 默认工作时间
        hour = 14  # 默认下午2点
        
        if "早上" in hint or "上午" in hint:
            hour = 9
        elif "中午" in hint:
            hour = 12
        elif "下午" in hint:
            hour = 14
        elif "晚上" in hint or "傍晚" in hint:
            hour = 18
        
        result = datetime.combine(target_date, datetime.min.time()).replace(hour=hour)
        
        # 如果时间已过，调整到下一天
        if result < now:
            result += timedelta(days=1)
        
        return result
    
    def _suggest_from_patterns(
        self, task_type: str, priority: int, now: datetime
    ) -> Optional[ReminderSuggestion]:
        """基于历史模式推荐。"""
        patterns = self._time_patterns.get(task_type, [])
        if not patterns:
            return None
        
        # 找到最匹配当前时间的模式
        current_hour = now.hour
        current_dow = now.weekday()
        
        best_pattern = None
        best_score: float = -1
        
        for p in patterns:
            # 计算匹配分数
            hour_diff = abs(p.hour - current_hour)
            dow_diff = abs(p.day_of_week - current_dow)
            score = p.completion_rate * (1 / (1 + hour_diff)) * (1 / (1 + dow_diff))
            
            if score > best_score:
                best_score = score
                best_pattern = p
        
        if best_pattern and best_score > 0.3:
            # 基于模式推荐
            suggested_time = now.replace(
                hour=best_pattern.hour,
                minute=0,
                second=0,
                microsecond=0,
            )
            if suggested_time < now:
                suggested_time += timedelta(days=1)
            
            # 调整到工作时间
            suggested_time = self._adjust_to_work_hours(suggested_time)
            
            return ReminderSuggestion(
                suggested_time=suggested_time,
                confidence=best_score,
                reason=f"基于你过去在{best_pattern.hour}点完成{task_type}任务的习惯",
                alternative_times=self._get_alternatives(suggested_time, task_type),
            )
        
        return None
    
    def _suggest_from_priority(self, priority: int, now: datetime) -> ReminderSuggestion:
        """基于优先级推荐。"""
        # 获取提前时间
        lead_minutes = 30  # 默认
        for threshold, minutes in sorted(self.PRIORITY_LEAD_TIME.items(), reverse=True):
            if priority >= threshold:
                lead_minutes = minutes
                break
        
        # 建议时间 = 现在 + 提前时间
        suggested_time = now + timedelta(minutes=lead_minutes)
        
        # 调整到工作时间
        suggested_time = self._adjust_to_work_hours(suggested_time)
        
        priority_label = "紧急" if priority >= 80 else "高" if priority >= 60 else "中" if priority >= 40 else "低"
        
        return ReminderSuggestion(
            suggested_time=suggested_time,
            confidence=0.5,
            reason=f"{priority_label}优先级任务，建议提前{lead_minutes}分钟提醒",
            alternative_times=self._get_alternatives(suggested_time, "default"),
        )
    
    def _adjust_to_work_hours(self, dt: datetime) -> datetime:
        """调整到工作时间内。"""
        # 如果是周末，调整到周一
        if dt.weekday() not in self._work_days:
            days_until_monday = (7 - dt.weekday()) % 7
            if days_until_monday == 0:
                days_until_monday = 7
            dt = dt + timedelta(days=days_until_monday)
            dt = dt.replace(hour=self._work_hours[0], minute=0, second=0, microsecond=0)
        
        # 如果在工作时间之外，调整到下一个工作时间
        if dt.hour < self._work_hours[0]:
            dt = dt.replace(hour=self._work_hours[0], minute=0, second=0, microsecond=0)
        elif dt.hour >= self._work_hours[1]:
            dt = dt + timedelta(days=1)
            dt = dt.replace(hour=self._work_hours[0], minute=0, second=0, microsecond=0)
        
        return dt
    
    def _get_alternatives(self, base_time: datetime, task_type: str) -> list[datetime]:
        """获取备选时间。"""
        alternatives = []
        
        # 前后1小时
        alternatives.append(base_time - timedelta(hours=1))
        alternatives.append(base_time + timedelta(hours=1))
        
        # 同一时间的明天
        alternatives.append(base_time + timedelta(days=1))
        
        # 过滤掉无效时间
        valid = []
        for t in alternatives:
            t = self._adjust_to_work_hours(t)
            if t not in valid:
                valid.append(t)
        
        return valid[:3]
    
    def set_work_hours(self, start: int, end: int) -> None:
        """设置工作时间。"""
        self._work_hours = (start, end)
    
    def set_work_days(self, days: list[int]) -> None:
        """设置工作日（0=周一，6=周日）。"""
        self._work_days = tuple(days)
    
    def get_learning_stats(self) -> dict[str, Any]:
        """获取学习统计。"""
        return {
            "total_tasks_recorded": len(self._task_history),
            "task_types": list(self._time_patterns.keys()),
            "patterns_count": sum(len(p) for p in self._time_patterns.values()),
        }
    
    # ── 内容紧急程度分析 ──────────────────────────────
    
    def analyze_urgency(self, content: str) -> float:
        """分析内容紧急程度 (0-1)。
        
        通过关键词匹配评估任务的紧急程度：
        - 0.0-0.3: 低紧急度（可以延后处理）
        - 0.3-0.6: 中等紧急度（正常优先级）
        - 0.6-0.8: 高紧急度（需要尽快处理）
        - 0.8-1.0: 紧急（需要立即处理）
        
        Args:
            content: 任务内容文本
            
        Returns:
            紧急程度分数 0-1
        """
        if not content:
            return 0.0
        
        content_lower = content.lower()
        score = 0.0
        
        # 检查紧急关键词（权重最高）
        for keyword in URGENT_KEYWORDS:
            if keyword.lower() in content_lower:
                score += 0.25
        
        # 检查高优先级关键词
        for keyword in HIGH_PRIORITY_KEYWORDS:
            if keyword.lower() in content_lower:
                score += 0.1
        
        # 检查中等优先级关键词（轻微加分）
        for keyword in MEDIUM_PRIORITY_KEYWORDS:
            if keyword.lower() in content_lower:
                score += 0.05
        
        # 检查时间压力指标
        time_pressure_patterns = [
            (r"\d+\s*(?:小时|h|hour).*内", 0.15),      # "X小时内"
            (r"今天|今日|today", 0.1),                  # 今天完成
            (r"明天|明日|tomorrow", 0.05),              # 明天完成
            (r"本周|这周|this week", 0.03),             # 本周完成
        ]
        
        for pattern, weight in time_pressure_patterns:
            if re.search(pattern, content_lower):
                score += weight
        
        # 检查感叹号和强调标记
        if "！" in content or "!" in content:
            score += 0.05
        if "???" in content or "？？？" in content:
            score += 0.03
        
        return min(score, 1.0)
    
    def suggest_smart_time(
        self,
        content: str,
        task_type: str = "default",
        user_hint: str = "",
        reference_time: Optional[datetime] = None,
    ) -> ReminderSuggestion:
        """基于内容紧急程度智能推荐提醒时间。
        
        结合内容分析和用户提示，提供更智能的提醒建议。
        
        Args:
            content: 任务内容文本
            task_type: 任务类型
            user_hint: 用户时间提示
            reference_time: 参考时间
            
        Returns:
            ReminderSuggestion 包含推荐时间和紧急程度分析
        """
        now = reference_time or datetime.now()
        
        # 分析紧急程度
        urgency = self.analyze_urgency(content)
        
        # 根据紧急程度调整建议策略
        if urgency >= 0.7:
            # 紧急任务：建议30分钟内
            return self._suggest_urgent(urgency, now, content)
        elif urgency >= 0.4:
            # 较紧急：建议2小时内
            return self._suggest_soon(urgency, now, content)
        else:
            # 普通任务：使用原有逻辑
            suggestion = self.suggest_reminder_time(
                task_type=task_type,
                priority=int(urgency * 100),
                user_hint=user_hint,
                reference_time=now,
            )
            # 添加紧急程度信息
            suggestion.reason = f"[紧急度: {urgency:.0%}] {suggestion.reason}"
            return suggestion
    
    def _suggest_urgent(
        self, urgency: float, now: datetime, content: str
    ) -> ReminderSuggestion:
        """紧急任务建议：30分钟内提醒。"""
        # 根据紧急程度计算提前时间
        if urgency >= 0.9:
            lead_minutes = 5   # 非常紧急：5分钟后
        elif urgency >= 0.8:
            lead_minutes = 15  # 很紧急：15分钟后
        else:
            lead_minutes = 30  # 紧急：30分钟后
        
        suggested_time = now + timedelta(minutes=lead_minutes)
        
        return ReminderSuggestion(
            suggested_time=suggested_time,
            confidence=urgency,
            reason=f"[紧急度: {urgency:.0%}] 检测到紧急关键词，建议{lead_minutes}分钟后提醒",
            alternative_times=[
                now + timedelta(minutes=lead_minutes + 15),
                now + timedelta(minutes=lead_minutes + 30),
            ],
        )
    
    def _suggest_soon(
        self, urgency: float, now: datetime, content: str
    ) -> ReminderSuggestion:
        """较紧急任务建议：2小时内提醒。"""
        # 根据紧急程度计算提前时间
        if urgency >= 0.6:
            lead_minutes = 60   # 较紧急：1小时后
        elif urgency >= 0.5:
            lead_minutes = 90   # 中等：1.5小时后
        else:
            lead_minutes = 120  # 一般：2小时后
        
        suggested_time = now + timedelta(minutes=lead_minutes)
        
        # 调整到工作时间
        suggested_time = self._adjust_to_work_hours(suggested_time)
        
        return ReminderSuggestion(
            suggested_time=suggested_time,
            confidence=urgency,
            reason=f"[紧急度: {urgency:.0%}] 检测到较高优先级，建议{lead_minutes}分钟后提醒",
            alternative_times=self._get_alternatives(suggested_time, "default"),
        )
    
    # ── 重复模式学习 ──────────────────────────────
    
    def learn_recurring_pattern(self, task_type: str) -> dict[str, Any]:
        """识别周期性任务模式。
        
        分析特定类型任务的历史完成时间，识别周期性模式。
        
        Args:
            task_type: 任务类型
            
        Returns:
            包含模式信息的字典
        """
        # 筛选特定类型的已完成任务
        records = [
            r for r in self._task_history
            if r.task_type == task_type and r.completed_at is not None
        ]
        
        if len(records) < 3:
            return {
                "pattern_type": "insufficient_data",
                "message": f"需要至少3条{task_type}类型的任务记录才能识别模式",
            }
        
        # 按星期几分组统计
        weekday_counts: dict[int, int] = defaultdict(int)
        hour_counts: dict[int, int] = defaultdict(int)
        
        for record in records:
            completed = record.completed_at
            if completed is None:
                continue
            weekday_counts[completed.weekday()] += 1
            hour_counts[completed.hour] += 1
        
        # 识别主要模式
        primary_weekday = max(weekday_counts, key=lambda k: weekday_counts[k])
        primary_hour = max(hour_counts, key=lambda k: hour_counts[k])
        
        # 计算置信度
        weekday_confidence = weekday_counts[primary_weekday] / len(records)
        hour_confidence = hour_counts[primary_hour] / len(records)
        
        weekday_names = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
        
        result = {
            "pattern_type": "weekly",
            "primary_weekday": primary_weekday,
            "primary_weekday_name": weekday_names[primary_weekday],
            "primary_hour": primary_hour,
            "weekday_confidence": weekday_confidence,
            "hour_confidence": hour_confidence,
            "sample_size": len(records),
        }
        
        # 判断是否为强模式
        if weekday_confidence >= 0.6 and hour_confidence >= 0.4:
            result["is_strong_pattern"] = True
            result["suggestion"] = (
                f"你通常在{weekday_names[primary_weekday]}"
                f"{primary_hour}点左右完成{task_type}任务"
            )
        else:
            result["is_strong_pattern"] = False
            result["suggestion"] = f"{task_type}任务没有明显的周期性模式"
        
        return result


# 全局实例
_smart_reminder: SmartReminder | None = None


def get_smart_reminder() -> SmartReminder:
    """获取全局智能提醒实例。"""
    global _smart_reminder
    if _smart_reminder is None:
        _smart_reminder = SmartReminder()
    return _smart_reminder