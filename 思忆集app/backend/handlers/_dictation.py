"""单词听写模式 — word.dictation_check RPC。

设计：
  - 输入用户的拼写答案，返回字符级 diff、正确性判断、建议评分
  - 大小写不敏感（除非词条明确大写敏感，下个版本再加）
  - 容错：去除首尾空白、把多个空格压缩为一个
  - 返回结构：
    {
      "correct": bool,            # 是否完全正确（忽略大小写/标点）
      "exact": bool,              # 大小写也完全一致
      "diff": [(op, char), ...],  # op ∈ {"=", "+", "-"}：保留 / 用户多输 / 用户漏输
      "expected": str,
      "actual": str,
      "suggested_quality": int,   # 0-5；用于自动调用 word.review
      "suggested_rating": int,    # 1-4 (Again/Hard/Good/Easy)
    }
"""

from __future__ import annotations

import asyncio
import logging
from difflib import SequenceMatcher
from typing import Any

from core.events import AppEvent, event_bus

logger = logging.getLogger("siyi.backend.handlers.dictation")


def _normalize(text: str) -> str:
    """大小写不敏感比较前的归一化：lower + trim + 多空白合并。"""
    s = " ".join(text.lower().split())
    return s


def diff_chars(expected: str, actual: str) -> list[dict[str, str]]:
    """字符级 diff，返回 [{op, ch}] 列表。

    op:
      "=" — 两端一致；前端绿色显示
      "+" — actual 多出（用户多打了）；前端红色显示
      "-" — actual 缺失（用户漏打）；前端用占位符或下划线提示

    使用 difflib.SequenceMatcher，最稳的 LCS 实现。
    """
    sm = SequenceMatcher(a=expected, b=actual, autojunk=False)
    out: list[dict[str, str]] = []
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            for ch in expected[i1:i2]:
                out.append({"op": "=", "ch": ch})
        elif tag == "replace":
            # 期望中有这些字符（用户漏 / 错），同时用户多打了 actual[j1:j2]
            for ch in expected[i1:i2]:
                out.append({"op": "-", "ch": ch})
            for ch in actual[j1:j2]:
                out.append({"op": "+", "ch": ch})
        elif tag == "delete":
            # actual 缺失的部分
            for ch in expected[i1:i2]:
                out.append({"op": "-", "ch": ch})
        elif tag == "insert":
            # actual 多出的部分
            for ch in actual[j1:j2]:
                out.append({"op": "+", "ch": ch})
    return out


def _suggest_score(expected: str, actual: str, exact: bool, correct: bool) -> tuple[int, int]:
    """根据答题情况推荐评分。

    返回 (quality_0_5, rating_1_4):
      - exact 一致         → quality 5 / rating 4 (Easy)
      - correct 但大小写错 → quality 4 / rating 3 (Good)
      - 1 个字符差         → quality 3 / rating 2 (Hard)
      - 完全错             → quality 1 / rating 1 (Again)
    """
    if exact:
        return 5, 4
    if correct:
        return 4, 3
    # 用 SequenceMatcher 算相似度
    ratio = SequenceMatcher(None, expected.lower(), actual.lower()).ratio()
    if ratio >= 0.85:
        return 3, 2
    return 1, 1


def register_dictation(registry, context) -> None:
    """注册 word.dictation_check RPC。"""

    async def word_dictation_check(
        word_id: int = 0,
        expected: str = "",
        actual: str = "",
        auto_record: bool = False,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """检查听写答案。

        Args:
          word_id: 词条 ID。当 auto_record=True 时必填（用于回写复习记录）
          expected: 正确答案；为空时从词库按 word_id 取
          actual: 用户输入的答案
          auto_record: True 时自动调用 record_review 写入 SM-2/FSRS

        Returns:
          字符级 diff + 正确性 + 建议评分；当 auto_record=True 时同时返回 review 结果。
        """
        actual_str = str(actual or "")
        # 解析 expected：优先用参数；没传则查仓储
        expected_str = str(expected or "").strip()
        if not expected_str and word_id:
            repo = context.vocabulary_repository
            if repo:
                try:
                    # P1 修复：list_words 是同步仓储方法（文件 I/O），用 to_thread 避免阻塞事件循环
                    words = await asyncio.to_thread(repo.list_words)
                    target = next((w for w in words if w.get("id") == word_id), None)
                    if target:
                        expected_str = str(target.get("word") or "").strip()
                except Exception:
                    logger.debug("look up word for dictation failed", exc_info=True)

        if not expected_str:
            return {"error": "expected required (or invalid word_id)"}

        normalized_exp = _normalize(expected_str)
        normalized_act = _normalize(actual_str)
        correct = normalized_exp == normalized_act
        exact = expected_str.strip() == actual_str.strip()
        # 字符级 diff：基于"原文 vs 用户输入"——小写对齐，但展示原文大小写
        diff = diff_chars(expected_str, actual_str)
        suggested_quality, suggested_rating = _suggest_score(expected_str, actual_str, exact, correct)

        result: dict[str, Any] = {
            "correct": correct,
            "exact": exact,
            "diff": diff,
            "expected": expected_str,
            "actual": actual_str,
            "suggested_quality": suggested_quality,
            "suggested_rating": suggested_rating,
        }

        # emit 事件：方便小问连续答错情绪联动
        try:
            event_bus.emit(AppEvent(
                event_type="vocabulary.dictation.checked",
                payload={
                    "word_id": word_id,
                    "word": expected_str,
                    "correct": correct,
                    "exact": exact,
                    "suggested_quality": suggested_quality,
                },
                source="word.dictation_check",
            ))
        except Exception:
            logger.debug("emit dictation event failed", exc_info=True)

        # auto_record：直接走 record_review
        if auto_record and word_id:
            repo = context.vocabulary_repository
            if repo:
                try:
                    review_result = await asyncio.to_thread(
                        repo.record_review, word_id, suggested_quality
                    )
                    if review_result is not None:
                        reps, ef, intv, nr = review_result
                        result["review"] = {
                            "repetitions": reps,
                            "efactor": ef,
                            "interval": intv,
                            "next_review": nr,
                        }
                except Exception as exc:
                    logger.warning("dictation auto_record failed: %s", exc)
                    result["review_error"] = str(exc)

        return result

    registry.register("word.dictation_check", word_dictation_check)
