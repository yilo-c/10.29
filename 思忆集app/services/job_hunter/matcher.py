"""AI 职位匹配打分。"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

BATCH_SIZE = 8


class JobMatcher:
    """基于 LLM 的职位批量匹配打分器。"""

    def __init__(self, ai_client: Any) -> None:
        self._ai_client = ai_client

    async def match_batch(self, profile: Any, jobs: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """批量为职位打匹配分。

        先按排除关键词过滤（命中者直接 0 分），其余按 BATCH_SIZE 分批交给 AI 打分。
        """
        if not jobs:
            return []

        filtered: list[dict[str, Any]] = []
        excludes = [k.lower() for k in profile.exclude_keywords if k.strip()]
        for job in jobs:
            text = f"{job.get('title', '')} {job.get('summary', '')} {job.get('company', '')}"
            text_lower = text.lower()
            if any(ex in text_lower for ex in excludes):
                job = dict(job)
                job["match_score"] = 0.0
                job["match_reasons"] = ["命中排除关键词"]
                filtered.append(job)
            else:
                filtered.append(dict(job))

        results: list[dict[str, Any]] = []
        for i in range(0, len(filtered), BATCH_SIZE):
            batch = filtered[i : i + BATCH_SIZE]
            scored = await self._match_batch_ai(profile, batch)
            results.extend(scored)
        return results

    async def _match_batch_ai(
        self, profile: Any, batch: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """调用 AI 为单批职位打分。"""
        if self._ai_client is None:
            for job in batch:
                job["match_score"] = 0.0
                job["match_reasons"] = ["AI 客户端不可用"]
            return batch

        profile_text = profile.to_prompt_text()
        jobs_text = self._format_jobs(batch)
        prompt = (
            "你是一位资深猎头，请根据用户画像为以下每个职位打分并给出理由。\n\n"
            "## 用户画像\n"
            f"{profile_text}"
            "\n\n## 职位列表\n"
            f"{jobs_text}"
            "\n\n## 输出要求\n严格按 JSON 数组格式输出，每个元素包含："
            "id（职位ID）、score（0-100整数，匹配度越高越高）、"
            "reasons（3条以内字符串理由，说明为什么匹配或不匹配）。"
            "只输出 JSON 数组，不要任何额外说明。"
        )

        try:
            import asyncio

            result = await asyncio.to_thread(
                self._ai_client.complete_text,
                [{"role": "user", "content": prompt}],
                max_tokens=1200,
                temperature=0.3,
            )
            scores = self._parse_scores(result or "")
        except Exception as exc:
            logger.warning("job match batch failed: %s", exc)
            scores = {}

        for job in batch:
            sid = job.get("id", "")
            score_info = (
                scores.get(sid, {"score": 0, "reasons": ["AI 匹配失败"]})
                if scores
                else {"score": 0, "reasons": ["AI 匹配失败"]}
            )
            job["match_score"] = float(score_info.get("score", 0))
            job["match_reasons"] = score_info.get("reasons", [])
        return batch

    @staticmethod
    def _format_jobs(jobs: list[dict[str, Any]]) -> str:
        lines = []
        for idx, job in enumerate(jobs, 1):
            lines.append(
                f"[{idx}] id={job.get('id', '')}"
                f"\n  职位：{job.get('title', '')}"
                f"\n  公司：{job.get('company', '')}"
                f"\n  城市：{job.get('city', '')}"
                f"\n  薪资：{job.get('salary', '')}"
                f"\n  经验要求：{job.get('experience', '')}"
                f"\n  学历要求：{job.get('education', '')}"
                f"\n  标签：{', '.join(job.get('tags', []))}"
                f"\n  简介：{job.get('summary', '')[:200]}"
            )
        return "\n\n".join(lines)

    @staticmethod
    def _parse_scores(text: str) -> dict[str, dict[str, Any]]:
        text = text.strip()
        if text.startswith("```"):
            text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.DOTALL)
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            # 容错：从自由文本中抽取第一个 JSON 数组
            match = re.search(r"\[.*?\]", text, re.DOTALL)
            if not match:
                return {}
            try:
                data = json.loads(match.group(0))
            except json.JSONDecodeError:
                return {}

        if not isinstance(data, list):
            return {}

        result: dict[str, dict[str, Any]] = {}
        for item in data:
            if not isinstance(item, dict):
                continue
            sid = str(item.get("id", ""))
            if not sid:
                continue
            score = item.get("score", item.get("match_score", 0))
            try:
                score = int(score)
            except (ValueError, TypeError):
                score = 0
            reasons = item.get("reasons", item.get("match_reasons", []))
            if isinstance(reasons, str):
                reasons = [reasons]
            result[sid] = {
                "score": max(0, min(100, score)),
                "reasons": reasons[:3] if isinstance(reasons, list) else [],
            }
        return result
