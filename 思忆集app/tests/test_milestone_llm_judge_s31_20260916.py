# -*- coding: utf-8 -*-
"""S3-1 里程碑 LLM 判评回归（2026-09-16）。

锁死四件事：
1. 弱词候选（带数字/感叹证据）必须送判评，score ≥0.8 才入选；<0.8 丢弃。
2. 判评单次批量调用（周候选 ≤50 条 = 1 次调用），不是每条一次。
3. 判评不可用（AI 未配置 / 抛异常 / 输出畸形）→ drop（宁缺毋滥），强词不受影响。
4. 强词命中不送判评（省 token + 防误杀），AI 缺席时行为与 S3-1a 完全一致。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from memory.narrative.milestone_detector import MilestoneDetector, _has_strong_keyword


class _FakeAI:
    """记录调用次数的判评桩。"""

    def __init__(self, scores: list[float] | None = None, raises: bool = False, raw: Any = None):
        self.calls: list[dict] = []
        self._scores = scores
        self._raises = raises
        self._raw = raw

    def complete(self, *, user_prompt: str, system_prompt: str = "", temperature: float = 0.0, max_tokens: int = 0, json_mode: bool = False):
        self.calls.append({"user_prompt": user_prompt, "json_mode": json_mode})
        if self._raises:
            raise RuntimeError("ai down")
        if self._raw is not None:
            return self._raw
        return {"scores": [{"index": i, "score": s} for i, s in enumerate(self._scores or [])]}


def _weak_note(idx: int, content: str) -> dict[str, Any]:
    return {"id": f"w{idx}", "title": f"记录{idx}", "content": content, "created_at": "2026-09-14T10:00:00+00:00"}


# 弱词 + 数字证据 → 候选（S3-1a 规则下会直接入选；S3-1 后须判评）
_WEAK_EVIDENCE = "完成了连续 30 天健身打卡计划！"
_STRONG_NOTE = {"id": "s1", "title": "今天", "content": "毕业后第一次搬家去新的城市。", "created_at": "2026-09-13T10:00:00+00:00"}


def test_strong_keyword_skips_judge():
    ai = _FakeAI(scores=[])
    out = MilestoneDetector(ai_text_service=ai).detect_from_memories([_STRONG_NOTE])
    assert [m["source_id"] for m in out] == ["s1"]
    assert ai.calls == [], "强词命中不得触发判评调用"
    assert all("_scan_text" not in m for m in out)


def test_weak_candidate_kept_only_above_threshold():
    notes = [
        _weak_note(1, _WEAK_EVIDENCE),  # 0.9 → 入选
        _weak_note(2, "通过了 5 门考试"),  # 0.3 → 丢弃
    ]
    ai = _FakeAI(scores=[0.9, 0.3])
    out = MilestoneDetector(ai_text_service=ai).detect_from_memories(notes)
    assert [m["source_id"] for m in out] == ["w1"]
    assert len(ai.calls) == 1, "两条候选应合并为一次批量判评"
    assert ai.calls[0]["json_mode"] is True


def test_judge_call_batched_once_for_many_candidates():
    notes = [_weak_note(i, _WEAK_EVIDENCE) for i in range(12)]
    ai = _FakeAI(scores=[0.95] * 12)
    out = MilestoneDetector(ai_text_service=ai).detect_from_memories(notes)
    assert len(out) == 12
    assert len(ai.calls) == 1


def test_judge_unavailable_drops_weak_keeps_strong():
    """AI 抛异常 → 弱词候选全部丢弃（drop 政策），强词照常。"""
    ai = _FakeAI(raises=True)
    out = MilestoneDetector(ai_text_service=ai).detect_from_memories([_weak_note(1, _WEAK_EVIDENCE), _STRONG_NOTE])
    assert [m["source_id"] for m in out] == ["s1"]


def test_no_ai_service_drops_weak_s31a_behaviour_changed():
    """未配置 AI → 弱词候选丢弃。注意：这与 S3-1a（共现即入选）刻意不同，
    判评不可用时宁缺毋滥是拍板方向（泛词塌方是本案病根）。"""
    out = MilestoneDetector().detect_from_memories([_weak_note(1, _WEAK_EVIDENCE)])
    assert out == []


def test_malformed_judge_output_treated_unavailable():
    ai = _FakeAI(raw="not json at all")
    out = MilestoneDetector(ai_text_service=ai).detect_from_memories([_weak_note(1, _WEAK_EVIDENCE)])
    assert out == []


def test_judge_string_json_parsed():
    raw = json.dumps({"scores": [{"index": 0, "score": 0.85}]})
    ai = _FakeAI(raw=raw)
    out = MilestoneDetector(ai_text_service=ai).detect_from_memories([_weak_note(1, _WEAK_EVIDENCE)])
    assert [m["source_id"] for m in out] == ["w1"]


def test_missing_index_defaults_zero():
    ai = _FakeAI(scores=[])  # 模型漏答
    ai._raw = {"scores": []}
    out = MilestoneDetector(ai_text_service=ai).detect_from_memories([_weak_note(1, _WEAK_EVIDENCE)])
    # scores=[] → seen_any False → None → drop
    assert out == []


def test_has_strong_keyword_helper():
    assert _has_strong_keyword(["第一次"]) is True
    assert _has_strong_keyword(["完成", "通过"]) is False


def test_candidates_capped_at_50():
    notes = [_weak_note(i, _WEAK_EVIDENCE) for i in range(60)]
    ai = _FakeAI(scores=[0.9] * 60)
    out = MilestoneDetector(ai_text_service=ai).detect_from_memories(notes)
    assert len(out) == 50, "每周期判评候选 ≤50 条（成本上限）"
