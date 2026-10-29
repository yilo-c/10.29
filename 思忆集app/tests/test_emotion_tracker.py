"""P2-19 回归测试：EmotionTracker 否定语境豁免 + "无聊" 修正为 negative。"""
from __future__ import annotations

import sys
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from memory.narrative.emotion_tracker import (
    EmotionTracker,
    _EMOTION_CATEGORIES,
    _is_negated,
)


# ── 1. "无聊" 归类修正 ──

def test_boredom_is_negative_not_neutral():
    """'无聊' 现在归 negative，反映情绪低落而非情绪平稳。"""
    assert _EMOTION_CATEGORIES["无聊"] == "negative"


def test_boredom_text_classified_as_negative_overall():
    tracker = EmotionTracker()
    memories = [
        {"content": "今天太无聊了"},
        {"content": "无聊死了"},
        {"content": "感觉很无聊"},
    ]
    result = tracker.analyze_memories(memories)
    # 全是 "无聊"，现在应为负面
    assert result["overall_mood"] == "negative"


# ── 2. 否定语境：positive 关键词不再误识别 ──

def test_negation_blocks_positive_match():
    tracker = EmotionTracker()
    detected = tracker._detect_emotions("我不开心")
    assert "开心" not in detected


def test_negation_blocks_negative_match():
    tracker = EmotionTracker()
    detected = tracker._detect_emotions("我并不难过")
    assert "难过" not in detected


def test_negation_with_kept_text_around():
    tracker = EmotionTracker()
    # "今天有点不开心" — 应不识别 "开心"
    detected = tracker._detect_emotions("今天有点不开心")
    assert "开心" not in detected


def test_no_negation_still_detects():
    tracker = EmotionTracker()
    detected = tracker._detect_emotions("我很开心")
    assert "开心" in detected


def test_partial_match_with_negation_then_positive():
    """'我不开心，但还是要努力，希望明天能开心一点' —— 第一处被否定，第二处计入。"""
    tracker = EmotionTracker()
    detected = tracker._detect_emotions("我不开心，但还是希望明天能开心一点")
    # 应识别一次 "开心"（来自后半句）
    assert detected.count("开心") == 1


# ── 3. _is_negated 单元用例 ──

def test_is_negated_simple():
    text = "我不开心"
    idx = text.find("开心")
    assert _is_negated(text, idx) is True


def test_is_negated_compound():
    """复合否定 '并没有难过'。"""
    text = "并没有难过"
    idx = text.find("难过")
    assert _is_negated(text, idx) is True


def test_is_negated_no_negation():
    text = "我很开心"
    idx = text.find("开心")
    assert _is_negated(text, idx) is False


def test_is_negated_with_whitespace_tolerance():
    """容忍 '我 不 开心' 这种空格分隔的否定。"""
    text = "我 不 开心"
    idx = text.find("开心")
    assert _is_negated(text, idx) is True


def test_is_negated_at_text_start_no_room_for_prefix():
    text = "开心一点"
    idx = text.find("开心")
    assert _is_negated(text, idx) is False


# ── 4. 综合：长文本统计 ──

def test_mixed_text_with_negations():
    tracker = EmotionTracker()
    memories = [
        {"content": "今天我并不难过，反而很开心"},
        {"content": "我很期待明天"},
    ]
    result = tracker.analyze_memories(memories)
    counts = result["emotion_counts"]
    # "难过" 被否定不应该出现
    assert "难过" not in counts
    # "开心" + "期待" 都是 positive
    assert counts.get("开心", 0) >= 1
    assert counts.get("期待", 0) >= 1
    assert result["overall_mood"] == "positive"
