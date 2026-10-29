"""ConnectionManager 单元测试。

防止未来再次出现 "minimal replacement for corrupted original" 那种空壳实现。
覆盖 ws_server.py 实际调用的 6 个方法：
  add_connection / remove_by_ws / find_by_ws / start_heartbeat / stop_heartbeat / close_all
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from backend.connection_manager import ConnectionInfo, ConnectionManager  # noqa: E402,F401


class _FakeWS:
    """最小 aiohttp WebSocketResponse 替身。"""

    def __init__(self) -> None:
        self.closed = False
        self.close_calls: list[tuple[int, bytes]] = []
        self.ping_count = 0
        self.sent: list[str | bytes] = []
        self.fail_ping = False

    async def close(self, *, code: int = 1000, message: bytes = b"") -> None:
        self.closed = True
        self.close_calls.append((code, message))

    async def ping(self) -> None:
        if self.fail_ping:
            raise ConnectionResetError("simulated network error")
        self.ping_count += 1

    async def send_str(self, data: str) -> None:
        self.sent.append(data)

    async def send_bytes(self, data: bytes) -> None:
        self.sent.append(data)


@pytest.mark.asyncio
async def test_add_and_find_by_ws():
    cm = ConnectionManager(max_connections=10)
    ws = _FakeWS()
    cid = await cm.add_connection(ws, peer="1.2.3.4")
    assert cid == "c1"
    info = cm.find_by_ws(ws)
    assert info is not None and info.id == "c1" and info.peer == "1.2.3.4"
    # 写入 last_pong 必须可用（ws_server.py:379 会做 attribute set）
    info.last_pong = "abc"
    assert cm.get_connection(cid).last_pong == "abc"


@pytest.mark.asyncio
async def test_remove_by_ws_returns_id_and_pops():
    cm = ConnectionManager()
    ws = _FakeWS()
    cid = await cm.add_connection(ws, peer="p")
    removed = await cm.remove_by_ws(ws)
    assert removed == cid
    assert cm.get_connection(cid) is None
    # 重复移除返回 None 而不是抛错
    assert await cm.remove_by_ws(ws) is None


@pytest.mark.asyncio
async def test_find_by_ws_unknown_returns_none():
    cm = ConnectionManager()
    assert cm.find_by_ws(_FakeWS()) is None


@pytest.mark.asyncio
async def test_max_connections_enforced():
    cm = ConnectionManager(max_connections=2)
    await cm.add_connection(_FakeWS(), "a")
    await cm.add_connection(_FakeWS(), "b")
    # P0-1 修复：连接超限时抛出 ConnectionLimitExceededError（而非 RuntimeError），
    # 让 ws_server 能正确捕获并返回 403 而非 500
    from core.errors import ConnectionLimitExceededError

    with pytest.raises(ConnectionLimitExceededError, match="max_connections"):
        await cm.add_connection(_FakeWS(), "c")


@pytest.mark.asyncio
async def test_close_all_notifies_and_stops_heartbeat():
    cm = ConnectionManager(heartbeat_interval=10.0)
    ws1, ws2 = _FakeWS(), _FakeWS()
    await cm.add_connection(ws1, "a")
    await cm.add_connection(ws2, "b")
    await cm.start_heartbeat()
    notified = await cm.close_all(code=1001, message=b"bye")
    assert notified == 2
    assert ws1.close_calls == [(1001, b"bye")]
    assert ws2.close_calls == [(1001, b"bye")]
    # 心跳已停止
    assert cm._heartbeat_task is None


@pytest.mark.asyncio
async def test_close_all_restart_heartbeat_keeps_ping_alive():
    """P3（2026-08-31）：restart_heartbeat=True（auth 踢人场景）——
    close_all 停心跳后立即重启，_heartbeat_task 恢复存活。"""
    cm = ConnectionManager(heartbeat_interval=10.0)
    ws1 = _FakeWS()
    await cm.add_connection(ws1, "a")
    await cm.start_heartbeat()
    notified = await cm.close_all(code=1008, message=b"token rotated", restart_heartbeat=True)
    assert notified == 1
    assert ws1.close_calls == [(1008, b"token rotated")]
    # 心跳已复活
    assert cm._heartbeat_task is not None
    assert not cm._heartbeat_task.done()
    await cm.stop_heartbeat()


@pytest.mark.asyncio
async def test_start_heartbeat_idempotent():
    cm = ConnectionManager()
    await cm.start_heartbeat()
    first = cm._heartbeat_task
    await cm.start_heartbeat()
    assert cm._heartbeat_task is first  # 不会重新创建
    await cm.stop_heartbeat()


@pytest.mark.asyncio
async def test_heartbeat_pings_active_and_evicts_failed():
    """心跳实际行为：发 ping 成功 → 保留；ping 抛错 → 关闭并移除。"""
    cm = ConnectionManager(heartbeat_interval=0.05, idle_timeout=10.0)
    good = _FakeWS()
    bad = _FakeWS()
    bad.fail_ping = True
    await cm.add_connection(good, "g")
    await cm.add_connection(bad, "b")
    await cm.start_heartbeat()
    # 等待至少一轮心跳
    await asyncio.sleep(0.18)
    await cm.stop_heartbeat()
    assert good.ping_count >= 1
    # bad 应已被关闭并移除
    assert cm.find_by_ws(bad) is None
    assert bad.closed is True


@pytest.mark.asyncio
async def test_broadcast_sends_to_open_connections():
    cm = ConnectionManager()
    ws_open = _FakeWS()
    ws_closed = _FakeWS()
    ws_closed.closed = True
    await cm.add_connection(ws_open, "o")
    await cm.add_connection(ws_closed, "c")
    sent = await cm.broadcast("hello")
    assert sent == 1
    assert ws_open.sent == ["hello"]
    assert ws_closed.sent == []


@pytest.mark.asyncio
async def test_get_stats():
    cm = ConnectionManager(max_connections=5)
    await cm.add_connection(_FakeWS(), "a")
    await cm.add_connection(_FakeWS(), "b")
    stats = cm.get_stats()
    assert stats == {"active": 2, "max": 5, "total": 2}
    await cm.remove_by_ws(list(cm._connections.values())[0].ws)
    stats2 = cm.get_stats()
    # remove 不减 total（total 是累计计数）
    assert stats2["active"] == 1 and stats2["total"] == 2
