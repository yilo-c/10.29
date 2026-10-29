# -*- coding: utf-8 -*-
"""AI 面试问题预测。

根据用户画像与职位详情预测技术面试、行为面试问题以及反问 HR 的建议问题。
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any

from services.job_hunter.config import JobHunterProfile

logger = logging.getLogger("siyi.services.job_hunter.interview_prep")


class InterviewPrep:
    """基于 LLM 的面试问题预测器。"""

    def __init__(self, ai_client: Any) -> None:
        self._ai_client = ai_client

    async def prepare(self, profile: JobHunterProfile, job: dict[str, Any]) -> dict[str, Any]:
        """返回面试准备内容。"""
        if self._ai_client is None:
            return {"success": False, "error": "AI 客户端不可用"}

        prompt = self._build_prompt(profile, job)
        try:
            import asyncio

            result = await asyncio.to_thread(
                self._ai_client.complete_text,
                [{"role": "user", "content": prompt}],
                max_tokens=1500,
                temperature=0.4,
            )
        except Exception as exc:
            logger.warning("interview prep failed: %s", exc)
            return {"success": False, "error": str(exc)}

        parsed = self._parse_result(result or "")
        return {"success": True, **parsed}

    @staticmethod
    def _build_prompt(profile: JobHunterProfile, job: dict[str, Any]) -> str:
        profile_text = profile.to_prompt_text()
        job_text = (
            f"职位：{job.get('title', '')}"
            f"\n公司：{job.get('company', '')}"
            f"\n城市：{job.get('city', '')}"
            f"\n薪资：{job.get('salary', '')}"
            f"\n经验要求：{job.get('experience', '')}"
            f"\n学历要求：{job.get('education', '')}"
            f"\n标签：{', '.join(job.get('tags', []))}"
            f"\n职位描述：\n{job.get('description', job.get('summary', ''))[:1200]}"
            f"\n岗位要求：\n{job.get('requirements', '')[:800]}"
        )
        return (
            "你是一位资深技术面试官与 HR，请根据用户画像与职位信息预测面试问题并给出准备建议。\n\n## 用户画像\n"
            f"{profile_text}"
            "\n\n## 职位信息\n"
            f"{job_text}"
            "\n\n## 输出要求\n严格按以下 JSON 格式输出（不要有任何额外说明）：\n"
            "{\n"
            '  "technical": [\n'
            '    {"question": "基于JD技术栈的技术面试问题1", "points": ["回答要点1", "回答要点2"]},\n'
            "    ...（共5题）\n"
            "  ],\n"
            '  "behavioral": [\n'
            '    {"question": "行为面试问题1", "points": ["回答要点1"]},\n'
            "    ...（共3题）\n"
            "  ],\n"
            '  "questions_for_hr": ["3个适合反问HR的问题"]\n'
            "}\n注意：问题要紧扣 JD 技术栈与职位要求，回答要点简洁 actionable。"
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
                return {"technical": [], "behavioral": [], "questions_for_hr": []}
            try:
                data = json.loads(match.group(0))
            except json.JSONDecodeError:
                return {"technical": [], "behavioral": [], "questions_for_hr": []}
        if not isinstance(data, dict):
            return {"technical": [], "behavioral": [], "questions_for_hr": []}

        def _qa_list(key: str, limit: int) -> list[dict[str, Any]]:
            value = data.get(key, [])
            if not isinstance(value, list):
                return []
            result = []
            for item in value:
                if not isinstance(item, dict):
                    continue
                question = str(item.get("question", "")).strip()
                if not question:
                    continue
                points = item.get("points", [])
                if isinstance(points, str):
                    points = [points]
                if isinstance(points, list):
                    points = [str(p).strip() for p in points if str(p).strip()]
                else:
                    points = []
                result.append({"question": question, "points": points})
            return result[:limit]

        questions = data.get("questions_for_hr", [])
        if isinstance(questions, str):
            questions = [questions]
        if isinstance(questions, list):
            questions = [str(q).strip() for q in questions if str(q).strip()][:3]
        else:
            questions = []

        return {
            "technical": _qa_list("technical", 5),
            "behavioral": _qa_list("behavioral", 3),
            "questions_for_hr": questions,
        }
