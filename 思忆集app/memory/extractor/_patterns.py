"""Pattern extraction from conversation history — PatternExtractor.

Extracted from memory/extractor.py — PatternExtractor, helper functions.
"""
from __future__ import annotations

import re
from datetime import datetime
from typing import Any

import logging

logger = logging.getLogger("siyi.memory.extractor.patterns")


class PatternExtractor:
    """从对话历史中提取行为模式和偏好变化。

    Detects recurring behavioral patterns, preference shifts,
    and interaction style changes from conversation history.

    Usage:
        extractor = PatternExtractor()
        patterns = extractor.extract_patterns(conversations)
        shift = extractor.detect_preference_shift(old_prefs, new_behavior)
    """

    PATTERN_TYPES = {
        "routine": "周期性行为（如每天早起背单词）",
        "preference": "偏好倾向（如喜欢简洁回答）",
        "habit_change": "习惯变化（如从喝茶改为喝咖啡）",
        "skill_progress": "技能进展（如 Rust 学习阶段变化）",
        "interaction_style": "交互风格（如越来越依赖快捷指令）",
        "topic_interest": "话题兴趣（如对 AI 安全的关注度变化）",
    }

    def extract_patterns(
        self,
        conversations: list[dict[str, Any]],
        min_confidence: float = 0.3,
    ) -> list[dict[str, Any]]:
        """从对话历史中提取行为模式和偏好。"""
        if not conversations:
            return []

        patterns: list[dict[str, Any]] = []
        patterns.extend(self._detect_routines(conversations))
        patterns.extend(self._detect_preferences(conversations))
        patterns.extend(self._detect_changes(conversations))
        patterns.extend(self._detect_skill_progress(conversations))
        patterns.extend(self._detect_interaction_style(conversations))

        filtered = [
            p for p in patterns
            if float(p.get("confidence", 0)) >= min_confidence
        ]
        filtered.sort(key=lambda p: float(p.get("confidence", 0)), reverse=True)
        return filtered

    @staticmethod
    def _detect_routines(conversations: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """检测周期性行为模式。"""
        patterns: list[dict[str, Any]] = []
        time_buckets: dict[str, list[str]] = {}

        for msg in conversations:
            role = str(msg.get("role", "")).lower()
            if role not in ("user",):
                continue
            content = str(msg.get("content", ""))
            timestamp = str(msg.get("timestamp", ""))

            time_key = _extract_time_bucket(timestamp)
            if time_key:
                if time_key not in time_buckets:
                    time_buckets[time_key] = []
                time_buckets[time_key].append(content)

        routine_keywords = {
            "早起": "morning_routine",
            "晚安": "night_routine",
            "打卡": "daily_checkin",
            "背单词": "study_routine",
            "健身": "exercise_routine",
        }

        for time_key, messages in time_buckets.items():
            if len(messages) < 2:
                continue
            combined = " ".join(messages).lower()
            for keyword, routine_type in routine_keywords.items():
                if keyword in combined:
                    patterns.append({
                        "type": "routine",
                        "pattern": routine_type,
                        "confidence": min(0.6, 0.3 + len(messages) * 0.1),
                        "evidence": [f"关键词'{keyword}'在时段{time_key}出现{len(messages)}次"],
                        "tags": ["routine", time_key, routine_type],
                    })

        return patterns

    @staticmethod
    def _detect_preferences(conversations: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """检测偏好倾向。"""
        patterns: list[dict[str, Any]] = []

        preference_indicators = [
            (r"我喜欢|我更喜欢|我比较喜欢|我更习惯", "positive_preference", "喜好"),
            (r"我不喜欢|我讨厌|我不要|我不想", "negative_preference", "厌恶"),
            (r"帮我|请帮我|麻烦你|请", "delegation_style", "委托倾向"),
            (r"简短|简洁|简单|一句话", "brevity_preference", "简洁偏好"),
            (r"详细|详细说明|具体点|展开", "detail_preference", "详细偏好"),
            (r"用英文|用中文|用[英中]文回答", "language_preference", "语言偏好"),
            (r"不需要|不用了|别|别了", "rejection_style", "拒绝倾向"),
        ]

        combined = " ".join(
            str(msg.get("content", ""))
            for msg in conversations
            if str(msg.get("role", "")).lower() == "user"
        )

        for pattern, pref_type, label in preference_indicators:
            if re.search(pattern, combined):
                patterns.append({
                    "type": "preference",
                    "pattern": pref_type,
                    "confidence": 0.6,
                    "evidence": [f"检测到'{label}'表达"],
                    "tags": ["preference", pref_type],
                })

        return patterns

    @staticmethod
    def _detect_changes(conversations: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """检测习惯变化或话题兴趣变化。"""
        patterns: list[dict[str, Any]] = []
        if not conversations:
            return patterns

        mid = len(conversations) // 2
        early = conversations[:mid]
        late = conversations[mid:]

        early_text = " ".join(str(m.get("content", "")) for m in early if str(m.get("role", "")).lower() == "user").lower()
        late_text = " ".join(str(m.get("content", "")) for m in late if str(m.get("role", "")).lower() == "user").lower()

        if not early_text or not late_text:
            return patterns

        early_tokens = set(early_text.split())
        late_tokens = set(late_text.split())

        dropped = early_tokens - late_tokens
        significant_dropped = [t for t in dropped if len(t) > 2 and early_text.count(t) >= 3]
        for topic in significant_dropped[:3]:
            patterns.append({
                "type": "habit_change",
                "pattern": "topic_dropped",
                "confidence": 0.5,
                "evidence": [f"早期频繁提及'{topic}'（{early_text.count(topic)}次），近期未出现"],
                "tags": ["change", "drop", topic],
            })

        added = late_tokens - early_tokens
        significant_added = [t for t in added if len(t) > 2 and late_text.count(t) >= 3]
        for topic in significant_added[:3]:
            patterns.append({
                "type": "topic_interest",
                "pattern": "new_interest",
                "confidence": 0.5,
                "evidence": [f"近期频繁提及'{topic}'（{late_text.count(topic)}次），早期未出现"],
                "tags": ["interest", "new", topic],
            })

        return patterns

    @staticmethod
    def _detect_skill_progress(conversations: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """检测技能进展。"""
        patterns: list[dict[str, Any]] = []

        skill_patterns = [
            (r"学(?:习|了|完|会)(?:了)?\s*(\S+)", "learning"),
            (r"(?:开始|正在|在学)\s*(\S+)", "learning_started"),
            (r"(?:学会了|掌握了|完成了|搞定了)\s*(\S+)", "skill_mastered"),
            (r"(?:放弃了|暂停了|停了)\s*(\S+)", "skill_paused"),
        ]

        for msg in conversations:
            content = str(msg.get("content", ""))
            role = str(msg.get("role", "")).lower()
            if role not in ("user", "ai"):
                continue

            for pattern, progress_type in skill_patterns:
                matches = re.findall(pattern, content)
                for match in matches:
                    skill_name = match.strip()
                    if len(skill_name) >= 2:
                        patterns.append({
                            "type": "skill_progress",
                            "pattern": progress_type,
                            "skill": skill_name,
                            "confidence": 0.6,
                            "evidence": [f"{role}: '{content[:60]}'"],
                            "tags": ["skill", progress_type, skill_name],
                        })

        seen: set[str] = set()
        unique: list[dict[str, Any]] = []
        for p in patterns:
            key = f"{p.get('pattern')}:{p.get('skill')}"
            if key not in seen:
                seen.add(key)
                unique.append(p)

        return unique

    @staticmethod
    def _detect_interaction_style(conversations: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """检测交互风格变化。"""
        patterns: list[dict[str, Any]] = []
        if not conversations:
            return patterns

        user_msgs = [
            str(m.get("content", ""))
            for m in conversations
            if str(m.get("role", "")).lower() == "user"
        ]
        if len(user_msgs) < 5:
            return []

        mid = len(user_msgs) // 2
        early_avg = sum(len(m) for m in user_msgs[:mid]) / max(1, mid)
        late_avg = sum(len(m) for m in user_msgs[mid:]) / max(1, len(user_msgs) - mid)

        if late_avg < early_avg * 0.7:
            patterns.append({
                "type": "interaction_style",
                "pattern": "increasingly_concise",
                "confidence": 0.5,
                "evidence": [f"用户消息平均长度从{early_avg:.0f}字降至{late_avg:.0f}字"],
                "tags": ["style", "concise"],
            })
        elif late_avg > early_avg * 1.5:
            patterns.append({
                "type": "interaction_style",
                "pattern": "increasingly_detailed",
                "confidence": 0.5,
                "evidence": [f"用户消息平均长度从{early_avg:.0f}字增至{late_avg:.0f}字"],
                "tags": ["style", "detailed"],
            })

        cmd_pattern = re.compile(r"(?:/|#|!)\w+")
        early_commands = sum(1 for m in user_msgs[:mid] if cmd_pattern.search(m))
        late_commands = sum(1 for m in user_msgs[mid:] if cmd_pattern.search(m))
        if late_commands > early_commands and late_commands >= 3:
            patterns.append({
                "type": "interaction_style",
                "pattern": "increasing_command_usage",
                "confidence": 0.6,
                "evidence": [f"快捷指令使用从{early_commands}次增至{late_commands}次"],
                "tags": ["style", "command"],
            })

        return patterns

    @staticmethod
    def detect_preference_shift(
        old_prefs: dict[str, Any],
        new_behavior: dict[str, Any],
    ) -> dict[str, Any] | None:
        """检测用户偏好是否发生变化。"""
        if not old_prefs:
            return {"shift_detected": False, "reason": "无历史偏好数据"}

        shifts: list[dict[str, Any]] = []

        for key, old_value in old_prefs.items():
            new_value = new_behavior.get(key)
            if new_value is None:
                continue

            old_str = str(old_value).lower().strip()
            new_str = str(new_value).lower().strip()

            if old_str != new_str and old_str and new_str:
                if _is_significant_change(key, old_str, new_str):
                    shifts.append({
                        "key": key,
                        "from": old_value,
                        "to": new_value,
                        "confidence": _compute_shift_confidence(key, old_str, new_str),
                    })

        if not shifts:
            return {"shift_detected": False, "reason": "未检测到显著变化"}

        return {
            "shift_detected": True,
            "shifts": shifts,
            "summary": "检测到 {} 项偏好变化".format(len(shifts)),
        }


# ── Helpers ──────────────────────────────────────────────────────────


def _extract_time_bucket(timestamp: str) -> str:
    """从时间戳提取时段标识（morning/afternoon/evening/night）。"""
    if not timestamp:
        return ""
    try:
        if "T" in timestamp:
            # ★ 修复时区剥离 bug：原 .split("+")[0] 会剥掉时区偏移，
            # 导致 UTC 时间戳（如 "02:00:00Z"=北京时间 10:00）取到
            # 错误的 hour=2 → 归类为 "night"，实际应是 "morning"。
            # 修正：保留时区并转换为本地时间再取 hour。
            dt = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
            if dt.tzinfo is not None:
                dt = dt.astimezone()  # 转为系统本地时区
        else:
            for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
                try:
                    dt = datetime.strptime(timestamp[:len(fmt)], fmt)
                    break
                except ValueError:
                    continue
            else:
                return ""

        hour = dt.hour
        if 5 <= hour < 12:
            return "morning"
        elif 12 <= hour < 14:
            return "noon"
        elif 14 <= hour < 18:
            return "afternoon"
        elif 18 <= hour < 22:
            return "evening"
        else:
            return "night"
    except Exception:
        return ""


def _is_significant_change(key: str, old: str, new: str) -> bool:
    """判断某个偏好变化是否显著。"""
    if old in ("true", "yes", "on", "开启", "启用") and new in ("false", "no", "off", "关闭", "禁用"):
        return True
    if new in ("true", "yes", "on", "开启", "启用") and old in ("false", "no", "off", "关闭", "禁用"):
        return True

    if "language" in key.lower() or "语言" in key:
        return old != new

    if "style" in key.lower() or "模式" in key or "mode" in key.lower():
        return old != new

    old_tokens = set(old.split())
    new_tokens = set(new.split())
    if not old_tokens or not new_tokens:
        return False
    overlap = old_tokens & new_tokens
    overlap_ratio = len(overlap) / max(len(old_tokens), len(new_tokens))
    return overlap_ratio < 0.5


def _compute_shift_confidence(key: str, old: str, new: str) -> float:
    """计算偏好变化的置信度。"""
    base = 0.6

    if "language" in key.lower() or "语言" in key:
        base += 0.2

    if _is_significant_change(key, old, new):
        base += 0.15

    if len(new) < 10 and len(old) > 10:
        base += 0.05

    return min(1.0, base)


__all__ = [
    "PatternExtractor",
    "_extract_time_bucket",
    "_is_significant_change",
    "_compute_shift_confidence",
]
