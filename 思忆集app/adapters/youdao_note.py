"""有道云笔记适配器 — 注册为 AI 管家能力，支持笔记的创建/列表/搜索。

依赖：
  - youdao_oauth_provider: OAuth2 认证
  - integration_http_client: HTTP 客户端
  - api_provider_registry: Provider 注册表

使用方法：
  用户说"帮我记下这个" → AI 调 youdao.note.create → 有道云创建笔记
"""
from __future__ import annotations

import logging
from typing import Any

from integrations.integration_http_client import ApiHttpClient
from integrations.youdao_oauth_provider import YoudaoOAuthProvider
from adapters.base import AdapterStatus

logger = logging.getLogger("siyi.adapter.youdao_note")

YOUDAO_NOTE_API_BASE = "https://note.youdao.com"


class YoudaoNoteAdapterError(RuntimeError):
    """有道云笔记适配器错误。"""


class YoudaoNoteAdapter:
    """有道云笔记适配器 — 通过 OAuth2 访问有道云 Open API。"""

    def __init__(
        self,
        *,
        oauth_provider: YoudaoOAuthProvider | None = None,
        integration_hub=None,
        provider_registry=None,
        settings_repository=None,
        http_client: ApiHttpClient | None = None,
    ):
        self._oauth_provider = oauth_provider
        self._integration_hub = integration_hub
        self._provider_registry = provider_registry
        self._settings_repository = settings_repository
        self._http_client = http_client or ApiHttpClient()

    # ── P3-5: ExternalAdapter 协议实现 ───────────────────────
    @property
    def adapter_name(self) -> str:
        return "youdao_note"

    def health_check(self) -> AdapterStatus:
        """已完成 OAuth 授权 → ONLINE，否则 UNCONFIGURED。"""
        if self.is_authorized():
            return AdapterStatus.ONLINE
        return AdapterStatus.UNCONFIGURED

    def is_available(self) -> bool:
        return self.is_authorized()

    def _get_oauth_provider(self) -> YoudaoOAuthProvider:
        if self._oauth_provider is not None:
            return self._oauth_provider
        if self._provider_registry is None or self._integration_hub is None:
            raise YoudaoNoteAdapterError("有道云适配器未配置 OAuth provider")
        self._oauth_provider = YoudaoOAuthProvider(
            integration_hub=self._integration_hub,
            provider_registry=self._provider_registry,
            settings_repository=self._settings_repository,
        )
        return self._oauth_provider

    def _get_access_token(self) -> str:
        """获取有效的 OAuth2 access token。"""
        if self._integration_hub is None:
            raise YoudaoNoteAdapterError("integration_hub 未配置")
        token = self._integration_hub.get_oauth_token("youdao")
        if not token:
            raise YoudaoNoteAdapterError("有道云未授权，请先完成 OAuth 登录")
        access_token = str(token.get("access_token") or "").strip()
        if not access_token:
            raise YoudaoNoteAdapterError("有道云 access_token 无效")
        return access_token

    def _headers(self) -> dict[str, str]:
        token = self._get_access_token()
        return {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }

    def _request(self, method: str, path: str, **kwargs) -> dict[str, Any]:
        """发起 API 请求，自动处理 token 刷新。"""
        url = f"{YOUDAO_NOTE_API_BASE}{path}"
        try:
            resp = self._http_client.request(method, url, headers=self._headers(), **kwargs)
            data = resp.json() if resp.content else {}
            if not isinstance(data, dict):
                return {"raw": str(resp.content)}
            return data
        except Exception as exc:
            # 尝试刷新 token 后重试一次
            if "401" in str(exc):
                try:
                    self._get_oauth_provider().refresh_access_token()
                    resp = self._http_client.request(method, url, headers=self._headers(), **kwargs)
                    data = resp.json() if resp.content else {}
                    return data if isinstance(data, dict) else {"raw": str(resp.content)}
                except Exception as retry_exc:
                    raise YoudaoNoteAdapterError(f"Token 刷新后仍失败: {retry_exc}") from retry_exc
            raise YoudaoNoteAdapterError(f"API 请求失败: {exc}") from exc

    # ── 公开 API ─────────────────────────────────────────────

    def list_notes(self, limit: int = 20, offset: int = 0) -> dict[str, Any]:
        """列出有道云笔记。"""
        return self._request("GET", "/api/note/list", params={"limit": limit, "offset": offset})

    def get_note(self, note_id: str) -> dict[str, Any]:
        """获取单篇笔记详情。"""
        return self._request("GET", f"/api/note/{note_id}")

    def create_note(self, title: str, content: str, folder: str | None = None) -> dict[str, Any]:
        """创建新笔记。"""
        body: dict[str, Any] = {"title": title, "content": content}
        if folder:
            body["folder"] = folder
        return self._request("POST", "/api/note/create", json_body=body)

    def update_note(self, note_id: str, title: str | None = None, content: str | None = None) -> dict[str, Any]:
        """更新笔记内容。"""
        body: dict[str, Any] = {}
        if title is not None:
            body["title"] = title
        if content is not None:
            body["content"] = content
        return self._request("POST", f"/api/note/{note_id}/update", json_body=body)

    def delete_note(self, note_id: str) -> dict[str, Any]:
        """删除笔记。"""
        return self._request("POST", f"/api/note/{note_id}/delete")

    def is_authorized(self) -> bool:
        """检查是否已完成 OAuth 授权。"""
        if self._integration_hub is None:
            return False
        try:
            token = self._integration_hub.get_oauth_token("youdao")
            return bool(token and token.get("access_token"))
        except Exception:
            logger.warning("is_authenticated failed", exc_info=True)
            return False
