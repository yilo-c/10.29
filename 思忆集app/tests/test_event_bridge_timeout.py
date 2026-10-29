"""P1-11 收尾：EventBridge 每连接超时保护。

验证：
1. 默认超时 2.0s
2. SIYI_WS_BROADCAST_TIMEOUT 环境变量可覆盖
3. timeout=0 → 禁用，旧行为
4. 一个慢客户端不会拖累其他快客户端
5. 超时的慢客户端仅丢当前事件、连接保持（B5 2026-08-26 语义；原 P1-11
   踢除断言未随 B5 同步更新，此处修正）
"""

from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

import pytest

from backend.event_bridge import (
    EventBridge,
    _DEFAULT_BROADCAST_PER_SOCKET_TIMEOUT,
    _resolve_broadcast_timeout,
)


class FastSocket:
    def __init__(self) -> None:
        self.closed = False
        self.sent: list[str] = []

    async def send_str(self, data: str) -> None:
        self.sent.append(data)


class SlowSocket:
    """模拟一个永远不返回的 send_str。"""

    def __init__(self) -> None:
        self.closed = False
        self.send_started = 0

    async def send_str(self, data: str) -> None:
        self.send_started += 1
        await asyncio.sleep(10.0)


# ── 1. 默认值 ──────────────────────────────────────────────────


def test_default_timeout_is_two_seconds(monkeypatch):
    monkeypatch.delenv("SIYI_WS_BROADCAST_TIMEOUT", raising=False)
    assert _resolve_broadcast_timeout() == _DEFAULT_BROADCAST_PER_SOCKET_TIMEOUT == 2.0


def test_env_override_sets_custom_timeout(monkeypatch):
    monkeypatch.setenv("SIYI_WS_BROADCAST_TIMEOUT", "0.5")
    assert _resolve_broadcast_timeout() == 0.5


def test_env_zero_disables_timeout(monkeypatch):
    monkeypatch.setenv("SIYI_WS_BROADCAST_TIMEOUT", "0")
    assert _resolve_broadcast_timeout() == 0.0


def test_env_invalid_falls_back_to_default(monkeypatch):
    monkeypatch.setenv("SIYI_WS_BROADCAST_TIMEOUT", "not-a-number")
    assert _resolve_broadcast_timeout() == _DEFAULT_BROADCAST_PER_SOCKET_TIMEOUT


# ── 2. 慢客户端被踢、快客户端立即完成 ──────────────────────


@pytest.mark.asyncio
async def test_slow_socket_is_dropped_without_blocking_others(monkeypatch):
    monkeypatch.setenv("SIYI_WS_BROADCAST_TIMEOUT", "0.1")
    bridge = EventBridge()
    fast = FastSocket()
    slow = SlowSocket()
    bridge._sockets.add(fast)  # type: ignore[arg-type]
    bridge._sockets.add(slow)  # type: ignore[arg-type]

    start = time.monotonic()
    await bridge.broadcast("hello", {"x": 1})
    elapsed = time.monotonic() - start

    # 应该在 ~0.1s 完成（远小于 SlowSocket 的 10s）
    assert elapsed < 1.0, f"broadcast took {elapsed:.2f}s, slow client blocked the broadcast"
    # 快客户端正常收到
    assert len(fast.sent) == 1
    assert '"event_type": "hello"' in fast.sent[0]
    # B5：超时仅丢弃当前事件，慢客户端保持连接（不再踢除整个连接）
    assert slow in bridge._sockets
    assert slow in bridge._outboxes
    # 快客户端仍在
    assert fast in bridge._sockets


@pytest.mark.asyncio
async def test_timeout_zero_keeps_old_behavior(monkeypatch):
    """timeout=0 → 不再加 wait_for；慢客户端会真的拖住，但本测试用一个会异常的
    fake socket 验证“没有走 wait_for 分支”不会引入新异常。"""
    monkeypatch.setenv("SIYI_WS_BROADCAST_TIMEOUT", "0")
    bridge = EventBridge()
    fast = FastSocket()
    bridge._sockets.add(fast)  # type: ignore[arg-type]
    await bridge.broadcast("hello", {})
    assert len(fast.sent) == 1
