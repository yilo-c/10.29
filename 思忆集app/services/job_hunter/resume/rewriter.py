"""AI 简历改写器。

基于用户原始简历，按目标岗位/行业/风格生成优化后的简历内容。
核心约束：只优化表述，不虚构任何项目、公司、职位、时间。
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class RewriteResult:
    """简历改写结果。"""

    success: bool
    full_text: str
    changes_summary: str
    sections: dict[str, str]
    keywords_added: list[str]
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "success": self.success,
            "full_text": self.full_text,
            "changes_summary": self.changes_summary,
            "sections": self.sections,
            "keywords_added": self.keywords_added,
            "error": self.error,
        }


class ResumeRewriter:
    """基于 LLM 的简历改写器。"""

    def __init__(self, ai_client: Any) -> None:
        self._ai_client = ai_client

    async def rewrite(
        self,
        resume_text: str,
        target_role: str = "",
        industry: str = "",
        style: str = "",
    ) -> RewriteResult:
        """生成优化后的完整简历。"""
        if self._ai_client is None:
            return RewriteResult(False, "", "", {}, [], error="AI 客户端不可用")

        text = (resume_text or "").strip()
        if not text:
            return RewriteResult(False, "", "", {}, [], error="简历文本为空")
        if len(text) > 12000:
            text = text[:12000]

        prompt = self._build_rewrite_prompt(text, target_role, industry, style)
        try:
            import asyncio

            result = await asyncio.to_thread(
                self._ai_client.complete_text,
                [{"role": "user", "content": prompt}],
                max_tokens=2000,
                temperature=0.4,
            )
        except Exception as exc:
            logger.warning("resume rewrite failed: %s", exc)
            return RewriteResult(False, "", "", {}, [], error=str(exc))

        parsed = self._parse_result(result or "")
        return RewriteResult(success=True, **parsed)

    async def rewrite_section(
        self, resume_text: str, section: str, target_role: str = ""
    ) -> dict[str, Any]:
        """仅改写简历中的指定章节。"""
        if self._ai_client is None:
            return {"success": False, "error": "AI 客户端不可用"}

        text = (resume_text or "").strip()
        if not text:
            return {"success": False, "error": "简历文本为空"}
        if len(text) > 12000:
            text = text[:12000]

        prompt = (
            "你是一位资深简历优化顾问。请仅优化以下简历中的指定章节，不修改其他内容，"
            "不编造经历。输出必须是合法 JSON。\n\n"
            f"目标岗位：{target_role or '未指定'}\n"
            f"指定章节：{section}\n\n"
            "## 简历全文\n"
            f"{text}\n\n"
            "## 输出要求\n"
            "{\n"
            f'  "rewritten_section": "优化后的{section}内容",\n'
            '  "changes": "改动说明"\n'
            "}\n"
        )
        try:
            import asyncio

            result = await asyncio.to_thread(
                self._ai_client.complete_text,
                [{"role": "user", "content": prompt}],
                max_tokens=1200,
                temperature=0.4,
            )
        except Exception as exc:
            logger.warning("resume rewrite_section failed: %s", exc)
            return {"success": False, "error": str(exc)}

        parsed = self._parse_simple_result(result or "")
        return dict({"success": True}, **parsed)

    @staticmethod
    def _build_rewrite_prompt(
        resume_text: str, target_role: str, industry: str, style: str
    ) -> str:
        style_desc = {
            "concise": "简洁精炼，每句话都有信息密度",
            "enthusiastic": "积极主动，突出热情与驱动力",
            "technical": "技术导向，突出架构、性能、技术栈",
        }.get(style, "专业稳重，突出成果与匹配度")

        target_hint = f"目标岗位：{target_role}\n" if target_role else ""
        industry_hint = f"目标行业：{industry}\n" if industry else ""

        return (
            "你是一位资深简历优化顾问。请基于以下原始简历，生成一份优化后的简历。必须严格遵守：\n"
            "1. 基于原文经历优化表述，不虚构、不编造任何项目、公司、职位、时间。\n"
            "2. 突出与目标岗位的匹配点，使用行业关键词。\n"
            "3. 用 STAR 法则和数据量化成果。\n"
            "4. 输出必须是合法 JSON，不要 Markdown 代码块。\n\n"
            f"{target_hint}"
            f"{industry_hint}"
            f"风格要求：{style_desc}"
            "\n\n## 原始简历\n"
            f"{resume_text}"
            "\n\n## 输出要求\n"
            "{\n"
            '  "full_text": "完整优化后的简历（Markdown 格式）",\n'
            '  "changes_summary": "改动摘要，3-5 条",\n'
            '  "sections": {\n'
            '    "self_introduction": "个人简介",\n'
            '    "skills": "技能栈",\n'
            '    "experience": "工作经历",\n'
            '    "projects": "项目经历"\n'
            "  },\n"
            '  "keywords_added": ["新增或强调的关键词"]\n'
            "}\n"
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
                return ResumeRewriter._empty_result()
            try:
                data = json.loads(match.group(0))
            except json.JSONDecodeError:
                return ResumeRewriter._empty_result()

        if not isinstance(data, dict):
            return ResumeRewriter._empty_result()

        sections = data.get("sections") or {}
        if not isinstance(sections, dict):
            sections = {}

        return {
            "full_text": str(data.get("full_text", "")).strip(),
            "changes_summary": str(data.get("changes_summary", "")).strip(),
            "sections": {str(k): str(v).strip() for k, v in sections.items() if v},
            "keywords_added": _str_list(data.get("keywords_added")),
        }

    @staticmethod
    def _parse_simple_result(text: str) -> dict[str, Any]:
        text = text.strip()
        if text.startswith("```"):
            text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.DOTALL)
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            match = re.search(r"\{.*\}", text, re.DOTALL)
            if not match:
                return {"rewritten_section": "", "changes": ""}
            try:
                data = json.loads(match.group(0))
            except json.JSONDecodeError:
                return {"rewritten_section": "", "changes": ""}

        if not isinstance(data, dict):
            return {"rewritten_section": "", "changes": ""}

        return {
            "rewritten_section": str(data.get("rewritten_section", "")).strip(),
            "changes": str(data.get("changes", "")).strip(),
        }

    @staticmethod
    def _empty_result() -> dict[str, Any]:
        return {
            "full_text": "",
            "changes_summary": "",
            "sections": {},
            "keywords_added": [],
        }


def _str_list(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]
    return []
