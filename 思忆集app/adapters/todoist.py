"""Todoist 任务适配器 — 注册为 AI 管家能力，支持任务的创建/列表/完成。

依赖：
  - 用户需在设置中配置 Todoist API Token
  - Todoist REST API v2

使用方法：
  用户说"帮我创建一个任务" → AI 调 todoist.task.create → Todoist 创建
"""
from __future__ import annotations

import logging
from typing import Any

from integrations.integration_http_client import ApiHttpClient
from adapters.base import AdapterStatus

logger = logging.getLogger("siyi.adapter.todoist")

TODOIST_API_BASE = "https://api.todoist.com/rest/v2"


class TodoistAdapterError(RuntimeError):
    """Todoist 适配器错误。"""


class TodoistAdapter:
    """Todoist 任务适配器 — 通过 REST API v2 操作 Todoist。"""

    def __init__(
        self,
        *,
        api_token: str = "",
        settings_repository=None,
        http_client: ApiHttpClient | None = None,
    ):
        self._api_token = api_token
        self._settings_repository = settings_repository
        self._http_client = http_client or ApiHttpClient()

    # ── P3-5: ExternalAdapter 协议实现 ───────────────────────
    @property
    def adapter_name(self) -> str:
        return "todoist"

    def health_check(self) -> AdapterStatus:
        """已配置 token → ONLINE，否则 UNCONFIGURED。"""
        if self.is_configured():
            return AdapterStatus.ONLINE
        return AdapterStatus.UNCONFIGURED

    def is_available(self) -> bool:
        return self.is_configured()

    @staticmethod
    def _decrypt_if_secure(value: Any) -> Any:
        from core.secure_key_storage import SecureKeyStorage

        if not isinstance(value, str):
            return value
        secure = SecureKeyStorage(namespace="com.siyi.apikey")
        if secure.is_secure_token(value):
            return secure.decrypt(value)
        return value

    def _get_token(self) -> str:
        token = self._api_token.strip()
        if not token and self._settings_repository is not None:
            from core.settings_schema import KEY_TODOIST_API_TOKEN
            token = str(self._settings_repository.get_setting(KEY_TODOIST_API_TOKEN, "") or "").strip()
            token = self._decrypt_if_secure(token)
        if not token:
            raise TodoistAdapterError("Todoist 未配置 API Token，请在设置中添加")
        return token

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._get_token()}",
            "Content-Type": "application/json",
        }

    def _request(self, method: str, path: str, **kwargs) -> Any:
        url = f"{TODOIST_API_BASE}{path}"
        try:
            resp = self._http_client.request(method, url, headers=self._headers(), **kwargs)
            if resp.status_code == 204:
                return {"success": True}
            data = resp.json() if resp.content else {}
            return data
        except Exception as exc:
            raise TodoistAdapterError(f"Todoist API 请求失败: {exc}") from exc

    # ── 公开 API ─────────────────────────────────────────────

    def list_tasks(self, project_id: str | None = None, limit: int = 20) -> dict[str, Any]:
        """列出 Todoist 任务。"""
        params: dict[str, Any] = {"limit": limit}
        if project_id:
            params["project_id"] = project_id
        result = self._request("GET", "/tasks", params=params)
        tasks = result if isinstance(result, list) else []
        return {"tasks": tasks, "count": len(tasks)}

    def create_task(
        self,
        content: str,
        description: str = "",
        project_id: str | None = None,
        priority: int = 1,
        due_string: str | None = None,
    ) -> dict[str, Any]:
        """创建 Todoist 任务。"""
        body: dict[str, Any] = {"content": content}
        if description:
            body["description"] = description
        if project_id:
            body["project_id"] = project_id
        if priority:
            body["priority"] = max(1, min(priority, 4))
        if due_string:
            body["due_string"] = due_string
        result = self._request("POST", "/tasks", json_body=body)
        return {"task": result, "success": True}

    def close_task(self, task_id: str) -> dict[str, Any]:
        """完成任务。"""
        return self._request("POST", f"/tasks/{task_id}/close")

    def list_projects(self) -> dict[str, Any]:
        """列出 Todoist 项目。"""
        result = self._request("GET", "/projects")
        projects = result if isinstance(result, list) else []
        return {"projects": projects, "count": len(projects)}

    def is_configured(self) -> bool:
        """检查是否已配置 API Token。"""
        try:
            return bool(self._get_token())
        except TodoistAdapterError:
            return False
