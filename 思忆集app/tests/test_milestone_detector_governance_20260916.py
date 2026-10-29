"""里程碑质量治理回归测试（2026-09-16，AI-2 案 / 任务 #27 零成本止血）。

覆盖三条治理规则（milestone_detector）+ emotion 诚实空值（emotion_tracker）：
① 产品内置 demo 笔记（《📖 思忆便签使用指南》）→ 绝不出里程碑；
② 泛词分级：仅两个弱泛词、无数字/感叹证据 → 不算里程碑；强词 → 算；
③ preview 摘要化：content ≤ 80 字，不再把原文 markdown 截断塞进周报正文；
④ emotion_summary 无情绪信号时自曝 available=False / overall_mood=unknown，
   不再伪造 "mixed"/"neutral" 假数据。
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from memory.narrative.emotion_tracker import EmotionTracker
from memory.narrative.milestone_detector import (
    MilestoneDetector,
    _find_milestone_keywords,
    _is_demo_entry,
    _summarize_preview,
)


# ── fixture：复刻霸榜周报的那条内置 demo 笔记（source_id=95）───────────
_DEMO_GUIDE_CONTENT = (
    "# 📖 思忆便签使用指南\n\n"
    "欢迎使用 **思忆便签** — 一款轻量级、全功能的桌面便签与待办管理工具。\n\n"
    "---\n\n"
    "## 🗒️ 笔记功能\n\n"
    "- **新建笔记**：点击底部工具栏的「新建笔记」按钮，或在笔记面板点击「+」\n"
    "- **Markdown 编辑**：支持完整的 Markdown 语法（标题、列表、表格、代码块等）\n"
    "- **富文本编辑**：通过格式工具栏可切换富文本/Markdown 模式，"
    "关键操作一目了然，这对新手非常重要。\n"
)

# 大量泛词、无数字/感叹，纯靠子串会误判的普通用户笔记
_PLAIN_WEAK_NOTE: dict[str, Any] = {
    "id": "u1",
    "title": "随手记",
    "content": "最近决定了新的计划，也想开始改变，感觉挺重要的。",
    "created_at": "2026-09-10T10:00:00+00:00",
}
# 含强词的真人里程碑笔记
_STRONG_NOTE: dict[str, Any] = {
    "id": "u2",
    "title": "今天",
    "content": "毕业后我第一次一个人搬家去新的城市。",
    "created_at": "2026-09-11T10:00:00+00:00",
}


def _demo_note() -> dict[str, Any]:
    return {
        "id": 95,
        "title": "📖 思忆便签使用指南",
        "content": _DEMO_GUIDE_CONTENT,
        "created_at": "2026-09-01T02:35:03.435000+00:00",
    }


# ── ① demo / 种子来源排除 ────────────────────────────────────────


def test_is_demo_entry_detects_builtin_guide():
    assert _is_demo_entry(_demo_note()) is True


def test_is_demo_entry_detects_explicit_flags():
    assert _is_demo_entry({"title": "随便", "is_example": True}) is True
    assert _is_demo_entry({"title": "随便", "is_demo": True}) is True
    assert _is_demo_entry({"title": "随便", "source": "builtin_demo"}) is True


def test_is_demo_entry_allows_real_user_note():
    assert _is_demo_entry({"id": "u1", "title": "我的项目复盘", "content": "第一次上线"}) is False


def test_demo_note_produces_no_milestone():
    """核心回归：demo 帮助文档被"通过/关键/重要"命中，绝不能进里程碑选材。"""
    milestones = MilestoneDetector().detect_from_memories([_demo_note()])
    assert milestones == []


def test_demo_note_excluded_but_user_note_kept():
    """混入真实里程碑时，demo 被剔除、用户笔记保留。"""
    milestones = MilestoneDetector().detect_from_memories([_demo_note(), _STRONG_NOTE])
    ids = [m["source_id"] for m in milestones]
    assert 95 not in ids
    assert "u2" in ids


# ── ② 泛词分级 ────────────────────────────────────────────────────


def test_two_weak_keywords_without_evidence_is_not_milestone():
    """旧 bug：两个弱泛词（通过+关键）即判里程碑。新规则：无证据不算。"""
    assert _find_milestone_keywords("通过这一关很关键") == []
    # 端到端同样不产出里程碑
    assert MilestoneDetector().detect_from_memories([_PLAIN_WEAK_NOTE]) == []


def test_weak_keywords_with_numeric_evidence_is_milestone():
    """弱词 + 数字/感叹共现才升级为里程碑。"""
    matched = _find_milestone_keywords("完成了 30 天打卡计划，真棒！")
    assert matched  # 命中弱词且带数字/感叹证据


def test_strong_keyword_alone_is_milestone():
    matched = _find_milestone_keywords("这是我第一次独立上线项目")
    assert "第一次" in matched
    assert MilestoneDetector().detect_from_memories([_STRONG_NOTE]) != []


def test_many_weak_substrings_without_evidence_still_rejected():
    """堆多个弱词、无证据 → 依旧不算（防子串刷分）。"""
    assert _find_milestone_keywords("计划通过重要关键的决策") == []


# ── ③ content 摘要化 ──────────────────────────────────────────────


def test_preview_is_short_summary_not_raw_truncated_markdown():
    preview = _summarize_preview(_DEMO_GUIDE_CONTENT)
    assert len(preview) <= 80
    # 取标题级摘要：只保留首行标题文字，剥离 markdown 结构符号
    assert "使用指南" in preview
    assert "**" not in preview and "\n" not in preview and "#" not in preview
    # 不应把整篇帮助文的多段正文塞进来
    assert "新建笔记" not in preview and "Markdown" not in preview


def test_preview_of_milestone_entry_within_limit():
    long_note = {
        "id": "u9",
        "title": "复盘",
        "content": "第一次正式发布：" + "很长的一段正文内容" * 40,
        "created_at": "2026-09-12T10:00:00+00:00",
    }
    milestones = MilestoneDetector().detect_from_memories([long_note])
    assert milestones
    assert len(milestones[0]["preview"]) <= 80


def test_preview_truncates_with_ellipsis():
    text = "第一行" + "字" * 200
    preview = _summarize_preview(text)
    assert len(preview) <= 80
    assert preview.endswith("…")


# ── ④ emotion 诚实空值 ────────────────────────────────────────────


def test_emotion_empty_input_is_unavailable():
    result = EmotionTracker().analyze_memories([])
    assert result["available"] is False
    assert result["overall_mood"] == "unknown"
    assert result["emotion_counts"] == {}
    assert result["top_emotions"] == []


def test_emotion_no_signal_does_not_fabricate_mixed():
    """旧 bug：无情绪词也返回 overall_mood=mixed。现必须自曝未计算。"""
    memories = [{"content": "记录了今天的天气和日程安排"}]  # 无任何情绪词
    result = EmotionTracker().analyze_memories(memories)
    assert result["available"] is False
    assert result["overall_mood"] == "unknown"
    assert result["overall_mood"] != "mixed"


def test_emotion_real_signal_available_true():
    memories = [{"content": "今天很开心，也很期待明天"}]
    result = EmotionTracker().analyze_memories(memories)
    assert result["available"] is True
    assert result["overall_mood"] == "positive"
