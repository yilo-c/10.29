"""AI 深度职位分析。"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, AsyncIterator

if TYPE_CHECKING:
    from .config import JobHunterProfile

logger = logging.getLogger(__name__)


class JobAnalyzer:
    """基于 LLM 的单职位深度分析器。"""

    def __init__(self, ai_client: Any) -> None:
        self._ai_client = ai_client

    async def analyze(
        self,
        profile: JobHunterProfile,
        job: dict[str, Any],
    ) -> dict[str, Any]:
        """返回结构化分析结果。"""
        if self._ai_client is None:
            return {"error": "AI 客户端不可用"}

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
            logger.warning("job analyze failed: %s", exc)
            return {"error": str(exc)}

        return {
            "job_id": job.get("id"),
            "analysis": (result or "").strip(),
            "dimensions": self._extract_dimensions(result or ""),
        }

    async def analyze_stream(
        self,
        profile: JobHunterProfile,
        job: dict[str, Any],
    ) -> AsyncIterator[str]:
        """流式输出分析文本（如果底层 AI 客户端支持流式）。"""
        if self._ai_client is None:
            yield "AI 客户端不可用"
            return

        prompt = self._build_prompt(profile, job)
        stream_method = getattr(self._ai_client, "complete_text_stream", None)
        if stream_method is None:
            # 回退到非流式
            result = await self.analyze(profile, job)
            yield result.get("analysis", "")
            return

        try:
            import asyncio

            sync_iter = await asyncio.to_thread(
                stream_method,
                [{"role": "user", "content": prompt}],
                max_tokens=1500,
                temperature=0.4,
            )

            while True:
                try:
                    chunk = await asyncio.to_thread(next, sync_iter)
                except StopIteration:
                    return
                if chunk:
                    yield chunk
        except Exception as exc:
            logger.warning("job analyze stream failed: %s", exc)
            yield f"分析失败: {exc}"
            return

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
            f"\n公司简介/规模：{job.get('company_size', '')} {job.get('company_stage', '')}"
            f"\n职位描述：\n{job.get('description', job.get('summary', ''))[:1500]}"
            f"\n岗位要求：\n{job.get('requirements', '')[:1000]}"
            f"\n福利：{', '.join(job.get('benefits', []))}"
        )

        return (
            "你是一位资深猎头与职业顾问，请根据用户画像深度分析以下职位，并给出结构化建议。\n"
            "\n## 用户画像\n"
            f"{profile_text}"
            "\n\n## 职位信息\n"
            f"{job_text}"
            "\n\n## 分析维度\n"
            "1. 技术要求匹配度：用户技术栈与岗位要求的匹配情况。\n"
            "2. 薪资竞争力：该薪资在用户期望范围内的竞争力评估。\n"
            "3. 公司背景：公司规模、阶段、行业地位等评估。\n"
            "4. 发展空间：岗位成长空间与长期价值。\n"
            "5. 投递策略：是否值得投递、简历侧重点、面试准备建议。\n"
            "\n请用中文 Markdown 格式输出，分维度给出结论，最后给出总体评分（0-100）与一句总结。"
        )

    @staticmethod
    def _extract_dimensions(text: str) -> dict[str, Any]:
        """轻量级维度提取（容错）。"""
        dims: dict[str, Any] = {}
        try:
            import re

            score_match = re.search(r"总体评分[:：]\s*(\d{1,3})", text)
            if score_match:
                dims["overall_score"] = int(score_match.group(1))
        except Exception:
            pass
        return dims
