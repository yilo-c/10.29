"""浏览器上下文适配器 — 注册为 AI 管家能力，支持读取当前浏览器页面内容。

依赖：
  - browser_assistant_bridge: 本地 HTTP 桥接服务（与浏览器扩展通信）
  - integration_http_client: HTTP 客户端

使用方法：
  用户"看看这个页面" → AI 调 browser.context.get → 获取当前页内容
"""
from __future__ import annotations

import json
import logging
from typing import Any
from urllib.parse import urljoin

from integrations.integration_http_client import ApiHttpClient
from adapters.base import AdapterStatus

logger = logging.getLogger("siyi.adapter.browser")

BROWSER_BRIDGE_DEFAULT_PORT = 18766


class BrowserAdapterError(RuntimeError):
    """浏览器适配器错误。"""


class BrowserAdapter:
    """浏览器上下文适配器 — 通过本地桥接服务获取浏览器页面内容。"""

    def __init__(
        self,
        *,
        bridge_port: int = BROWSER_BRIDGE_DEFAULT_PORT,
        settings_repository=None,
        http_client: ApiHttpClient | None = None,
    ):
        self._settings_repository = settings_repository
        self._http_client = http_client or ApiHttpClient()
        self._bridge_port = self._resolve_port(bridge_port)
        self._base_url = f"http://127.0.0.1:{self._bridge_port}"

    # ── P3-5: ExternalAdapter 协议实现 ───────────────────────
    @property
    def adapter_name(self) -> str:
        return "browser"

    def health_check(self) -> AdapterStatus:
        """桥接运行中 → ONLINE，否则 UNCONFIGURED。"""
        if self.is_bridge_running():
            return AdapterStatus.ONLINE
        return AdapterStatus.UNCONFIGURED

    def is_available(self) -> bool:
        return self.is_bridge_running()

    def _resolve_port(self, default_port: int) -> int:
        if self._settings_repository is None:
            return default_port
        try:
            return int(self._settings_repository.get_setting("browser_assistant_bridge_port", default_port))
        except (TypeError, ValueError):
            return default_port

    def _get_session_token(self) -> str:
        """尝试获取桥接服务的 session token。"""
        try:
            resp = self._http_client.request("GET", f"{self._base_url}/api/browser-assistant/status")
            data = resp.json() if resp.content else {}
            if isinstance(data, dict):
                status = data.get("status") or {}
                return str(status.get("token_expires_at") or "")
        except Exception:
            logger.debug("获取 session token 失败", exc_info=True)
        return ""

    def is_bridge_running(self) -> bool:
        """检查浏览器桥接服务是否正在运行。

        P3-5 审查修复：原实现请求 ``/health``，但后端 WS 服务器也暴露 ``/health``
        且默认监听同一端口 18765，导致只要后端运行就返回 200（假阳性）。
        改为请求桥接专属端点 ``/api/browser-assistant/status``，后端无此路由会返回
        404，从而准确区分桥接服务与后端。
        """
        try:
            resp = self._http_client.request(
                "GET", f"{self._base_url}/api/browser-assistant/status", timeout=(2.0, 5.0),
            )
            return resp.status_code == 200
        except Exception:
            logger.debug("浏览器桥接健康检查失败", exc_info=True)
            return False

    def get_page_context(self) -> dict[str, Any]:
        """获取当前浏览器页面的上下文（标题、URL、选中文本等）。

        Returns:
            dict: {
                "success": True/False,
                "title": "页面标题",
                "url": "页面URL",
                "content": "页面正文摘要",
                "selection": "用户选中的文本(如有)",
            }
        """
        if not self.is_bridge_running():
            return {"success": False, "reason": "bridge_not_running",
                    "hint": "请确保浏览器扩展已连接（浏览器桥接服务未运行）"}
        try:
            resp = self._http_client.request(
                "POST",
                f"{self._base_url}/api/browser-assistant/capture",
                json_body={},
                timeout=(3.0, 15.0),
            )
            data = resp.json() if resp.content else {}
            if isinstance(data, dict):
                return {
                    "success": True,
                    "title": str(data.get("title") or data.get("pageTitle") or ""),
                    "url": str(data.get("url") or data.get("pageUrl") or ""),
                    "content": str(data.get("content") or data.get("textContent") or ""),
                    "selection": str(data.get("selection") or ""),
                }
            return {"success": False, "reason": "invalid_response"}
        except Exception as exc:
            logger.warning("browser.context.get failed: %s", exc)
            return {"success": False, "reason": str(exc)}

    def summarize_page(self) -> dict[str, Any]:
        """对当前浏览器页面进行 AI 总结。

        Returns:
            dict: {"success": True/False, "summary": "总结内容", ...}
        """
        if not self.is_bridge_running():
            return {"success": False, "reason": "bridge_not_running"}
        try:
            resp = self._http_client.request(
                "POST",
                f"{self._base_url}/api/browser-assistant/summarize",
                json_body={},
                timeout=(3.0, 30.0),
            )
            data = resp.json() if resp.content else {}
            if isinstance(data, dict):
                return {"success": True, "summary": str(data.get("summary") or "")}
            return {"success": False, "reason": "invalid_response"}
        except Exception as exc:
            logger.warning("browser.summarize failed: %s", exc)
            return {"success": False, "reason": str(exc)}
