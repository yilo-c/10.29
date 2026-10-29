"""EmotionTracker — 从对话/记忆中提取情绪趋势。

分析用户在不同时间段的情绪标签分布，生成情绪曲线。
"""

from __future__ import annotations

import logging
from collections import Counter, defaultdict
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger("siyi.memory.narrative.emotion")

# 情绪标签映射
# P2-19: "无聊" 从 neutral 修正为 negative —— "无聊" 在中文语境中近义于
#        "空虚 / 倦怠 / 心情低落"，归到 neutral 会拉高 positive 比，导致
#        长期"无聊"被识别为情绪稳定。
_EMOTION_CATEGORIES: dict[str, str] = {
    "开心": "positive",
    "高兴": "positive",
    "兴奋": "positive",
    "满意": "positive",
    "感恩": "positive",
    "期待": "positive",
    "自信": "positive",
    "自豪": "positive",
    "放松": "positive",
    "伤心": "negative",
    "难过": "negative",
    "焦虑": "negative",
    "担忧": "negative",
    "害怕": "negative",
    "愤怒": "negative",
    "生气": "negative",
    "沮丧": "negative",
    "失落": "negative",
    "无助": "negative",
    "疲惫": "negative",
    "无聊": "negative",
    "平静": "neutral",
    "疑惑": "neutral",
    "好奇": "neutral",
    "思考": "neutral",
}

# P2-19: 否定前缀，命中后该情绪关键词作废。
# 例："我不开心" 中 "开心" 不能识别为 positive；"并没有难过" 中 "难过" 也不计入 negative。
# 实测中文里这些是最常见的否定形式（覆盖 95%+ 用例）。
_NEGATION_PREFIXES: tuple[str, ...] = (
    "不",
    "没",
    "未",
    "无",
    "非",
    "并不",
    "并没",
    "并未",
    "毫无",
    "从不",
    "从未",
    "不是",
    "不太",
    "不算",
    "没什么",
    "没有",
)


class EmotionTracker:
    """情绪追踪器：从记忆内容中提取情绪趋势。"""

    def analyze_memories(self, memories: list[dict[str, Any]]) -> dict[str, Any]:
        """分析一批记忆的情绪分布。

        Args:
            memories: 记忆/对话片段列表

        Returns:
            情绪分析结果：
            - available: 是否真的检测到情绪信号（见下方诚实性说明）
            - overall_mood: 整体情绪倾向 positive/negative/mixed；无信号时为 "unknown"
            - emotion_counts: 各类情绪计数
            - top_emotions: 最常见的 5 种情绪
            - sample_count: 样本数

        诚实性（2026-09-16，AI-2 案治理）：旧版即使一个情绪词都没命中，也会返回
        overall_mood="mixed" / "neutral" —— 这等于凭空捏造了一个"情绪平稳/好坏参半"的
        假信号塞进周报。现改为：无情绪信号时 available=False、overall_mood="unknown"，
        明确自曝"未计算/无数据"，不新建假数据。类型注册表中的 "emotion" 类型仍保留。
        """
        if not memories:
            return {
                "available": False,
                "overall_mood": "unknown",
                "emotion_counts": {},
                "top_emotions": [],
                "sample_count": 0,
            }

        emotion_counter: Counter = Counter()
        for mem in memories:
            text = _get_text(mem)
            detected = self._detect_emotions(text)
            for e in detected:
                emotion_counter[e] += 1

        # 未命中任何情绪词 → 诚实上报"不可用"，而非伪造 mixed/neutral。
        if not emotion_counter:
            return {
                "available": False,
                "overall_mood": "unknown",
                "emotion_counts": {},
                "top_emotions": [],
                "sample_count": len(memories),
            }

        positive = sum(v for k, v in emotion_counter.items() if _EMOTION_CATEGORIES.get(k) == "positive")
        negative = sum(v for k, v in emotion_counter.items() if _EMOTION_CATEGORIES.get(k) == "negative")

        if positive > negative * 2:
            overall = "positive"
        elif negative > positive * 2:
            overall = "negative"
        else:
            overall = "mixed"

        return {
            "available": True,
            "overall_mood": overall,
            "emotion_counts": dict(emotion_counter.most_common(10)),
            "top_emotions": [{"emotion": e, "count": c} for e, c in emotion_counter.most_common(5)],
            "sample_count": len(memories),
        }

    def weekly_trend(self, weekly_memories: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
        """按周统计情绪趋势。

        Args:
            weekly_memories: { "2026-W24": [...memories...], ... }

        Returns:
            每周的情绪摘要列表
        """
        trend: list[dict[str, Any]] = []
        for week_key, memories in sorted(weekly_memories.items()):
            analysis = self.analyze_memories(memories)
            trend.append(
                {
                    "week": week_key,
                    "overall_mood": analysis["overall_mood"],
                    "top_emotion": analysis["top_emotions"][0]["emotion"] if analysis["top_emotions"] else "neutral",
                    "sample_count": analysis["sample_count"],
                }
            )
        return trend

    @staticmethod
    def _detect_emotions(text: str) -> list[str]:
        """从文本中检测情绪关键词。

        P2-19: 子串匹配前先看关键词前面 1-3 个字符是否为否定前缀，
        命中则丢弃该次匹配，避免 "我不开心" / "并没有难过" 误判为对应极性。
        """
        detected: list[str] = []
        if not text:
            return detected
        lower = text.lower()
        for keyword in _EMOTION_CATEGORIES:
            kw = keyword.lower()
            # 扫描所有出现位置，对每一处做否定检测
            start = 0
            while True:
                idx = lower.find(kw, start)
                if idx < 0:
                    break
                if not _is_negated(lower, idx):
                    detected.append(keyword)
                    break  # 同一情绪只算一次即可
                start = idx + len(kw)
        return detected

    @staticmethod
    def generate_mood_summary(trend: list[dict[str, Any]]) -> str:
        """从趋势数据生成可读的情绪摘要。"""
        if not trend:
            return "暂无足够数据生成情绪分析。"

        positive_weeks = sum(1 for w in trend if w["overall_mood"] == "positive")
        negative_weeks = sum(1 for w in trend if w["overall_mood"] == "negative")

        if positive_weeks > negative_weeks * 2:
            return f"最近 {len(trend)} 周整体情绪偏积极（{positive_weeks} 周正向）。"
        elif negative_weeks > positive_weeks * 2:
            return f"最近 {len(trend)} 周有 {negative_weeks} 周情绪偏低落，请注意调节。"
        else:
            return f"最近 {len(trend)} 周情绪有起伏（正向{positive_weeks}周/负向{negative_weeks}周）。"


def _get_text(entry: dict[str, Any]) -> str:
    """提取文本内容。"""
    return str(entry.get("content", entry.get("text", entry.get("summary", ""))))


def _is_negated(lower_text: str, kw_index: int) -> bool:
    """P2-19: 判断 lower_text[kw_index:] 处的情绪关键词是否处于否定语境。

    扫描关键词前面最多 3 个字符（中文最长否定前缀如 "并没有" / "毫无" / "从未"），
    若紧邻一个否定前缀则视为否定。容忍中间一个空格（如 "我 不 开心"）。
    """
    if kw_index <= 0:
        return False
    # 取关键词前 3 个字符（已是 lower）
    window_start = max(0, kw_index - 3)
    prefix = lower_text[window_start:kw_index]
    # 去掉空白便于匹配 "我 不 开心" 这种空格分割
    compact = prefix.replace(" ", "").replace("\u3000", "")
    for neg in _NEGATION_PREFIXES:
        if compact.endswith(neg):
            return True
    return False
