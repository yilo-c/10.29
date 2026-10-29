"""Sync Transport — WebSocket-based transport layer for cross-device sync.

Provides the actual network transport that sync/channels.py channels use to
communicate with remote devices (phones, other desktops). Previously the
channels relied on externally-injected callables (get_envelopes_fn etc.)
without a concrete transport implementation; this module fills that gap.

Architecture:
    ┌─────────────────────────────────────────────────────────────┐
    │                   SyncWebSocketTransport                    │
    │  ┌──────────────┐  ┌──────────────┐  ┌──────────────────┐  │
    │  │  _sender     │  │  _receiver   │  │  _reconnect_loop │  │
    │  │ (outgoing)   │  │ (incoming)   │  │ (auto-reconnect) │  │
    │  └──────────────┘  └──────────────┘  └──────────────────┘  │
    │                                                             │
    │  Exposes:                                                   │
    │    - send_envelope(envelope) -> ack                         │
    │    - request_pull(device_id, cursor) -> SyncPullResponse    │
    │    - send_heartbeat(device_id) -> status                    │
    │    - on_envelope_received(callback)                         │
    │    - on_heartbeat_received(callback)                        │
    └─────────────────────────────────────────────────────────────┘

Usage:
    transport = SyncWebSocketTransport("ws://192.168.1.100:18765/sync")
    await transport.connect()
    response = await transport.request_pull("phone-001", cursor=42)
    await transport.send_envelope(envelope_dict)
    transport.on_envelope_received(my_callback)
    await transport.close()

The transport uses aiohttp.WebSocketClientProtocol internally and integrates
with the existing asyncio event loop in headless mode.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any, Awaitable, Callable

logger = logging.getLogger("siyi.sync.transport")


# ── Callback type aliases ────────────────────────────────────────────

EnvelopeCallback = Callable[[dict[str, Any]], None | Awaitable[None]]
HeartbeatCallback = Callable[[dict[str, Any]], None | Awaitable[None]]
AckCallback = Callable[[dict[str, Any]], None | Awaitable[None]]


class SyncTransportError(RuntimeError):
    """Base error for sync transport failures."""


class SyncTransportConnectionError(SyncTransportError):
    """Failed to connect or connection lost."""


class SyncTransportTimeoutError(SyncTransportError):
    """Request timed out waiting for response."""


class SyncWebSocketTransport:
    """WebSocket-based transport for cross-device sync.

    Implements the 4 channel operations (pull/relay/ack/heartbeat) over a
    single WebSocket connection using JSON-RPC-style request/response.

    Reconnection:
        - Auto-reconnect on connection drop with exponential backoff
        - Pending requests fail fast with SyncTransportConnectionError
        - Incoming envelopes buffered during reconnect window are lost
          (callers should re-pull after reconnect)
    """

    def __init__(
        self,
        url: str,
        *,
        token: str | None = None,
        request_timeout: float = 30.0,
        reconnect_interval: float = 5.0,
        max_reconnect_interval: float = 60.0,
    ) -> None:
        self._url = url
        self._token = token
        self._request_timeout = request_timeout
        self._reconnect_interval = reconnect_interval
        self._max_reconnect_interval = max_reconnect_interval

        self._ws: Any = None  # aiohttp.ClientWebSocketResponse
        self._session: Any = None  # aiohttp.ClientSession
        self._connected = False
        self._request_id = 0
        self._pending: dict[int, asyncio.Future[dict[str, Any]]] = {}

        # Callbacks for incoming events
        self._envelope_callbacks: list[EnvelopeCallback] = []
        self._heartbeat_callbacks: list[HeartbeatCallback] = []
        self._ack_callbacks: list[AckCallback] = []

        # Background tasks
        self._receiver_task: asyncio.Task[None] | None = None
        self._reconnect_task: asyncio.Task[None] | None = None
        self._should_run = False

    # ── Connection management ──────────────────────────────────────

    async def connect(self) -> None:
        """Establish WebSocket connection. Raises on failure."""
        try:
            import aiohttp
        except ImportError as exc:
            raise SyncTransportError(
                "aiohttp is required for SyncWebSocketTransport"
            ) from exc

        headers = {}
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"

        self._session = aiohttp.ClientSession(headers=headers)
        try:
            self._ws = await self._session.ws_connect(self._url)
            self._connected = True
            self._should_run = True
            self._receiver_task = asyncio.create_task(self._receiver_loop())
            logger.info("Sync transport connected to %s", self._url)
        except Exception as exc:
            await self._session.close()
            raise SyncTransportConnectionError(
                f"Failed to connect to {self._url}: {exc}"
            ) from exc

    async def close(self) -> None:
        """Close the transport and clean up resources."""
        self._should_run = False
        self._connected = False

        if self._receiver_task:
            self._receiver_task.cancel()
            try:
                await self._receiver_task
            except asyncio.CancelledError:
                pass
            self._receiver_task = None

        if self._ws:
            await self._ws.close()
            self._ws = None

        if self._session:
            await self._session.close()
            self._session = None

        # Fail all pending requests
        for fut in self._pending.values():
            if not fut.done():
                fut.set_exception(
                    SyncTransportConnectionError("transport closed")
                )
        self._pending.clear()

        logger.info("Sync transport closed")

    @property
    def is_connected(self) -> bool:
        return self._connected

    # ── Channel operations ─────────────────────────────────────────

    async def request_pull(
        self,
        device_id: str,
        since_cursor: int = 0,
        resource_type: str | None = None,
        limit: int = 100,
    ) -> dict[str, Any]:
        """SyncPull channel: request incremental envelopes from peer.

        Returns the SyncPullResponse as a dict (envelopes, latest_cursor,
        has_more, conflicts).
        """
        params = {
            "device_id": device_id,
            "since_cursor": since_cursor,
            "resource_type": resource_type,
            "limit": limit,
        }
        return await self._request("sync.pull", params)

    async def send_envelope(self, envelope: dict[str, Any]) -> dict[str, Any]:
        """QuickRelay channel: send an envelope to the peer device.

        Returns the ack response from the peer.
        """
        return await self._request("sync.relay", {"envelope": envelope})

    async def send_ack(self, ack: dict[str, Any]) -> dict[str, Any]:
        """AckWrite channel: send a write confirmation/read receipt."""
        return await self._request("sync.ack", {"ack": ack})

    async def send_heartbeat(self, device_id: str, status: dict[str, Any] | None = None) -> dict[str, Any]:
        """DeviceHeartbeat channel: send device status to peer."""
        payload = {
            "device_id": device_id,
            "timestamp": time.time(),
            "status": status or {},
        }
        return await self._request("sync.heartbeat", payload)

    # ── Event subscriptions ────────────────────────────────────────

    def on_envelope_received(self, callback: EnvelopeCallback) -> None:
        """Register a callback for incoming envelopes (push from peer)."""
        self._envelope_callbacks.append(callback)

    def on_heartbeat_received(self, callback: HeartbeatCallback) -> None:
        """Register a callback for incoming heartbeats."""
        self._heartbeat_callbacks.append(callback)

    def on_ack_received(self, callback: AckCallback) -> None:
        """Register a callback for incoming acks."""
        self._ack_callbacks.append(callback)

    # ── Internal: request/response ─────────────────────────────────

    async def _request(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        """Send a JSON-RPC request and await the response."""
        if not self._connected or self._ws is None:
            raise SyncTransportConnectionError("not connected")

        self._request_id += 1
        req_id = self._request_id
        message = {
            "jsonrpc": "2.0",
            "id": req_id,
            "method": method,
            "params": params,
        }

        fut: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
        self._pending[req_id] = fut

        try:
            await self._ws.send_str(json.dumps(message, ensure_ascii=False))
        except Exception as exc:
            self._pending.pop(req_id, None)
            raise SyncTransportConnectionError(
                f"failed to send {method}: {exc}"
            ) from exc

        try:
            response = await asyncio.wait_for(fut, timeout=self._request_timeout)
        except asyncio.TimeoutError:
            self._pending.pop(req_id, None)
            raise SyncTransportTimeoutError(
                f"{method} timed out after {self._request_timeout}s"
            ) from None

        if "error" in response:
            err = response["error"]
            raise SyncTransportError(
                f"{method} failed: {err.get('message', 'unknown error')}"
            )

        return response.get("result", {})

    async def _receiver_loop(self) -> None:
        """Background task: receive and dispatch incoming WebSocket messages."""
        try:
            while self._should_run and self._ws is not None:
                msg = await self._ws.receive()
                if msg.type == 0x1:  # aiohttp.WSMsgType.TEXT
                    await self._handle_message(msg.data)
                elif msg.type in (0x8, 0x101):  # CLOSE, GOING_AWAY
                    logger.warning("WebSocket closed by peer: %s", msg.type)
                    break
                elif msg.type == 0x4:  # ERROR
                    logger.error("WebSocket error: %s", msg.data)
                    break
        except asyncio.CancelledError:
            pass
        except Exception:
            logger.exception("receiver loop crashed")
        finally:
            self._connected = False
            if self._should_run:
                # Trigger reconnect
                self._reconnect_task = asyncio.create_task(self._reconnect_loop())

    async def _handle_message(self, raw: str) -> None:
        """Parse and dispatch an incoming JSON message."""
        try:
            msg = json.loads(raw)
        except json.JSONDecodeError:
            logger.warning("invalid JSON received: %s", raw[:200])
            return

        # JSON-RPC response (has 'id' matching a pending request)
        msg_id = msg.get("id")
        if msg_id is not None and msg_id in self._pending:
            fut = self._pending.pop(msg_id)
            if not fut.done():
                fut.set_result(msg)
            return

        # Server push event (has 'method' or 'event_type')
        method = msg.get("method") or msg.get("event_type")
        if method == "sync.push_envelope":
            await self._dispatch_callbacks(self._envelope_callbacks, msg.get("params", {}))
        elif method == "sync.heartbeat":
            await self._dispatch_callbacks(self._heartbeat_callbacks, msg.get("params", {}))
        elif method == "sync.ack":
            await self._dispatch_callbacks(self._ack_callbacks, msg.get("params", {}))
        else:
            logger.debug("unhandled sync message: %s", method)

    async def _dispatch_callbacks(
        self,
        callbacks: list[EnvelopeCallback | HeartbeatCallback | AckCallback],
        payload: dict[str, Any],
    ) -> None:
        """Invoke all registered callbacks for an event type."""
        for cb in callbacks:
            try:
                result = cb(payload)
                if asyncio.iscoroutine(result):
                    await result
            except Exception:
                logger.exception("sync callback failed")

    async def _reconnect_loop(self) -> None:
        """Auto-reconnect with exponential backoff."""
        interval = self._reconnect_interval
        while self._should_run and not self._connected:
            logger.info("attempting reconnect in %.1fs", interval)
            await asyncio.sleep(interval)
            if not self._should_run:
                break
            try:
                await self._do_reconnect()
                interval = self._reconnect_interval  # reset on success
            except Exception:
                interval = min(interval * 2, self._max_reconnect_interval)

    async def _do_reconnect(self) -> None:
        """Perform a single reconnection attempt."""
        # Clean up old state
        if self._ws:
            try:
                await self._ws.close()
            except Exception:
                pass
            self._ws = None
        if self._session and not self._session.closed:
            await self._session.close()

        # Reconnect
        import aiohttp
        headers = {}
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        self._session = aiohttp.ClientSession(headers=headers)
        self._ws = await self._session.ws_connect(self._url)
        self._connected = True
        self._receiver_task = asyncio.create_task(self._receiver_loop())
        logger.info("Sync transport reconnected to %s", self._url)
