"""简历诊断分析器。

基于 LLM 对简历进行多维度评分，并给出可操作的改进建议。
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class DiagnosisScores:
    """简历多维度评分。"""

    ats_readable: int
    quantified: int
    concise: int
    keyword_match: int
    overall: int

    def to_dict(self) -> dict[str, int]:
        return {
            "ats_readable": self.ats_readable,
            "quantified": self.quantified,
            "concise": self.concise,
            "keyword_match": self.keyword_match,
            "overall": self.overall,
        }


@dataclass
class DiagnosisResult:
    """简历诊断结果。"""

    success: bool
    scores: DiagnosisScores | None
    suggestions: list[str]
    highlights: list[str]
    weaknesses: list[str]
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "success": self.success,
            "scores": self.scores.to_dict() if self.scores else None,
            "suggestions": self.suggestions,
            "highlights": self.highlights,
            "weaknesses": self.weaknesses,
            "error": self.error,
        }


class ResumeAnalyzer:
    """基于 LLM 的简历诊断器。"""

    def __init__(self, ai_client: Any) -> None:
        self._ai_client = ai_client

    async def diagnose(
        self, resume_text: str, target_role: str = "", industry: str = ""
    ) -> DiagnosisResult:
        """对简历进行多维度诊断评分。"""
        if self._ai_client is None:
            return DiagnosisResult(False, None, [], [], [], error="AI 客户端不可用")

        text = (resume_text or "").strip()
        if not text:
            return DiagnosisResult(False, None, [], [], [], error="简历文本为空")
        if len(text) > 12000:
            text = text[:12000]

        prompt = self._build_prompt(text, target_role, industry)
        try:
            import asyncio

            result = await asyncio.to_thread(
                self._ai_client.complete_text,
                [{"role": "user", "content": prompt}],
                max_tokens=1200,
                temperature=0.3,
            )
        except Exception as exc:
            logger.warning("resume diagnose failed: %s", exc)
            return DiagnosisResult(False, None, [], [], [], error=str(exc))

        parsed = self._parse_result(result or "")
        return DiagnosisResult(success=True, **parsed)

    @staticmethod
    def _build_prompt(resume_text: str, target_role: str, industry: str) -> str:
        target_hint = f"目标岗位：{target_role}\n" if target_role else ""
        industry_hint = f"目标行业：{industry}\n" if industry else ""
        return (
            "你是一位资深 HR 与简历诊断专家。请对以下简历进行多维度评分并给出改进建议。"
            "输出必须是合法 JSON，不要添加任何额外说明。\n\n"
            f"{target_hint}"
            f"{industry_hint}"
            "## 简历文本\n"
            f"{resume_text}"
            "\n\n## 输出要求\n"
            "请严格按以下 JSON 格式输出：\n"
            "{\n"
            '  "scores": {\n'
            '    "ats_readable": 0-100,    // ATS 可读性：结构清晰、有标准章节、无复杂排版\n'
            '    "quantified": 0-100,      // 量化程度：经历中有数据、指标、成果\n'
            '    "concise": 0-100,         // 简洁度：无冗余、重点突出\n'
            '    "keyword_match": 0-100,   // 目标岗位关键词匹配度\n'
            '    "overall": 0-100          // 综合评分\n'
            "  },\n"
            '  "suggestions": ["具体可操作建议1", "建议2", "建议3-5条"],\n'
            '  "highlights": ["简历亮点1", "亮点2"],\n'
            '  "weaknesses": ["主要不足1", "不足2"]\n'
            "}\n"
            "注意：\n"
            "1. 只输出 JSON，不要 Markdown 代码块、不要解释。\n"
            "2. 评分必须真实反映简历质量，不要刻意偏高。\n"
            "3. suggestions 必须具体、可操作，避免空泛。"
        )

    @staticmethod
    def _parse_result(text: str) -> dict[str, Any]:
        text = text.strip()
        if text.startswith("```"):
            text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.DOTALL)
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            match = re.search(r"\{.*\}", text, re.DOTALL)
            if not match:
                return ResumeAnalyzer._empty_result()
            try:
                data = json.loads(match.group(0))
            except json.JSONDecodeError:
                return ResumeAnalyzer._empty_result()

        if not isinstance(data, dict):
            return ResumeAnalyzer._empty_result()

        scores_raw = data.get("scores") or {}
        scores = DiagnosisScores(
            ats_readable=_clip_int(scores_raw.get("ats_readable")),
            quantified=_clip_int(scores_raw.get("quantified")),
            concise=_clip_int(scores_raw.get("concise")),
            keyword_match=_clip_int(scores_raw.get("keyword_match")),
            overall=_clip_int(scores_raw.get("overall")),
        )
        return {
            "scores": scores,
            "suggestions": _str_list(data.get("suggestions")),
            "highlights": _str_list(data.get("highlights")),
            "weaknesses": _str_list(data.get("weaknesses")),
        }

    @staticmethod
    def _empty_result() -> dict[str, Any]:
        return {
            "scores": DiagnosisScores(
                ats_readable=0, quantified=0, concise=0, keyword_match=0, overall=0
            ),
            "suggestions": [],
            "highlights": [],
            "weaknesses": [],
        }


def _clip_int(value: Any) -> int:
    try:
        num = int(value)
    except (TypeError, ValueError):
        return 0
    return max(0, min(100, num))


def _str_list(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]
    return []
