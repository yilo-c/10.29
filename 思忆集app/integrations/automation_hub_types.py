from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Generic, TypeVar

T = TypeVar("T")
E = TypeVar("E")


class AutomationHubClientError(RuntimeError):
    """automation-hub 桌面端接入失败。"""


@dataclass(frozen=True)
class AutomationHubConnectionConfig:
    base_url: str | None = None
    api_token: str | None = None

    @property
    def has_base_url(self) -> bool:
        return bool(self.base_url)

    @property
    def has_token(self) -> bool:
        return bool(self.api_token)


@dataclass(frozen=True)
class AutomationHubRequestSpec:
    method: str
    path: str
    require_token: bool = True
    json_body: dict | None = None
    extra_headers: dict | None = None

    def resolve_url(self, base_url: str) -> str:
        return f"{base_url.rstrip('/')}{self.path}"


# ── 泛型响应类型 ──


@dataclass(frozen=True)
class SyncResponse(Generic[T]):
    items: list[T]
    next_cursor: str | None = None


@dataclass(frozen=True)
class UpsertResponse(Generic[E]):
    item: E


# ── 具体类型别名（向后兼容） ──

AutomationHubNoteSyncResponse = SyncResponse[dict]
AutomationHubNoteUpsertResponse = UpsertResponse[dict]
AutomationHubTaskSyncResponse = SyncResponse[dict]
AutomationHubTaskUpsertResponse = UpsertResponse[dict]
AutomationHubNewsSyncResponse = SyncResponse[dict]
AutomationHubNewsUpsertResponse = UpsertResponse[dict]
AutomationHubTranslationSyncResponse = SyncResponse[dict]
AutomationHubTranslationUpsertResponse = UpsertResponse[dict]
AutomationHubWordbookSyncResponse = SyncResponse[dict]
AutomationHubWordbookUpsertResponse = UpsertResponse[dict]
AutomationHubWordSyncResponse = SyncResponse[dict]
AutomationHubWordUpsertResponse = UpsertResponse[dict]


@dataclass(frozen=True)
class AutomationHubAgentAskResponse:
    plan_id: str
    summary: str
    status: str
    steps_completed: int
    total_steps: int
    session_id: str
    execution_time: float
    pending_approvals: list[str]


@dataclass(frozen=True)
class AutomationHubAgentPlanResponse:
    plan_id: str
    task_type: str
    steps: list[dict[str, Any]]
    estimated_duration: int
