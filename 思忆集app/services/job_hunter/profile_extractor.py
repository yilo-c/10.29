"""AI 简历画像提取器。

根据用户粘贴或上传的简历文本，提取结构化求职画像与关键词，
用于回填个人资料、优化职位匹配。
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

logger = logging.getLogger(__name__)


class ProfileExtractor:
    """基于 LLM 的简历关键词与画像提取器。"""

    def __init__(self, ai_client: Any) -> None:
        self._ai_client = ai_client

    async def extract(self, resume_text: str) -> dict[str, Any]:
        """从简历文本中提取结构化画像建议。

        返回字段尽量与 JobHunterProfile 对齐，便于前端直接回填。
        """
        if self._ai_client is None:
            return {"success": False, "error": "AI 客户端不可用"}

        text = (resume_text or "").strip()
        if not text:
            return {"success": False, "error": "简历文本为空"}
        if len(text) > 12000:
            # 截断，避免超长上下文
            text = text[:12000]

        prompt = self._build_prompt(text)
        try:
            import asyncio

            result = await asyncio.to_thread(
                self._ai_client.complete_text,
                [{"role": "user", "content": prompt}],
                max_tokens=1200,
                temperature=0.3,
            )
        except Exception as exc:
            logger.warning("profile extraction failed: %s", exc)
            return {"success": False, "error": str(exc)}

        parsed = self._parse_result(result or "")
        if not parsed.get("name") and not parsed.get("tech_stack") and \
                not parsed.get("target_positions") and not parsed.get("resume_keywords"):
            logger.warning("profile extraction returned empty profile (model output unparseable)")
        return {"success": True, "profile": parsed}

    @staticmethod
    def _build_prompt(resume_text: str) -> str:
        return (
            "你是一位资深招聘顾问与简历解析专家。请仔细阅读以下简历文本，"
            "提取出用户的求职画像与关键词。输出必须是合法 JSON，不要添加任何额外说明。\n"
            "\n## 简历文本\n"
            f"{resume_text}"
            "\n\n## 输出要求\n"
            "请严格按以下 JSON 格式输出：\n"
            "{\n"
            '  "name": "姓名，未知则空字符串",\n'
            '  "city": "期望工作城市，未知则空字符串",\n'
            '  "years_of_experience": "工作年限，用数字或范围，如 3 或 3-5，未知则空字符串",\n'
            '  "tech_stack": ["技术栈/技能关键词列表，如 Python、React"],\n'
            '  "target_positions": ["目标岗位列表，如 后端开发、全栈工程师"],\n'
            '  "expected_salary": "期望薪资范围，如 20k-30k，未知则空字符串",\n'
            '  "education": "最高学历，如 本科/硕士，未知则空字符串",\n'
            '  "industry_preference": ["行业偏好列表，如 互联网、金融科技"],\n'
            '  "exclude_keywords": ["简历中明显不适合或希望排除的方向/关键词"],\n'
            '  "self_introduction": "一句话优势/个人简介，150字以内",\n'
            '  "resume_keywords": ["从简历中提取的10-20个核心关键词，用于职位匹配"]\n'
            "}\n"
            "注意：\n"
            "1. 只输出 JSON，不要 Markdown 代码块、不要解释。\n"
            "2. 无法推断的字段请用空字符串或空数组，不要编造。\n"
            "3. resume_keywords 应覆盖技能、岗位、行业、工具、业务领域。"
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
                return ProfileExtractor._empty_result()
            try:
                data = json.loads(match.group(0))
            except json.JSONDecodeError:
                return ProfileExtractor._empty_result()

        if not isinstance(data, dict):
            return ProfileExtractor._empty_result()

        def _str(key: str) -> str:
            value = data.get(key)
            if value is None or value == "":
                return ""
            if isinstance(value, (int, float)):
                return str(value)
            if isinstance(value, str):
                return value.strip()
            return ""

        def _list(key: str, limit: int | None = None) -> list[str]:
            value = data.get(key, [])
            if isinstance(value, str):
                value = [value]
            if isinstance(value, list):
                items = [str(v).strip() for v in value if str(v).strip()]
                if limit:
                    items = items[:limit]
                return items
            return []

        return {
            "name": _str("name"),
            "city": _str("city"),
            "years_of_experience": _str("years_of_experience"),
            "tech_stack": _list("tech_stack"),
            "target_positions": _list("target_positions"),
            "expected_salary": _str("expected_salary"),
            "education": _str("education"),
            "industry_preference": _list("industry_preference"),
            "exclude_keywords": _list("exclude_keywords"),
            "self_introduction": _str("self_introduction")[:300],
            "resume_keywords": _list("resume_keywords", 30),
        }

    @staticmethod
    def _empty_result() -> dict[str, Any]:
        return {
            "name": "",
            "city": "",
            "years_of_experience": "",
            "tech_stack": [],
            "target_positions": [],
            "expected_salary": "",
            "education": "",
            "industry_preference": [],
            "exclude_keywords": [],
            "self_introduction": "",
            "resume_keywords": [],
        }
