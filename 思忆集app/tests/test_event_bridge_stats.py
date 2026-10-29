# -*- coding: utf-8 -*-
"""七维-复C2/C3/C4：EventBridge 聚合统计 + 订阅异常计数 + 关键白名单配置化。

验收（execution-plan-2026-08-29-p0-wave2 W4 / 七维报告 §3.3 C2-C4）：
- stats() 聚合 {dropped_total, evicted_total, send_timeout_total, by_type}（线程安全）
- TimeoutError 丢弃分支补计数（此前连计数都不加）
- 白名单追加 task.resumable / proactive. 前缀；SIYI_WS_CRITICAL_PREFIXES 可覆盖
- EventBus 订阅方异常计数并入可读接口
- system_health events 收集器透出 dropped_total 等字段

口径：by_type = 未送达事件按 event_type 计数（队列满丢弃 + 关键事件挤出最旧
+ 发送超时丢弃），恒等式 sum(by_type.values()) == dropped_total + evicted_total
+ send_timeout_total。
"""

from __future__ import annotations

import asyncio
import json
import sys
import threading
from pathlib import Path
from types import SimpleNamespace

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

import pytest

import backend.event_bridge as eb_module
from backend.event_bridge import EventBridge, _is_critical_event
from core.events import AppEvent, EventBus, event_bus


class FastSocket:
    def __init__(self) -> None:
        self.closed = False
        self.sent: list[str] = []

    async def send_str(self, data: str) -> None:
        self.sent.append(data)


class HungSocket:
    """第一条消息后永远挂起的客户端（sender 占住 1 条在途，队列很快灌满）。"""

    def __init__(self) -> None:
        self.closed = False
        self.sent: list[str] = []

    async def send_str(self, data: str) -> None:
        self.sent.append(data)
        await asyncio.sleep(30.0)


class SlowSocket:
    """send_str 永不返回（触发发送超时分支）。"""

    def __init__(self) -> None:
        self.closed = False
        self.send_started = 0

    async def send_str(self, data: str) -> None:
        self.send_started += 1
        await asyncio.sleep(10.0)


@pytest.fixture(autouse=True)
def _clean_bus_and_active_bridge():
    """全局 event_bus 单例与 active-bridge 注册，前后清空避免跨测试污染。"""
    event_bus.clear()
    eb_module._active_bridge = None
    yield
    event_bus.clear()
    eb_module._active_bridge = None


# ── 复C2：stats() 聚合统计 ──────────────────────────────────────


def test_stats_zero_on_fresh_bridge():
    bridge = EventBridge()
    snap = bridge.stats()
    assert snap["dropped_total"] == 0
    assert snap["evicted_total"] == 0
    assert snap["send_timeout_total"] == 0
    assert snap["by_type"] == {}


async def test_queue_full_drops_counted_in_stats(monkeypatch):
    """灌满队列触发丢弃：dropped_total>0 且 by_type 按类型计数。

    手法同既有 queue 测试：停掉 sender 保持队列稳定后直接对 outbox 入队。
    不走 broadcast 制造队列满——per-send 超时与广播排空共用
    SIYI_WS_BROADCAST_TIMEOUT，timeout>0 时队列无法稳定累积（必然竞态）；
    broadcast→enqueue 的 event_type 接线由 test_send_timeout_counted 覆盖。
    """
    monkeypatch.setenv("SIYI_WS_CLIENT_QUEUE", "3")
    bridge = EventBridge()
    ws = FastSocket()
    bridge.add_socket(ws)
    outbox = bridge._outboxes[ws]
    outbox.cancel()  # 停 sender：确定性验证入队丢弃逻辑（不消费）
    await asyncio.sleep(0.02)
    for i in range(6):
        outbox.enqueue(json.dumps({"i": i}), critical=False, event_type="normal.fill")
    snap = bridge.stats()
    assert snap["dropped_total"] == 3
    assert snap["by_type"].get("normal.fill") == 3
    assert snap["evicted_total"] == 0
    assert snap["send_timeout_total"] == 0


async def test_critical_eviction_counted(monkeypatch):
    """关键事件挤出最旧普通事件：evicted_total 计数且按被挤出事件类型入 by_type。"""
    monkeypatch.setenv("SIYI_WS_CLIENT_QUEUE", "3")
    bridge = EventBridge()
    ws = FastSocket()
    bridge.add_socket(ws)
    outbox = bridge._outboxes[ws]
    outbox.cancel()
    await asyncio.sleep(0.02)
    for i in range(3):  # 恰好灌满队列，无丢弃
        outbox.enqueue(json.dumps({"i": i}), critical=False, event_type="normal.fill")
    assert bridge.stats()["dropped_total"] == 0

    outbox.enqueue(json.dumps({"event_type": "sync.conflict"}), critical=True, event_type="sync.conflict")
    outbox.enqueue(json.dumps({"event_type": "notification.delivered"}), critical=True, event_type="notification.delivered")
    snap = bridge.stats()
    assert snap["evicted_total"] == 2
    assert snap["dropped_total"] == 0
    assert snap["by_type"].get("normal.fill") == 2  # 被挤出的普通事件按类型计数
    queued_types = [t for _d, _c, t in list(outbox.queue._queue)]
    assert "sync.conflict" in queued_types
    assert "notification.delivered" in queued_types


async def test_send_timeout_counted(monkeypatch):
    """发送超时丢弃分支补计数（复C2 核心：此前该分支连计数都不加）。"""
    monkeypatch.setenv("SIYI_WS_BROADCAST_TIMEOUT", "0.05")
    bridge = EventBridge()
    ws = SlowSocket()
    bridge.add_socket(ws)
    await bridge.broadcast("slow.evt", {"x": 1})
    await asyncio.sleep(0.3)  # 等 sender 触发 TimeoutError
    snap = bridge.stats()
    assert snap["send_timeout_total"] == 1
    assert snap["by_type"].get("slow.evt") == 1
    # B5 语义回归：超时后客户端保持连接（仅丢当前事件，不踢连接）
    assert ws in bridge._sockets
    bridge.remove_socket(ws)
    await asyncio.sleep(0.02)


async def test_stats_thread_safe_snapshot_from_worker_thread(monkeypatch):
    """stats() 可从工作线程读取（system_health 采集线程路径）。"""
    monkeypatch.setenv("SIYI_WS_CLIENT_QUEUE", "64")
    bridge = EventBridge()
    bridge.add_socket(FastSocket())
    snap = await asyncio.to_thread(bridge.stats)
    assert set(snap) == {"dropped_total", "evicted_total", "send_timeout_total", "by_type"}
    bridge.remove_socket(bridge._sockets.copy().pop())
    await asyncio.sleep(0.02)


def test_bridge_stats_concurrent_increments():
    """多线程并发递增计数不失真（线程安全硬证明）。"""
    from backend.event_bridge import _BridgeStats

    st = _BridgeStats()

    def worker(i: int) -> None:
        for _ in range(200):
            st.inc_dropped(f"t{i}")
            st.inc_evicted(f"t{i}")
            st.inc_send_timeout(f"t{i}")

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    snap = st.snapshot()
    assert snap["dropped_total"] == 1600
    assert snap["evicted_total"] == 1600
    assert snap["send_timeout_total"] == 1600
    assert sum(snap["by_type"].values()) == 4800
    assert all(v == 600 for v in snap["by_type"].values())


async def test_cross_thread_broadcast_drops_counted(monkeypatch):
    """工作线程经 call_soon_threadsafe 路径广播时统计仍正确（cron 场景）。

    SIYI_WS_BROADCAST_TIMEOUT=0 禁用 per-send 超时 → sender 在第 1 条上真实
    挂起（HungSocket 无 wait_for），queue=1 下第 3/4 条必然 QueueFull。
    """
    monkeypatch.setenv("SIYI_WS_CLIENT_QUEUE", "1")
    monkeypatch.setenv("SIYI_WS_BROADCAST_TIMEOUT", "0")
    bridge = EventBridge()
    ws = HungSocket()
    bridge.add_socket(ws)
    bridge.set_loop(asyncio.get_running_loop())  # 供跨线程 _on_app_event 定位循环
    bridge.start_bridging()

    def emit_from_thread(evt_type: str) -> None:
        event_bus.emit(AppEvent(evt_type, {"k": 1}))

    await asyncio.to_thread(emit_from_thread, "thread.flood")
    await asyncio.sleep(0.05)  # 让 sender 取走第 1 条并真实挂起
    await asyncio.to_thread(emit_from_thread, "thread.flood")  # 占住队列（1）
    await asyncio.to_thread(emit_from_thread, "thread.flood")  # QueueFull → 丢
    await asyncio.to_thread(emit_from_thread, "thread.flood")  # QueueFull → 丢
    await asyncio.sleep(0.05)
    snap = bridge.stats()
    assert snap["dropped_total"] == 2
    assert snap["by_type"].get("thread.flood") == 2
    # 清理：timeout=0 下排空任务无限等待，需显式取消
    bridge.stop_bridging()
    for task in list(bridge._pending_tasks):
        task.cancel()
    bridge.remove_socket(ws)
    await asyncio.sleep(0.02)


# ── 复C4：关键白名单 + SIYI_WS_CRITICAL_PREFIXES ────────────────


def test_default_whitelist_includes_task_resumable_and_proactive(monkeypatch):
    monkeypatch.delenv("SIYI_WS_CRITICAL_PREFIXES", raising=False)
    # 复C4 新增
    assert _is_critical_event("task.resumable")
    assert _is_critical_event("proactive.triggered")
    assert _is_critical_event("proactive.suggestion")
    # 既有 6 前缀不回退
    assert _is_critical_event("approval.created")
    assert _is_critical_event("sandbox.timeout")
    assert _is_critical_event("sync.completed")
    assert _is_critical_event("notification.delivered")
    assert _is_critical_event("chain.resumable")
    assert _is_critical_event("cron.job_alert")
    # 普通事件仍不进白名单
    assert not _is_critical_event("chat.chunk")
    assert not _is_critical_event("exec.step")


def test_env_critical_prefixes_override_default(monkeypatch):
    from backend.event_bridge import _resolve_critical_prefixes

    monkeypatch.setenv("SIYI_WS_CRITICAL_PREFIXES", "custom.alpha, custom.beta,,")
    assert _resolve_critical_prefixes() == ("custom.alpha", "custom.beta")
    assert _is_critical_event("custom.alpha.x")
    # 设置时整体覆盖缺省表
    assert not _is_critical_event("sync.completed")
    assert not _is_critical_event("task.resumable")


def test_env_critical_prefixes_blank_falls_back_to_default(monkeypatch):
    monkeypatch.setenv("SIYI_WS_CRITICAL_PREFIXES", "  ,, ")
    # 空白解析结果回退缺省表
    assert _is_critical_event("task.resumable")
    assert _is_critical_event("sync.completed")


# ── 复C3：EventBus 订阅方异常计数 ───────────────────────────────


def test_event_bus_handler_error_counted():
    bus = EventBus()
    calls: list[int] = []

    def bad(_evt: AppEvent) -> None:
        raise RuntimeError("boom")

    def good(_evt: AppEvent) -> None:
        calls.append(1)

    bus.on("t.*", bad)
    bus.on("t.*", good)
    bus.emit(AppEvent("t.x", {}))
    bus.emit(AppEvent("t.x", {}))
    assert bus.stats()["handler_errors"] == 2
    assert len(calls) == 2  # 正常订阅方不受抛错订阅方影响
    bus.clear()
    assert bus.stats()["handler_errors"] == 0  # clear 一并复位


# ── 复C2：system_health events 收集器 ───────────────────────────


def test_collect_events_unknown_without_bridge():
    from services.system_health import SEVERITY_UNKNOWN, _collect_events

    eb_module._active_bridge = None
    result = _collect_events(SimpleNamespace())
    assert result["severity"] == SEVERITY_UNKNOWN
    assert result["available"] is False


async def test_collect_events_healthy_and_exposes_stats_fields(monkeypatch):
    from services.system_health import SEVERITY_HEALTHY, _collect_events

    monkeypatch.setenv("SIYI_WS_CLIENT_QUEUE", "64")
    monkeypatch.setenv("SIYI_WS_BROADCAST_TIMEOUT", "0.05")
    bridge = EventBridge()
    bridge.add_socket(FastSocket())
    bridge.start_bridging()
    await bridge.broadcast("health.evt", {"k": 1})
    await asyncio.sleep(0.02)
    result = _collect_events(SimpleNamespace())
    assert result["severity"] == SEVERITY_HEALTHY
    assert result["available"] is True
    d = result["details"]
    assert d["dropped_total"] == 0
    assert d["evicted_total"] == 0
    assert d["send_timeout_total"] == 0
    assert isinstance(d["by_type"], dict)
    assert "subscriber_errors" in d
    bridge.stop_bridging()


async def test_collect_events_exposes_drops(monkeypatch):
    """验收口径：发生丢弃后 health 输出 dropped_total>0（含 by_type 归因）。"""
    from services.system_health import _collect_events

    monkeypatch.setenv("SIYI_WS_CLIENT_QUEUE", "3")
    bridge = EventBridge()
    ws = FastSocket()
    bridge.add_socket(ws)
    bridge.start_bridging()
    outbox = bridge._outboxes[ws]
    # 同步连续入队（sender 无 await 机会无法消费）：3 入队 + 1 丢弃
    for i in range(4):
        outbox.enqueue(json.dumps({"i": i}), critical=False, event_type="flood.evt")
    result = _collect_events(SimpleNamespace())
    d = result["details"]
    assert d["dropped_total"] == 1
    assert d["by_type"].get("flood.evt") == 1
    bridge.stop_bridging()
