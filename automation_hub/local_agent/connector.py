"""local_agent.connector — 与 automation-hub 控制面的通信层。"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from local_agent.models import (
    DrainRequest,
    ExecutionAuthorizationRequest,
    ExecutionAuthorizationResponse,
    HeartbeatRequest,
    HeartbeatResponse,
    NodeRegisterRequest,
    NodeRegisterResponse,
    PolicyBundle,
    RevokeRequest,
    RunClaimRequest,
    RunClaimResponse,
    RunEventsRequest,
    SessionEventsRequest,
)

logger = logging.getLogger("local_agent.connector")


class HubConnector:
    """与 automation-hub 控制面的 HTTP 客户端。"""

    def __init__(self, base_url: str, token: str | None = None, timeout: float = 30) -> None:
        self.base_url = base_url.rstrip("/")
        self._token = token
        self._timeout = timeout
        self._node_id: str | None = None

    @property
    def node_id(self) -> str | None:
        return self._node_id

    def _headers(self) -> dict[str, str]:
        h: dict[str, str] = {"Content-Type": "application/json"}
        if self._token:
            h["Authorization"] = f"Bearer {self._token}"
        return h

    def _url(self, path: str) -> str:
        return f"{self.base_url}{path}"

    # ------------------------------------------------------------------
    # 节点注册
    # ------------------------------------------------------------------

    async def register(self, req: NodeRegisterRequest) -> NodeRegisterResponse:
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            r = await client.post(
                self._url("/agent-nodes/register"),
                json=req.model_dump(),
                headers=self._headers(),
            )
            r.raise_for_status()
            data = r.json()
            self._node_id = data["node_id"]
            self._token = data.get("issued_token", self._token)
            return NodeRegisterResponse(**data)

    # ------------------------------------------------------------------
    # 心跳
    # ------------------------------------------------------------------

    async def heartbeat(self, req: HeartbeatRequest) -> HeartbeatResponse:
        if not self._node_id:
            raise RuntimeError("not registered")
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            r = await client.post(
                self._url(f"/agent-nodes/{self._node_id}/heartbeat"),
                json=req.model_dump(),
                headers=self._headers(),
            )
            r.raise_for_status()
            return HeartbeatResponse(**r.json())

    # ------------------------------------------------------------------
    # 策略
    # ------------------------------------------------------------------

    async def fetch_policy(self) -> PolicyBundle:
        if not self._node_id:
            raise RuntimeError("not registered")
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            r = await client.get(
                self._url(f"/agent-nodes/{self._node_id}/policy-bundle"),
                headers=self._headers(),
            )
            r.raise_for_status()
            return PolicyBundle(**r.json())

    # ------------------------------------------------------------------
    # 会话事件
    # ------------------------------------------------------------------

    async def push_session_events(self, session_id: str, req: SessionEventsRequest) -> dict[str, Any]:
        if not self._node_id:
            raise RuntimeError("not registered")
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            r = await client.post(
                self._url(f"/agent-nodes/{self._node_id}/sessions/{session_id}/events"),
                json=req.model_dump(),
                headers=self._headers(),
            )
            r.raise_for_status()
            return r.json()

    # ------------------------------------------------------------------
    # 执行授权
    # ------------------------------------------------------------------

    async def request_authorization(self, req: ExecutionAuthorizationRequest) -> ExecutionAuthorizationResponse:
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            r = await client.post(
                self._url("/execution-authorizations"),
                json=req.model_dump(),
                headers=self._headers(),
            )
            r.raise_for_status()
            return ExecutionAuthorizationResponse(**r.json())

    # ------------------------------------------------------------------
    # tool-runs
    # ------------------------------------------------------------------

    async def create_tool_run(self, tool_id: str, args: dict[str, Any], reason: str = "") -> dict[str, Any]:
        payload = {
            "tool_id": tool_id,
            "args": args,
            "reason": reason,
        }
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            r = await client.post(
                self._url("/tool-runs"),
                json=payload,
                headers=self._headers(),
            )
            r.raise_for_status()
            return r.json()

    # ------------------------------------------------------------------
    # Run claim & events
    # ------------------------------------------------------------------

    async def claim_run(self, run_id: str) -> RunClaimResponse:
        if not self._node_id:
            raise RuntimeError("not registered")
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            r = await client.post(
                self._url(f"/tool-runs/{run_id}/claim"),
                json=RunClaimRequest(node_id=self._node_id).model_dump(),
                headers=self._headers(),
            )
            r.raise_for_status()
            return RunClaimResponse(**r.json())

    async def push_run_events(self, run_id: str, req: RunEventsRequest) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            r = await client.post(
                self._url(f"/tool-runs/{run_id}/events"),
                json=req.model_dump(),
                headers=self._headers(),
            )
            r.raise_for_status()
            return r.json()

    async def upload_artifact(self, run_id: str, filename: str, data: bytes) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            r = await client.post(
                self._url(f"/tool-runs/{run_id}/artifacts"),
                files={"file": (filename, data)},
                headers={"Authorization": f"Bearer {self._token}"} if self._token else {},
            )
            r.raise_for_status()
            return r.json()

    # ------------------------------------------------------------------
    # 节点管理
    # ------------------------------------------------------------------

    async def drain(self, reason: str = "") -> dict[str, Any]:
        if not self._node_id:
            raise RuntimeError("not registered")
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            r = await client.post(
                self._url(f"/agent-nodes/{self._node_id}/drain"),
                json=DrainRequest(reason=reason).model_dump(),
                headers=self._headers(),
            )
            r.raise_for_status()
            return r.json()

    async def revoke(self, reason: str = "") -> dict[str, Any]:
        if not self._node_id:
            raise RuntimeError("not registered")
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            r = await client.post(
                self._url(f"/agent-nodes/{self._node_id}/revoke"),
                json=RevokeRequest(reason=reason).model_dump(),
                headers=self._headers(),
            )
            r.raise_for_status()
            return r.json()

    # ------------------------------------------------------------------
    # 健康
    # ------------------------------------------------------------------

    async def health_check(self) -> bool:
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                r = await client.get(self._url("/health"), headers=self._headers())
                return r.status_code == 200
        except Exception:
            return False
