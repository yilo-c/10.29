"""EventBus → WebSocket 事件桥接。

订阅 core.events.event_bus 并将事件推送到所有已连接的 WebSocket 客户端。

P0-3：每客户端独立发送队列 ——
- 每个连接拥有独立 asyncio.Queue + 专属 sender 协程，慢客户端只拖自己
- 队列满时按优先级丢弃：审批/同步/通知等关键事件永不丢弃（挤出最旧条目），
  普通事件直接丢弃并计数
- broadcast 入队后限时等待排空（保持既有同步送达语义与超时上限）
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import threading
from collections import deque
from typing import Any

from aiohttp import web

from core.events import AppEvent, event_bus

logger = logging.getLogger("siyi.backend.event_bridge")

# B4 修复：无运行循环时缓存的事件上限，避免内存无限增长
_PENDING_EVENT_LIMIT = 200

# P1-11 收尾：单个 WebSocket send_str 超时上限（秒）
# 用于防止某个慢连接拉长 asyncio.gather 整体时长。
_DEFAULT_BROADCAST_PER_SOCKET_TIMEOUT = 2.0

# P0-3：每客户端发送队列长度（可通过 SIYI_WS_CLIENT_QUEUE 调整）
_DEFAULT_CLIENT_QUEUE_SIZE = 256

# P0-3：关键事件前缀 —— 队列满时永不丢弃（挤出最旧条目腾位）
# 覆盖：审批 / 沙箱决策 / 同步 / 通知 / 断点续跑提示 / cron 告警
# 复C4（2026-08-29）：追加 task.resumable（断点续跑提示）与 proactive.（主动引擎
# 事件，P2.3）；可用 SIYI_WS_CRITICAL_PREFIXES（逗号分隔）整体覆盖缺省表。
_DEFAULT_CRITICAL_EVENT_PREFIXES = (
    "approval.",
    "sandbox.",
    "sync.",
    "notification.",
    "chain.resumable",
    "cron.job_alert",
    "task.resumable",
    "proactive.",
)


def _resolve_broadcast_timeout() -> float:
    raw = os.environ.get("SIYI_WS_BROADCAST_TIMEOUT", "").strip()
    if not raw:
        return _DEFAULT_BROADCAST_PER_SOCKET_TIMEOUT
    try:
        value = float(raw)
        if value <= 0:
            return 0.0  # 0 = 禁用超时（保留旧行为）
        return value
    except ValueError:
        return _DEFAULT_BROADCAST_PER_SOCKET_TIMEOUT


def _resolve_client_queue_size() -> int:
    raw = os.environ.get("SIYI_WS_CLIENT_QUEUE", "").strip()
    if not raw:
        return _DEFAULT_CLIENT_QUEUE_SIZE
    try:
        value = int(raw)
        return value if value > 0 else _DEFAULT_CLIENT_QUEUE_SIZE
    except ValueError:
        return _DEFAULT_CLIENT_QUEUE_SIZE


def _resolve_critical_prefixes() -> tuple[str, ...]:
    """复C4（2026-08-29）：解析关键事件前缀表。

    未设置 SIYI_WS_CRITICAL_PREFIXES 时返回缺省表（8 前缀，向后兼容）；
    设置时按逗号分隔解析并整体覆盖缺省表；解析结果为空（全空白/逗号）
    时回退缺省表。每次调用实时读取环境变量，便于测试与运行期覆盖。
    """
    raw = os.environ.get("SIYI_WS_CRITICAL_PREFIXES", "").strip()
    if not raw:
        return _DEFAULT_CRITICAL_EVENT_PREFIXES
    prefixes = tuple(dict.fromkeys(p.strip() for p in raw.split(",") if p.strip()))
    return prefixes or _DEFAULT_CRITICAL_EVENT_PREFIXES


def _is_critical_event(event_type: str) -> bool:
    return event_type.startswith(_resolve_critical_prefixes())


class _BridgeStats:
    """复C2（2026-08-29）：EventBridge 聚合统计（线程安全）。

    - dropped_total: 队列满被丢弃的普通事件数
    - evicted_total: 为关键事件腾位而被挤出的最旧条目数
    - send_timeout_total: 发送超时被丢弃的事件数（此前该分支零计数）
    - by_type: 未送达事件按 event_type 计数（丢弃 + 挤出 + 超时三类丢失），
      恒等式 sum(by_type.values()) == dropped_total + evicted_total + send_timeout_total

    计数只发生在事件循环线程（broadcast / sender 协程，含 call_soon_threadsafe
    跨线程调度路径的最终执行点），但 stats() 可能被 system_health 采集线程调用，
    读写均经 threading.Lock 串行化。
    """

    __slots__ = ("_lock", "dropped_total", "evicted_total", "send_timeout_total", "by_type")

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.dropped_total = 0
        self.evicted_total = 0
        self.send_timeout_total = 0
        self.by_type: dict[str, int] = {}

    def _inc(self, counter: str, event_type: str) -> None:
        with self._lock:
            setattr(self, counter, getattr(self, counter) + 1)
            if event_type:
                self.by_type[event_type] = self.by_type.get(event_type, 0) + 1

    def inc_dropped(self, event_type: str = "") -> None:
        self._inc("dropped_total", event_type)

    def inc_evicted(self, event_type: str = "") -> None:
        self._inc("evicted_total", event_type)

    def inc_send_timeout(self, event_type: str = "") -> None:
        self._inc("send_timeout_total", event_type)

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "dropped_total": self.dropped_total,
                "evicted_total": self.evicted_total,
                "send_timeout_total": self.send_timeout_total,
                "by_type": dict(self.by_type),
            }


# 复C2：最近一次 start_bridging 的桥接实例。生产路径 bridge 不挂在 context 上
# （headless_main 局部变量），system_health events 收集器经 get_active_bridge()
# 读取统计；stop_bridging 时若仍登记为本实例则注销。
_active_bridge: "EventBridge | None" = None


def get_active_bridge() -> "EventBridge | None":
    """返回最近一次 start_bridging 的 EventBridge（无则 None）。"""
    return _active_bridge


class _ClientOutbox:
    """单个 WebSocket 客户端的独立发送队列 + 专属 sender 协程。"""

    __slots__ = ("ws", "queue", "task", "dropped", "busy", "_bridge")

    def __init__(
        self,
        ws: web.WebSocketResponse,
        bridge: "EventBridge",
        loop: asyncio.AbstractEventLoop,
    ) -> None:
        self.ws = ws
        self._bridge = bridge
        self.queue: asyncio.Queue[tuple[str, bool, str]] = asyncio.Queue(maxsize=_resolve_client_queue_size())
        self.dropped = 0
        # sender 正在发送中（已取出条目未完成）——排空判断须同时看队列与忙标志
        self.busy = False
        self.task = loop.create_task(self._sender())

    def enqueue(self, data: str, critical: bool, event_type: str = "") -> None:
        """入队；队列满时按优先级丢弃（复C2：所有丢失计入聚合统计）。"""
        try:
            self.queue.put_nowait((data, critical, event_type))
            return
        except asyncio.QueueFull:
            pass
        if critical:
            # 关键事件永不丢弃：挤出最旧条目腾位
            try:
                _evicted_data, _evicted_critical, evicted_type = self.queue.get_nowait()
                self._bridge._stats.inc_evicted(evicted_type)
                logger.warning(
                    "client queue full: evicting oldest %s event to make room for critical event",
                    "critical" if _evicted_critical else "normal",
                )
            except asyncio.QueueEmpty:
                pass
            try:
                self.queue.put_nowait((data, critical, event_type))
            except asyncio.QueueFull:
                # 挤位后仍满（理论边缘）：该关键事件真实丢失，如实计数
                self._bridge._stats.inc_dropped(event_type)
            return
        self.dropped += 1
        self._bridge._stats.inc_dropped(event_type)
        if self.dropped == 1 or self.dropped % 100 == 0:
            logger.warning(
                "client queue full: dropping normal event for slow client (total dropped=%d)",
                self.dropped,
            )

    async def _sender(self) -> None:
        """持续消费队列并发送；失败/超时/关闭时自行注销。"""
        timeout = _resolve_broadcast_timeout()
        try:
            while True:
                data, _critical, event_type = await self.queue.get()
                self.busy = True
                try:
                    if self.ws.closed:
                        break
                    if timeout > 0:
                        await asyncio.wait_for(self.ws.send_str(data), timeout=timeout)
                    else:
                        await self.ws.send_str(data)
                except asyncio.TimeoutError:
                    # B5（2026-08-26）：慢客户端仅丢弃「当前这一条」事件，不永久踢除。
                    # 旧实现 break → finally 触发 _detach 注销整个连接，一次发送超时
                    # 会让用户连接被静默断开。改为继续消费后续事件，连接保持存活。
                    # 复C2（2026-08-29）：发送超时丢弃补计数（此前该分支零计数）。
                    self._bridge._stats.inc_send_timeout(event_type)
                    logger.warning(
                        "send to slow client timed out (timeout=%.1fs); dropping this event, keeping client",
                        timeout,
                    )
                    continue
                except Exception as exc:  # noqa: BLE001
                    logger.debug("send to client failed: %s", exc)
                    break
                finally:
                    self.busy = False
        finally:
            self._bridge._detach(self.ws)

    def cancel(self) -> None:
        if not self.task.done():
            self.task.cancel()


class EventBridge:
    """将 EventBus 事件转发到 WebSocket 客户端。"""

    def __init__(self) -> None:
        self._sockets: set[web.WebSocketResponse] = set()
        # P0-3：每客户端独立发送队列（懒创建）
        self._outboxes: dict[web.WebSocketResponse, _ClientOutbox] = {}
        # B4 修复：无运行循环时缓存事件，待循环可用时 flush
        self._pending_events: deque[tuple[str, dict[str, Any] | None]] = deque(maxlen=_PENDING_EVENT_LIMIT)
        self._loop: asyncio.AbstractEventLoop | None = None
        # P2-9: 保存 create_task 的强引用，防止 task 被 GC 回收导致事件静默丢失
        self._pending_tasks: set[asyncio.Task] = set()
        # 复C2：聚合统计（线程安全快照经 stats() 透出）
        self._stats = _BridgeStats()

    def set_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        """设置事件循环引用，并 flush 缓存的事件。"""
        self._loop = loop
        if self._pending_events and loop.is_running():
            pending = list(self._pending_events)
            self._pending_events.clear()
            for event_type, payload in pending:
                self._spawn_task(loop, self._broadcast_safe(event_type, payload))

    def _spawn_task(self, loop: asyncio.AbstractEventLoop, coro: Any) -> None:
        """P2-9: 创建 task 并保存强引用，完成后自动移除。

        Python 文档要求调用方保存 create_task 返回的 Task 强引用，
        否则可能被 GC 回收导致 task 静默取消。
        """
        task = loop.create_task(coro)
        self._pending_tasks.add(task)
        task.add_done_callback(self._pending_tasks.discard)

    def add_socket(self, ws: web.WebSocketResponse) -> None:
        self._sockets.add(ws)
        # 有运行循环时立即创建 outbox；否则留到 broadcast 时懒创建
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        if loop is not None and loop.is_running():
            self._ensure_outbox(ws, loop)

    def _ensure_outbox(self, ws: web.WebSocketResponse, loop: asyncio.AbstractEventLoop) -> _ClientOutbox:
        outbox = self._outboxes.get(ws)
        if outbox is None or outbox.task.done():
            outbox = _ClientOutbox(ws, self, loop)
            self._outboxes[ws] = outbox
        return outbox

    def _detach(self, ws: web.WebSocketResponse) -> None:
        """sender 失败/关闭后注销客户端（幂等）。"""
        self._sockets.discard(ws)
        outbox = self._outboxes.pop(ws, None)
        if outbox is not None:
            outbox.cancel()

    def remove_socket(self, ws: web.WebSocketResponse) -> None:
        self._sockets.discard(ws)
        outbox = self._outboxes.pop(ws, None)
        if outbox is not None:
            outbox.cancel()

    def _on_app_event(self, event: AppEvent) -> None:
        """EventBus 回调：广播到所有 WebSocket。"""
        # B4 修复：优先用已记录的 loop，其次尝试获取运行中的 loop，
        # 都失败时缓存事件待后续 flush，避免事件丢失。
        loop = self._loop
        if loop is None or not loop.is_running():
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                loop = None

        if loop is not None and loop.is_running():
            # 如果有缓存的事件，先 flush
            if self._pending_events:
                pending = list(self._pending_events)
                self._pending_events.clear()
                for evt_type, payload in pending:
                    self._schedule_broadcast(loop, evt_type, payload)
            self._schedule_broadcast(loop, event.event_type, event.payload)
        else:
            # 无运行循环：缓存事件，待循环可用时 flush
            self._pending_events.append((event.event_type, event.payload))
            logger.debug(
                "no running loop, queued event %s (pending=%d)",
                event.event_type,
                len(self._pending_events),
            )

    def _schedule_broadcast(self, loop: asyncio.AbstractEventLoop, event_type: str, payload: dict[str, Any] | None) -> None:
        """在正确的线程调度一次广播协程。

        B5（2026-08-26）：EventBus.emit 可能由非事件循环线程触发
        （cron_service / 各 service 在工作线程内发布事件）。此时直接
        ``loop.create_task`` 不是线程安全的——必须经由
        ``loop.call_soon_threadsafe`` 在循环线程内创建 task，且 task 强引用
        的登记也在循环线程完成（_spawn_task 内部），避免跨线程 set 修改与
        "coroutine never awaited" 风险。
        """
        coro = self._broadcast_safe(event_type, payload)
        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        if running is loop:
            # 当前就在循环线程：直接创建（保持既有路径）
            self._spawn_task(loop, coro)
        else:
            # 跨线程：委派到循环线程内执行 _spawn_task，线程安全
            loop.call_soon_threadsafe(self._spawn_task, loop, coro)

    async def _broadcast_safe(self, event_type: str, payload: dict[str, Any] | None) -> None:
        """广播包装，确保异常不会丢失。"""
        try:
            await self.broadcast(event_type, payload)
        except Exception as exc:
            logger.error("Event broadcast failed for %s: %s", event_type, exc)

    async def broadcast(self, event_type: str, payload: dict[str, Any] | None = None) -> None:
        """广播事件到所有连接（P0-3：每客户端独立队列）。

        语义保持：入队后限时等待排空（上限为 SIYI_WS_BROADCAST_TIMEOUT），
        快客户端在返回前已完成送达；慢客户端只占用等待上限，不拖累其他
        客户端的送达，队列满时按优先级丢弃（关键事件永不丢弃）。
        发送失败/超时的客户端由 sender 协程自动剔除。
        """
        data = json.dumps(
            {"type": "event", "event_type": event_type, "payload": payload or {}},
            ensure_ascii=False,
            default=str,
        )
        critical = _is_critical_event(event_type)
        loop = asyncio.get_running_loop()

        # 快照防止迭代中并发修改；已关闭的直接淘汰
        for ws in list(self._sockets):
            if ws.closed:
                self._detach(ws)
                continue
            self._ensure_outbox(ws, loop).enqueue(data, critical, event_type)

        # 限时等待排空：快客户端立即满足，慢客户端只拖到上限。
        # 排空 = 队列为空 且 无在途发送（busy）；超时/失败的客户端由
        # sender 在排空等待期内完成剔除，保持旧版"broadcast 返回即已处置"语义。
        # timeout<=0（禁用超时）时无限期等待，等同旧版无 wait_for 行为。
        timeout = _resolve_broadcast_timeout()
        deadline = None if timeout <= 0 else loop.time() + timeout
        while deadline is None or loop.time() < deadline:
            pending = [ob for ob in list(self._outboxes.values()) if (not ob.queue.empty() or ob.busy) and not ob.task.done()]
            if not pending:
                break
            await asyncio.sleep(0.005)

    def stats(self) -> dict[str, Any]:
        """复C2：聚合统计快照（线程安全，可从任意线程调用）。

        返回 {dropped_total, evicted_total, send_timeout_total, by_type}。
        """
        return self._stats.snapshot()

    async def send_to(self, ws: web.WebSocketResponse, event_type: str, payload: dict[str, Any] | None = None) -> None:
        """发送事件到指定连接。"""
        data = json.dumps(
            {"type": "event", "event_type": event_type, "payload": payload or {}},
            ensure_ascii=False,
            default=str,
        )
        try:
            if not ws.closed:
                await ws.send_str(data)
        except Exception:
            self._detach(ws)

    def start_bridging(self) -> None:
        """订阅 EventBus 全局事件。"""
        global _active_bridge
        event_bus.on("*", self._on_app_event)
        _active_bridge = self
        logger.info("event bridge started: forwarding EventBus events to WebSocket clients")

    async def close_all(self, code: int = 1001, message: bytes = b"Server shutting down") -> None:
        """关闭所有 WebSocket 连接（服务器停用时调用）。"""
        for ws in list(self._sockets):
            try:
                if not ws.closed:
                    await ws.close(code=code, message=message)
            except Exception as exc:
                # P2 修复：WebSocket 关闭失败无日志，死连接可能残留
                logger.debug("event_bridge failed to close socket: %s", exc, exc_info=True)
        for outbox in list(self._outboxes.values()):
            outbox.cancel()
        self._outboxes.clear()
        self._sockets.clear()

    def stop_bridging(self) -> None:
        global _active_bridge
        event_bus.off("*", self._on_app_event)
        if _active_bridge is self:
            _active_bridge = None
        logger.info("event bridge stopped")
