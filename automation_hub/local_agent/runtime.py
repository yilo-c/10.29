"""local_agent.runtime — 主运行循环。"""

from __future__ import annotations

import asyncio
import logging
import platform
import uuid
from typing import Any

from local_agent.bus import InboundMessage, LocalBus, OutboundMessage
from local_agent.connector import HubConnector
from local_agent.models import (
    ExecutionAuthorizationRequest,
    HeartbeatRequest,
    NodeRegisterRequest,
    NodeStatus,
    RiskLevel,
    RunClaimStatus,
    RunEvent,
    RunEventsRequest,
    SessionEvent,
    SessionEventType,
    SessionEventsRequest,
)
from local_agent.policy import PolicyCache
from local_agent.session import SessionManager
from local_agent.stream import StreamBuffer, StreamChunk

logger = logging.getLogger("local_agent.runtime")


class AgentRuntime:
    """本地代理主循环。

    职责：
    1. 注册到控制面
    2. 维持心跳
    3. 拉取策略
    4. 从 Bus 读取用户消息 → 策略检查 → 执行 → 回传结果
    """

    def __init__(
        self,
        connector: HubConnector,
        bus: LocalBus,
        session_mgr: SessionManager,
        policy: PolicyCache,
        adapters: dict[str, Any] | None = None,
        heartbeat_interval: float = 30.0,
        policy_refresh_interval: float = 120.0,
    ) -> None:
        self.connector = connector
        self.bus = bus
        self.session_mgr = session_mgr
        self.policy = policy
        self.adapters: dict[str, Any] = adapters or {}
        self._heartbeat_interval = heartbeat_interval
        self._policy_refresh_interval = policy_refresh_interval
        self._running = False
        self._tasks: list[asyncio.Task[None]] = []
        self._status = NodeStatus.OFFLINE

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """注册并启动所有后台任务。"""
        logger.info("agent runtime starting …")
        self._running = True

        # 1. 注册
        hostname = platform.node()
        reg_resp = await self.connector.register(
            NodeRegisterRequest(
                hostname=hostname,
                labels={"os": platform.system(), "arch": platform.machine()},
                capabilities=list(self.adapters.keys()),
            )
        )
        logger.info("registered as node %s", reg_resp.node_id)

        # 2. 首次策略拉取
        await self._refresh_policy()

        # 3. 后台任务
        self._status = NodeStatus.ONLINE
        self._tasks.append(asyncio.create_task(self._heartbeat_loop()))
        self._tasks.append(asyncio.create_task(self._policy_loop()))
        self._tasks.append(asyncio.create_task(self._inbound_loop()))
        logger.info("agent runtime started")

    async def stop(self) -> None:
        """优雅停机。"""
        logger.info("agent runtime stopping …")
        self._running = False
        self._status = NodeStatus.DRAINING
        for t in self._tasks:
            t.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()
        try:
            await self.connector.drain(reason="shutdown")
        except Exception:
            logger.warning("drain failed, ignored")
        self._status = NodeStatus.OFFLINE
        logger.info("agent runtime stopped")

    # ------------------------------------------------------------------
    # 后台循环
    # ------------------------------------------------------------------

    async def _heartbeat_loop(self) -> None:
        while self._running:
            try:
                await self.connector.heartbeat(
                    HeartbeatRequest(
                        status=self._status.value,
                        active_runs=0,
                        cpu_percent=0.0,
                        mem_percent=0.0,
                    )
                )
            except Exception:
                logger.warning("heartbeat failed")
            await asyncio.sleep(self._heartbeat_interval)

    async def _policy_loop(self) -> None:
        while self._running:
            await asyncio.sleep(self._policy_refresh_interval)
            await self._refresh_policy()

    async def _refresh_policy(self) -> None:
        try:
            bundle = await self.connector.fetch_policy()
            self.policy.update(bundle)
            logger.debug("policy refreshed, version=%s", bundle.version)
        except Exception:
            logger.warning("policy refresh failed, using cached")

    # ------------------------------------------------------------------
    # 消息处理
    # ------------------------------------------------------------------

    async def _inbound_loop(self) -> None:
        """从 Bus 取入站消息，处理后发出站消息。"""
        while self._running:
            msg = await self.bus.recv_inbound()
            asyncio.create_task(self._handle(msg))

    async def _handle(self, msg: InboundMessage) -> None:
        session = self.session_mgr.get_or_create(msg.session_id)
        session.add_user_message(msg.content)

        # 判断是否为工具执行请求
        tool_name = msg.metadata.get("tool")
        if tool_name:
            await self._handle_tool(msg, tool_name)
        else:
            # 普通对话 → 通过 channel 回复（当前仅 echo）
            await self.bus.send_outbound(
                OutboundMessage(
                    session_id=msg.session_id,
                    content=f"[agent] 收到: {msg.content}",
                )
            )

    async def _handle_tool(self, msg: InboundMessage, tool_name: str) -> None:
        """工具执行链: 策略预检 → 授权 → 执行 → 回传。"""
        session_id = msg.session_id
        args: dict[str, Any] = msg.metadata.get("args", {})

        # 1. 策略预检
        check = self.policy.pre_check(tool_name, args)
        if not check["allowed"]:
            await self.bus.send_outbound(
                OutboundMessage(session_id=session_id, content=f"[policy] 拒绝: {check['reason']}")
            )
            return

        # 2. 需审批？
        if check.get("requires_approval"):
            auth_resp = await self.connector.request_authorization(
                ExecutionAuthorizationRequest(
                    node_id=self.connector.node_id or "",
                    tool_name=tool_name,
                    args=args,
                    risk_level=RiskLevel.HIGH.value,
                )
            )
            if auth_resp.decision != "approved":
                await self.bus.send_outbound(
                    OutboundMessage(session_id=session_id, content=f"[auth] 未通过: {auth_resp.reason}")
                )
                return

        # 3. 领取 run
        run_id = msg.metadata.get("run_id", "")
        if not run_id:
            try:
                created = await self.connector.create_tool_run(tool_name, args, reason="local-agent request")
                run_id = created.get("run_id") or str(uuid.uuid4())
            except Exception as e:
                await self.bus.send_outbound(
                    OutboundMessage(session_id=session_id, content=f"[run] 创建失败: {e}")
                )
                return

        try:
            claim = await self.connector.claim_run(run_id)
            if claim.status != RunClaimStatus.CLAIMED.value:
                await self.bus.send_outbound(
                    OutboundMessage(session_id=session_id, content=f"[claim] 失败: {claim.status}")
                )
                return
        except Exception as e:
            await self.bus.send_outbound(
                OutboundMessage(session_id=session_id, content=f"[claim] 异常: {e}")
            )
            return

        # 4. 执行
        adapter = self.adapters.get(tool_name)
        if adapter is None:
            await self.bus.send_outbound(
                OutboundMessage(session_id=session_id, content=f"[adapter] 未找到 adapter: {tool_name}")
            )
            return

        # 流式输出缓冲
        async def _flush(chunks: list[StreamChunk]) -> None:
            events = [RunEvent(type="output", data=c.content) for c in chunks]
            await self.connector.push_run_events(run_id, RunEventsRequest(events=events))

        stream_buf = StreamBuffer(flush_fn=_flush, flush_interval=1.5)
        await stream_buf.start()

        try:
            result = await adapter.execute(args, stream=stream_buf)
            await stream_buf.stop()

            # 5. 上报最终结果
            await self.connector.push_run_events(
                run_id,
                RunEventsRequest(events=[RunEvent(type="completed", data=str(result))]),
            )
            await self.bus.send_outbound(
                OutboundMessage(session_id=session_id, content=f"[result] {result}")
            )
        except Exception as exc:
            await stream_buf.stop()
            await self.connector.push_run_events(
                run_id,
                RunEventsRequest(events=[RunEvent(type="failed", data=str(exc))]),
            )
            await self.bus.send_outbound(
                OutboundMessage(session_id=session_id, content=f"[error] {exc}")
            )

        # 6. 会话事件同步
        try:
            await self.connector.push_session_events(
                session_id,
                SessionEventsRequest(events=[
                    SessionEvent(
                        type=SessionEventType.TOOL_CALL.value,
                        session_id=session_id,
                        data={"tool": tool_name, "run_id": run_id},
                    )
                ]),
            )
        except Exception:
            logger.debug("session event push failed (non-critical)")
