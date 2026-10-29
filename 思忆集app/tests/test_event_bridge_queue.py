# -*- coding: utf-8 -*-
"""P0-3：EventBridge 每客户端独立发送队列测试。

验收标准：
- 一个客户端不消费消息时，其他客户端送达不受影响
- 队列满时普通事件被丢弃，关键事件（审批/同步/通知等）永不丢弃
"""

from __future__ import annotations

import asyncio
import json
import sys
import time
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

import pytest

from backend.event_bridge import EventBridge, _is_critical_event


class FastSocket:
    def __init__(self) -> None:
        self.closed = False
        self.sent: list[str] = []

    async def send_str(self, data: str) -> None:
        self.sent.append(data)


class HungSocket:
    """第一条消息后永远挂起的客户端（模拟不消费/卡死）。"""

    def __init__(self) -> None:
        self.closed = False
        self.sent: list[str] = []

    async def send_str(self, data: str) -> None:
        self.sent.append(data)
        await asyncio.sleep(30.0)


@pytest.fixture(autouse=True)
def _short_timeout(monkeypatch):
    monkeypatch.setenv("SIYI_WS_BROADCAST_TIMEOUT", "0.15")


async def _drain(n: int = 6) -> None:
    for _ in range(n):
        await asyncio.sleep(0.01)


async def test_slow_client_does_not_delay_fast_client_delivery(monkeypatch):
    monkeypatch.setenv("SIYI_WS_CLIENT_QUEUE", "64")
    bridge = EventBridge()
    fast = FastSocket()
    hung = HungSocket()
    bridge.add_socket(fast)
    bridge.add_socket(hung)

    start = time.monotonic()
    await bridge.broadcast("proactive.suggestion", {"k": "v"})
    elapsed = time.monotonic() - start

    # 快客户端立即收到；broadcast 不被挂起客户端拖到远超单发超时
    assert len(fast.sent) == 1
    assert json.loads(fast.sent[0])["event_type"] == "proactive.suggestion"
    assert elapsed < 1.0, f"broadcast blocked by hung client: {elapsed:.2f}s"
    await _drain()


async def test_queue_full_drops_normal_events(monkeypatch):
    monkeypatch.setenv("SIYI_WS_CLIENT_QUEUE", "3")
    bridge = EventBridge()
    ws = FastSocket()
    bridge.add_socket(ws)
    outbox = bridge._outboxes[ws]
    # 停掉 sender 保持队列稳定，确定性验证入队丢弃逻辑
    outbox.cancel()
    await asyncio.sleep(0.02)

    for i in range(6):
        outbox.enqueue(json.dumps({"i": i}), critical=False)

    assert outbox.dropped == 3, "队列满时后到的普通事件应被丢弃"
    assert outbox.queue.qsize() == 3


async def test_critical_events_never_dropped(monkeypatch):
    monkeypatch.setenv("SIYI_WS_CLIENT_QUEUE", "3")
    bridge = EventBridge()
    ws = FastSocket()
    bridge.add_socket(ws)
    outbox = bridge._outboxes[ws]
    outbox.cancel()
    await asyncio.sleep(0.02)

    # 先用普通事件塞满队列
    for i in range(6):
        outbox.enqueue(json.dumps({"i": i}), critical=False)
    dropped_before = outbox.dropped

    # 关键事件入队：永不丢弃（挤出最旧条目腾位）
    outbox.enqueue(json.dumps({"event_type": "sync.conflict"}), critical=True)
    outbox.enqueue(json.dumps({"event_type": "notification.delivered"}), critical=True)

    queued = [json.loads(d).get("event_type") for d, _c, _t in list(outbox.queue._queue)]
    assert "sync.conflict" in queued
    assert "notification.delivered" in queued
    assert outbox.queue.qsize() == 3
    # 关键事件未被计入丢弃
    assert outbox.dropped == dropped_before


def test_critical_event_prefixes():
    assert _is_critical_event("sync.completed")
    assert _is_critical_event("sync.conflict")
    assert _is_critical_event("notification.delivered")
    assert _is_critical_event("sandbox.timeout")
    assert _is_critical_event("approval.created")
    assert _is_critical_event("cron.job_alert")
    assert _is_critical_event("chain.resumable")
    assert not _is_critical_event("chat.chunk")
    assert not _is_critical_event("exec.step")
    # 复C4（2026-08-29）：task.resumable / proactive. 前缀已加入关键白名单
    assert _is_critical_event("proactive.suggestion")
    assert _is_critical_event("task.resumable")


async def test_remove_socket_cancels_sender(monkeypatch):
    monkeypatch.setenv("SIYI_WS_CLIENT_QUEUE", "16")
    bridge = EventBridge()
    fast = FastSocket()
    bridge.add_socket(fast)
    await bridge.broadcast("a.b", {})
    await _drain()
    assert len(fast.sent) == 1

    outbox = bridge._outboxes[fast]
    bridge.remove_socket(fast)
    await _drain()
    assert outbox.task.done()
    assert fast not in bridge._sockets
