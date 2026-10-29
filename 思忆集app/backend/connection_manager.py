"""WebSocket 连接管理器。

负责：
- 连接注册 / 反查 / 移除
- 空闲连接清理
- 心跳保活（PING / PONG 监控）
- 优雅关闭（close_all）

之前版本是被截断的占位符（"minimal replacement for corrupted original"），
仅保留 add/remove/get/cleanup_idle/broadcast 五个方法，但 ws_server.py 调用的
find_by_ws / remove_by_ws / close_all / start_heartbeat（实质实现）/ stop_heartbeat
全部缺失，导致：
- WS PONG 处理 AttributeError → 心跳完全失效
- 客户端断开时 _cleanup_connection 抛 AttributeError → 连接泄漏
- 服务器优雅关闭抛错

本次补齐所有调用点，保持原有签名兼容。
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any

from aiohttp import web

from core.errors import ConnectionLimitExceededError

logger = logging.getLogger("siyi.backend.connection_manager")


@dataclass
class ConnectionInfo:
    """单个 WebSocket 连接的元数据。

    使用 dataclass 而非 dict 以获得 attribute 访问（ws_server.py:379 用
    `info.last_pong = ...` 设置属性）。
    """

    id: str
    peer: str | None
    ws: web.WebSocketResponse
    connected_at: float = field(default_factory=time.time)
    last_pong: float = 0.0  # P2-10: 最后收到 pong 的时间戳（0 = 从未收到）
    last_ping_at: float = 0.0


class ConnectionManager:
    """线程不安全（asyncio 单线程）的 WebSocket 连接注册中心。"""

    def __init__(
        self,
        max_connections: int = 100,
        heartbeat_interval: float = 30.0,
        idle_timeout: float = 300.0,
    ) -> None:
        self.max_connections = max_connections
        self.heartbeat_interval = heartbeat_interval
        self.idle_timeout = idle_timeout
        self._connections: dict[str, ConnectionInfo] = {}
        self._total_connections = 0
        self._cid_seq = 0
        self._heartbeat_task: asyncio.Task | None = None
        self._stopping = False

    # ---------- 注册 / 反查 / 移除 ----------

    async def add_connection(self, ws: web.WebSocketResponse, peer: str | None) -> str:
        """注册新连接，返回 conn_id。"""
        if len(self._connections) >= self.max_connections:
            logger.warning("Reject connection from %s: max_connections=%d reached", peer, self.max_connections)
            raise ConnectionLimitExceededError(f"max_connections={self.max_connections} reached")
        self._cid_seq += 1
        cid = f"c{self._cid_seq}"
        info = ConnectionInfo(id=cid, peer=peer, ws=ws)
        self._connections[cid] = info
        self._total_connections += 1
        logger.info(
            "Connection added: id=%s peer=%s active=%d/%d",
            cid,
            peer,
            len(self._connections),
            self.max_connections,
        )
        return cid

    def remove_connection(self, conn_id: str) -> None:
        """按 conn_id 移除（同步）。"""
        self._connections.pop(conn_id, None)

    def get_connection(self, conn_id: str) -> ConnectionInfo | None:
        return self._connections.get(conn_id)

    def find_by_ws(self, ws: web.WebSocketResponse) -> ConnectionInfo | None:
        """ws_server.py PONG 处理需要按 ws 反查 ConnectionInfo。"""
        for info in self._connections.values():
            if info.ws is ws:
                return info
        return None

    async def remove_by_ws(self, ws: web.WebSocketResponse) -> str | None:
        """按 ws 实例移除连接，返回被移除的 conn_id（如有）。

        ws_server.py `_cleanup_connection` 中调用，必须返回协程（用 await）。
        """
        target_id: str | None = None
        for cid, info in self._connections.items():
            if info.ws is ws:
                target_id = cid
                break
        if target_id is not None:
            self._connections.pop(target_id, None)
            logger.debug("Connection removed by ws: id=%s active=%d", target_id, len(self._connections))
        return target_id

    # ---------- 心跳 ----------

    async def start_heartbeat(self) -> None:
        """启动后台心跳任务。重复调用无副作用。"""
        if self._heartbeat_task is not None and not self._heartbeat_task.done():
            return
        self._stopping = False
        self._heartbeat_task = asyncio.create_task(
            self._heartbeat_loop(),
            name="conn_manager.heartbeat",
        )
        logger.info("Heartbeat started: interval=%.1fs idle_timeout=%.1fs", self.heartbeat_interval, self.idle_timeout)

    async def stop_heartbeat(self) -> None:
        """停止心跳任务，等待退出。"""
        self._stopping = True
        task = self._heartbeat_task
        if task is None:
            return
        if not task.done():
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass
        self._heartbeat_task = None
        logger.info("Heartbeat stopped")

    async def _heartbeat_loop(self) -> None:
        """周期向所有连接发 ping，并清理空闲连接。"""
        while not self._stopping:
            try:
                await asyncio.sleep(self.heartbeat_interval)
                if self._stopping:
                    break
                now = time.time()
                stale_ids: list[str] = []
                for cid, info in list(self._connections.items()):
                    # P2-10: 使用 last_pong（最后收到 pong 的时间）判断 stale，
                    # 而非 last_ping_at（最后发送 ping 的时间）。
                    # 此前即使用户端从未回复 pong，只要服务端在发 ping 就不会判定 stale，
                    # 违背心跳检测目的。
                    if info.last_pong > 0:
                        last_active = info.last_pong
                    else:
                        last_active = info.connected_at
                    if now - last_active > self.idle_timeout:
                        stale_ids.append(cid)
                        continue
                    # 2. 否则发 ping 保活
                    try:
                        await info.ws.ping()
                        info.last_ping_at = now
                    except Exception as exc:
                        logger.debug("Ping failed for %s: %s", cid, exc)
                        stale_ids.append(cid)

                # 关闭 stale 连接
                for cid in stale_ids:
                    info = self._connections.pop(cid, None)
                    if info is None:
                        continue
                    try:
                        if not info.ws.closed:
                            await info.ws.close(code=1011, message=b"idle timeout")
                    except Exception as exc:
                        logger.debug("Close stale %s failed: %s", cid, exc)
                if stale_ids:
                    logger.info("Heartbeat closed %d stale connections", len(stale_ids))
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Heartbeat loop error")

    # ---------- 优雅关闭 ----------

    async def close_all(
        self,
        code: int = 1001,
        message: bytes = b"",
        *,
        restart_heartbeat: bool = False,
    ) -> int:
        """通知所有客户端即将关闭，返回被通知数量。

        不立即移除连接（让 _wait_for_connections_drain 等待自然清理）。
        会先停止心跳避免与关闭过程竞争。

        restart_heartbeat（P3 修复，2026-08-31）：auth.rotate/revoke 的
        "踢掉全部旧连接但服务端继续存活"场景必须传 True——否则本方法停掉
        的心跳没有重启路径，此后死连接永不清理（逼近 max_connections 拒连）、
        无 ping 保活（NAT 假活）。进程优雅关停路径保持默认 False：
        关停后进程即退，无需也不应复活心跳。
        """
        await self.stop_heartbeat()
        notified = 0
        for cid, info in list(self._connections.items()):
            try:
                if not info.ws.closed:
                    await info.ws.close(code=code, message=message)
                    notified += 1
            except Exception as exc:
                logger.debug("close_all: failed to close %s: %s", cid, exc)
        logger.info("close_all sent close frame to %d connections", notified)
        if restart_heartbeat:
            await self.start_heartbeat()
        return notified

    # ---------- 状态 / 统计 ----------

    def get_stats(self) -> dict[str, Any]:
        return {
            "active": len(self._connections),
            "max": self.max_connections,
            "total": self._total_connections,
        }

    async def cleanup_idle(self) -> int:
        """同步清理（不发 ping，仅按 connected_at 判定）。供单元测试使用。"""
        now = time.time()
        idle_ids = [cid for cid, info in self._connections.items() if now - info.connected_at > self.idle_timeout]
        for cid in idle_ids:
            self._connections.pop(cid, None)
        return len(idle_ids)

    async def broadcast(self, message: str | bytes) -> int:
        """向所有连接广播消息。返回成功发送数量。"""
        sent = 0
        for cid, info in list(self._connections.items()):
            try:
                if info.ws.closed:
                    continue
                if isinstance(message, bytes):
                    await info.ws.send_bytes(message)
                else:
                    await info.ws.send_str(message)
                sent += 1
            except Exception as exc:
                logger.debug("broadcast: failed to send to %s: %s", cid, exc)
        return sent
