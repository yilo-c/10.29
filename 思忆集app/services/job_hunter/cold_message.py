"""AI 招呼语生成器。

根据用户画像与职位详情生成 3 种风格的投递招呼语，并附带反问建议。
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

MAX_MESSAGE_LENGTH = 200


class ColdMessageGenerator:
    """基于 LLM 的投递招呼语生成器。"""

    def __init__(self, ai_client: Any) -> None:
        self._ai_client = ai_client

    async def generate(self, profile: Any, job: dict[str, Any]) -> dict[str, Any]:
        """生成 3 种风格的招呼语与反问问题。

        Args:
            profile: 求职画像（需实现 ``to_prompt_text()``）。
            job: 职位信息字典。

        Returns:
            ``{"success": bool, "messages": {...}, "questions": [...]}``；
            失败时返回 ``{"success": False, "error": "..."}``。
        """
        if self._ai_client is None:
            return {"success": False, "error": "AI 客户端不可用"}

        prompt = self._build_prompt(profile, job)
        try:
            import asyncio

            result = await asyncio.to_thread(
                self._ai_client.complete_text,
                [{"role": "user", "content": prompt}],
                max_tokens=1200,
                temperature=0.6,
            )
        except Exception as exc:
            logger.warning("cold message generation failed: %s", exc)
            return {"success": False, "error": str(exc)}

        parsed = self._parse_result(result or "")
        return {"success": True, **parsed}

    @staticmethod
    def _build_prompt(profile: Any, job: dict[str, Any]) -> str:
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
            "你是一位资深求职顾问，请根据用户画像与职位信息生成用于招聘平台（如 BOSS 直聘）的投递招呼语。\n\n"
            "## 用户画像\n"
            f"{profile_text}"
            "\n\n## 职位信息\n"
            f"{job_text}"
            "\n\n## 输出要求\n严格按以下 JSON 格式输出（不要有任何额外说明）：\n"
            "{\n"
            '  "concise": "简洁风格招呼语，50-80字，突出最核心匹配点",\n'
            '  "professional": "专业风格招呼语，80-150字，体现经验与能力",\n'
            '  "enthusiastic": "热情风格招呼语，80-150字，体现兴趣与主动性",\n'
            '  "questions": ["3个反问HR的问题，展示对职位/公司的理解，每个问题不超过30字"]\n'
            "}\n"
            "注意：\n"
            "1. 招呼语用于直接复制粘贴到招聘平台，语言自然、有礼貌。\n"
            "2. 必须针对该职位具体信息撰写，避免通用模板感。\n"
            "3. 每个招呼语不超过 200 字。"
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
                return {"messages": {}, "questions": []}
            try:
                data = json.loads(match.group(0))
            except json.JSONDecodeError:
                return {"messages": {}, "questions": []}

        if not isinstance(data, dict):
            return {"messages": {}, "questions": []}

        messages: dict[str, str] = {}
        for style in ("concise", "professional", "enthusiastic"):
            msg = data.get(style, "")
            if isinstance(msg, str):
                msg = msg.strip()
                if len(msg) > MAX_MESSAGE_LENGTH:
                    msg = msg[:MAX_MESSAGE_LENGTH]
                messages[style] = msg
            else:
                messages[style] = ""

        questions = data.get("questions", [])
        if isinstance(questions, str):
            questions = [questions]
        if isinstance(questions, list):
            questions = [str(q).strip() for q in questions if str(q).strip()]
        else:
            questions = []

        return {"messages": messages, "questions": questions[:3]}
