# -*- coding: utf-8 -*-
"""AI 简历优化建议。

根据用户画像与职位详情给出简历应突出的重点、应弱化的部分以及推荐关键词。
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any

from services.job_hunter.config import JobHunterProfile

logger = logging.getLogger("siyi.services.job_hunter.resume_advisor")


class ResumeAdvisor:
    """基于 LLM 的简历优化建议器。"""

    def __init__(self, ai_client: Any) -> None:
        self._ai_client = ai_client

    async def advise(self, profile: JobHunterProfile, job: dict[str, Any]) -> dict[str, Any]:
        """返回简历优化建议。"""
        if self._ai_client is None:
            return {"success": False, "error": "AI 客户端不可用"}

        prompt = self._build_prompt(profile, job)
        try:
            import asyncio

            result = await asyncio.to_thread(
                self._ai_client.complete_text,
                [{"role": "user", "content": prompt}],
                max_tokens=1200,
                temperature=0.4,
            )
        except Exception as exc:
            logger.warning("resume advice failed: %s", exc)
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
            "你是一位资深 HR 与简历优化专家，请根据用户画像与职位 JD，给出针对该职位的简历优化建议。\n\n## 用户画像\n"
            f"{profile_text}"
            "\n\n## 职位信息\n"
            f"{job_text}"
            "\n\n## 输出要求\n严格按以下 JSON 格式输出（不要有任何额外说明）：\n"
            "{\n"
            '  "key_skills": ["该职位最看重的前3项技能/经验，每项一句话"],\n'
            '  "highlight": ["简历中应突出的项目/经历建议，最多3条，每条具体可操作"],\n'
            '  "downplay": ["简历中应弱化或调整的部分，最多3条"],\n'
            '  "keywords": ["推荐在简历中增加的关键词，帮助通过HR筛选，最多5个"]\n'
            "}\n注意：建议要具体、可操作，避免空泛。"
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
                return {"key_skills": [], "highlight": [], "downplay": [], "keywords": []}
            try:
                data = json.loads(match.group(0))
            except json.JSONDecodeError:
                return {"key_skills": [], "highlight": [], "downplay": [], "keywords": []}
        if not isinstance(data, dict):
            return {"key_skills": [], "highlight": [], "downplay": [], "keywords": []}

        def _list(key: str, limit: int) -> list[str]:
            value = data.get(key, [])
            if isinstance(value, str):
                value = [value]
            if isinstance(value, list):
                return [str(v).strip() for v in value if str(v).strip()][:limit]
            return []

        return {
            "key_skills": _list("key_skills", 3),
            "highlight": _list("highlight", 3),
            "downplay": _list("downplay", 3),
            "keywords": _list("keywords", 5),
        }
