from __future__ import annotations

from dataclasses import dataclass


class AIServiceError(RuntimeError):
    """AI 服务配置或调用失败。

    Attributes:
        transient: 是否为瞬态错误（可重试，例如 429/502/503/504）。
        status: HTTP 状态码（如果可用）。
    """
    def __init__(
        self,
        message: str,
        *,
        transient: bool = False,
        status: int | None = None,
        retry_after_seconds: float | None = None,
        error_kind: str | None = None,
    ):
        super().__init__(message)
        self.transient = bool(transient)
        self.status = status
        self.retry_after_seconds = retry_after_seconds
        self.error_kind = error_kind or "unknown_error"


@dataclass(frozen=True)
class OpenAICompatibleConfig:
    api_key: str | None = None
    base_url: str | None = None
    model: str | None = None
    timeout: int = 60

    @property
    def is_configured(self) -> bool:
        return bool(self.api_key and self.base_url and self.model)


@dataclass(frozen=True)
class UrlLearningAISummary:
    summary: str
    key_points: list[str]
    study_suggestions: list[str]
    model: str
