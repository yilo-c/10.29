"""EventBridge 单元测试 —— 覆盖 EventBus → WebSocket 事件桥接的核心行为。"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

import pytest

from backend.event_bridge import EventBridge
from core.events import AppEvent, event_bus
from sync.cloud_sync_client import CloudSyncAuthError, CloudSyncClient


class FakeWebSocketResponse:
    """模拟 aiohttp.web.WebSocketResponse 的最小行为。"""

    def __init__(self, closed: bool = False) -> None:
        self.closed = closed
        self.sent: list[str] = []
        self.close_calls: list[tuple[int, bytes]] = []

    async def send_str(self, data: str) -> None:
        self.sent.append(data)

    async def close(self, *, code: int = 1000, message: bytes = b"") -> None:
        self.close_calls.append((code, message))


@pytest.fixture(autouse=True)
def _clean_event_bus():
    """全局 event_bus 是单例，每个测试前后清空订阅，避免互相干扰。"""
    event_bus.clear()
    yield
    event_bus.clear()


def test_add_and_remove_socket():
    bridge = EventBridge()
    ws = FakeWebSocketResponse()

    bridge.add_socket(ws)
    assert ws in bridge._sockets

    bridge.remove_socket(ws)
    assert ws not in bridge._sockets


async def test_broadcast_sends_json_event_to_all_connected_sockets():
    bridge = EventBridge()
    ws1 = FakeWebSocketResponse()
    ws2 = FakeWebSocketResponse()
    bridge.add_socket(ws1)
    bridge.add_socket(ws2)

    await bridge.broadcast("event-bridge.broadcast", {"msg": "hello"})

    expected = {
        "type": "event",
        "event_type": "event-bridge.broadcast",
        "payload": {"msg": "hello"},
    }
    assert len(ws1.sent) == 1
    assert len(ws2.sent) == 1
    assert json.loads(ws1.sent[0]) == expected
    assert json.loads(ws2.sent[0]) == expected


async def test_broadcast_skips_closed_sockets():
    bridge = EventBridge()
    open_ws = FakeWebSocketResponse(closed=False)
    closed_ws = FakeWebSocketResponse(closed=True)
    bridge.add_socket(open_ws)
    bridge.add_socket(closed_ws)

    await bridge.broadcast("event-bridge.skip-closed", {"value": 42})

    assert len(open_ws.sent) == 1
    assert json.loads(open_ws.sent[0])["event_type"] == "event-bridge.skip-closed"
    assert closed_ws.sent == []


async def test_send_to_sends_to_specific_socket_only():
    bridge = EventBridge()
    ws1 = FakeWebSocketResponse()
    ws2 = FakeWebSocketResponse()
    bridge.add_socket(ws1)
    bridge.add_socket(ws2)

    await bridge.send_to(ws1, "event-bridge.send-to", {"target": 1})

    assert len(ws1.sent) == 1
    assert json.loads(ws1.sent[0])["event_type"] == "event-bridge.send-to"
    assert ws2.sent == []


async def test_send_to_skips_closed_target():
    bridge = EventBridge()
    closed_ws = FakeWebSocketResponse(closed=True)
    bridge.add_socket(closed_ws)

    await bridge.send_to(closed_ws, "event-bridge.send-to-closed", {"x": 1})

    assert closed_ws.sent == []


async def test_start_bridging_subscribes_to_event_bus_and_forwards_app_event():
    bridge = EventBridge()
    ws1 = FakeWebSocketResponse()
    ws2 = FakeWebSocketResponse()
    bridge.add_socket(ws1)
    bridge.add_socket(ws2)

    bridge.start_bridging()
    event_bus.emit(AppEvent("event-bridge.forward", {"data": "payload"}))
    # 等待 _on_app_event 中 create_task 调度的广播任务执行完毕
    # P1-11 收尾：broadcast 现在内部使用 asyncio.wait_for，多加一次 yield 以稳健排空
    for _ in range(4):
        await asyncio.sleep(0)

    assert len(ws1.sent) == 1
    assert len(ws2.sent) == 1
    payload = json.loads(ws1.sent[0])
    assert payload["type"] == "event"
    assert payload["event_type"] == "event-bridge.forward"
    assert payload["payload"] == {"data": "payload"}


async def test_stop_bridging_unsubscribes_from_event_bus():
    bridge = EventBridge()
    ws = FakeWebSocketResponse()
    bridge.add_socket(ws)

    bridge.start_bridging()
    bridge.stop_bridging()

    event_bus.emit(AppEvent("event-bridge.no-forward", {"data": "lost"}))
    await asyncio.sleep(0)
    await asyncio.sleep(0)

    assert ws.sent == []


async def test_cloud_sync_success_event_is_forwarded_to_websocket():
    bridge = EventBridge()
    ws = FakeWebSocketResponse()
    bridge.add_socket(ws)
    bridge.start_bridging()

    client = CloudSyncClient(
        worker_url="https://sync.example.workers.dev",
        sync_token="tok",
        device_id="desktop-test",
        register_secret="reg-secret",
    )
    client.push_local_data = AsyncMock(return_value=True)
    client.pull_remote_data = AsyncMock(return_value=True)
    client._push_count = 2
    client._pull_count = 5
    client._cursor = 321

    ok = await client.sync_once()
    assert ok is True

    for _ in range(4):
        await asyncio.sleep(0)

    assert len(ws.sent) == 1
    payload = json.loads(ws.sent[0])
    assert payload["type"] == "event"
    assert payload["event_type"] == "sync.completed"
    assert payload["payload"] == {
        "device_id": "desktop-test",
        "push_ok": True,
        "pull_ok": True,
        "push_count": 2,
        "pull_count": 5,
        "cursor": 321,
    }


async def test_cloud_sync_auth_failure_event_is_forwarded_to_websocket():
    bridge = EventBridge()
    ws = FakeWebSocketResponse()
    bridge.add_socket(ws)
    bridge.start_bridging()

    client = CloudSyncClient(
        worker_url="https://sync.example.workers.dev",
        sync_token="tok",
        device_id="desktop-test",
        register_secret="reg-secret",
    )
    client.push_local_data = AsyncMock(side_effect=CloudSyncAuthError("bad token"))

    ok = await client.sync_once()
    assert ok is False

    for _ in range(4):
        await asyncio.sleep(0)

    assert len(ws.sent) == 1
    payload = json.loads(ws.sent[0])
    assert payload["type"] == "event"
    assert payload["event_type"] == "sync.failed"
    assert payload["payload"]["device_id"] == "desktop-test"
    assert payload["payload"]["error"] == "bad token"
    assert payload["payload"]["fatal"] is True


async def test_broadcast_runs_sends_concurrently_so_slow_client_does_not_block():
    """P1-11 回归：广播应并发发送，慢客户端不能拖慢其他客户端。"""

    class SlowWebSocket(FakeWebSocketResponse):
        def __init__(self, delay: float) -> None:
            super().__init__()
            self._delay = delay
            self.finished_at: float | None = None

        async def send_str(self, data: str) -> None:
            await asyncio.sleep(self._delay)
            self.finished_at = asyncio.get_running_loop().time()
            self.sent.append(data)

    bridge = EventBridge()
    s1 = SlowWebSocket(delay=0.04)
    s2 = SlowWebSocket(delay=0.04)
    s3 = SlowWebSocket(delay=0.04)
    bridge.add_socket(s1)
    bridge.add_socket(s2)
    bridge.add_socket(s3)

    t0 = asyncio.get_running_loop().time()
    await bridge.broadcast("event-bridge.parallel", {"k": "v"})
    elapsed = asyncio.get_running_loop().time() - t0

    # 三个 40ms 客户端：并发 ≈ 40ms，串行 ≈ 120ms；
    # 给出充足上限 <100ms 即足以证明 broadcast 走的是 gather 并发路径。
    assert elapsed < 0.10, f"broadcast should be concurrent, got {elapsed:.3f}s"
    assert all(ws.sent for ws in (s1, s2, s3))


async def test_broadcast_failing_socket_does_not_break_others():
    """P1-11 回归：单个客户端异常应被吞掉，其他客户端仍能收到。"""

    class BadWebSocket(FakeWebSocketResponse):
        async def send_str(self, data: str) -> None:
            raise RuntimeError("network error")

    bridge = EventBridge()
    bad = BadWebSocket()
    good = FakeWebSocketResponse()
    bridge.add_socket(bad)
    bridge.add_socket(good)

    await bridge.broadcast("event-bridge.partial-fail", {"x": 1})

    assert len(good.sent) == 1
    assert json.loads(good.sent[0])["event_type"] == "event-bridge.partial-fail"
    # 失败的连接应被自动剔除，保留正常客户端
    assert bad not in bridge._sockets
    assert good in bridge._sockets


async def test_close_all_closes_all_sockets():
    bridge = EventBridge()
    ws1 = FakeWebSocketResponse()
    ws2 = FakeWebSocketResponse()
    bridge.add_socket(ws1)
    bridge.add_socket(ws2)

    await bridge.close_all(code=1001, message=b"Server shutting down")

    assert ws1.close_calls == [(1001, b"Server shutting down")]
    assert ws2.close_calls == [(1001, b"Server shutting down")]
    assert bridge._sockets == set()
