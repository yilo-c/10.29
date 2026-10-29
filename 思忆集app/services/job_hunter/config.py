from __future__ import annotations

import logging
from dataclasses import asdict, dataclass, field
from typing import Any

from pydantic import BaseModel, Field, ValidationError, field_validator

logger = logging.getLogger(__name__)

SETTINGS_KEY = "job_hunter_profile"


class JobHunterProfileSchema(BaseModel):
    """求职画像的 pydantic 校验模型。"""

    name: str = ""
    city: str = ""
    years_of_experience: int = 0
    tech_stack: list[str] = Field(default_factory=list)
    target_positions: list[str] = Field(default_factory=list)
    expected_salary: str = ""
    education: str = ""
    industry_preference: list[str] = Field(default_factory=list)
    exclude_keywords: list[str] = Field(default_factory=list)
    self_introduction: str = ""
    custom_prompt: str = ""
    resume_text: str = ""
    resume_keywords: list[str] = Field(default_factory=list)

    @field_validator(
        "tech_stack",
        "target_positions",
        "industry_preference",
        "exclude_keywords",
        "resume_keywords",
        mode="before",
    )
    @classmethod
    def _ensure_list(cls, v: Any) -> list[str]:
        if v is None:
            return []
        if isinstance(v, str):
            return [s.strip() for s in v.split(",") if s.strip()]
        if isinstance(v, list):
            return [str(s).strip() for s in v if str(s).strip()]
        return []


@dataclass
class JobHunterProfile:
    """求职画像。"""

    name: str = ""
    city: str = ""
    years_of_experience: int = 0
    tech_stack: list[str] = field(default_factory=list)
    target_positions: list[str] = field(default_factory=list)
    expected_salary: str = ""
    education: str = ""
    industry_preference: list[str] = field(default_factory=list)
    exclude_keywords: list[str] = field(default_factory=list)
    self_introduction: str = ""
    custom_prompt: str = ""
    resume_text: str = ""
    resume_keywords: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_prompt_text(self) -> str:
        """渲染为可注入 LLM prompt 的画像文本。"""
        parts = [
            f"期望城市：{self.city or '不限'}",
            f"工作年限：{self.years_of_experience}年" if self.years_of_experience else "工作年限：不限",
            f"技术栈：{', '.join(self.tech_stack) if self.tech_stack else '未填写'}",
            f"目标岗位：{', '.join(self.target_positions) if self.target_positions else '未填写'}",
            f"期望薪资：{self.expected_salary or '未填写'}",
            f"学历：{self.education or '未填写'}",
            f"行业偏好：{', '.join(self.industry_preference) if self.industry_preference else '未填写'}",
            f"排除关键词：{', '.join(self.exclude_keywords) if self.exclude_keywords else '无'}",
        ]
        if self.self_introduction:
            parts.append(f"个人简介：{self.self_introduction[:300]}")
        if self.custom_prompt:
            parts.append(f"自定义偏好：{self.custom_prompt[:300]}")
        if self.resume_keywords:
            parts.append(f"简历关键词：{', '.join(self.resume_keywords)}")
        return "\n".join(parts)

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "JobHunterProfile":
        if not data:
            return cls()
        try:
            validated = JobHunterProfileSchema.model_validate(data)
            return cls(**validated.model_dump())
        except ValidationError as exc:
            logger.warning("job hunter profile validation failed: %s", exc)
            return cls()


def get_profile(settings_repository: Any) -> JobHunterProfile:
    """从设置仓库读取求职画像，失败时回退到空画像。"""
    if settings_repository is None:
        return JobHunterProfile()
    try:
        raw = settings_repository.get_setting(SETTINGS_KEY, {})
    except Exception as exc:
        logger.warning("read job_hunter_profile failed: %s", exc)
        return JobHunterProfile()
    if not isinstance(raw, dict):
        return JobHunterProfile()
    return JobHunterProfile.from_dict(raw)


def set_profile(settings_repository: Any, profile: JobHunterProfile) -> dict[str, Any]:
    """写入求职画像，返回 {success, error?} 结果字典。"""
    if settings_repository is None:
        return {"success": False, "error": "settings_repository unavailable"}
    try:
        validated = JobHunterProfileSchema.model_validate(profile.to_dict())
        settings_repository.set_setting(SETTINGS_KEY, validated.model_dump())
        return {"success": True, "profile": validated.model_dump()}
    except ValidationError as exc:
        logger.warning("save job_hunter_profile validation failed: %s", exc)
        return {"success": False, "error": f"validation failed: {exc}"}
    except Exception as exc:
        logger.warning("save job_hunter_profile failed: %s", exc)
        return {"success": False, "error": str(exc)}
