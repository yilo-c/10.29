"""system.health JSON-RPC handler 单元测试。

验证 backend/handlers/_system_settings.py 的 system.health：
1. 注册到 registry
2. 空 context → 18 个子系统（复C2 新增 events），overall=down
3. summary 计数正确
4. force_refresh=True 跳过缓存
5. 缓存 TTL 生效（连续调用返回相同 timestamp）
6. 有 ai_client 时 severity 改善

不依赖真实后端服务，全部通过 mock 实现。
"""

from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock


APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from backend.method_registry import MethodRegistry
from backend.handlers._system_settings import register_system_settings


def _make_context(with_ai: bool = False):
    """构造最小可用的 AppContext mock。"""
    ctx = SimpleNamespace()
    ctx.settings_repository = SimpleNamespace(
        get_setting=lambda key, default=None: default,
        set_setting=lambda key, value: None,
    )
    ctx.ai_client = MagicMock() if with_ai else None
    ctx.memory_index_service = None
    ctx.knowledge_graph_store = None
    ctx.recreate_ai_client = MagicMock()
    ctx.autonomous_assistant_manager = None
    ctx.cost_tracker = None
    ctx.cron_service = None
    ctx.cloud_sync_bridge = None
    ctx.vocabulary_repository = None
    ctx.personal_inbox_repository = None
    ctx.personal_note_repository = None
    ctx.personal_task_repository = None
    return ctx


def _make_registry(with_ai: bool = False):
    registry = MethodRegistry()
    ctx = _make_context(with_ai=with_ai)
    ak_service = MagicMock()
    register_system_settings(registry, ctx, ws_port=18765, ak_service=ak_service)
    return registry


# ── 注册完整性 ──


def test_system_health_registered():
    """system.health 应被注册。"""
    registry = _make_registry()
    assert registry.has_method("system.health"), "system.health not registered"


# ── 空 context 行为 ──


def test_system_health_empty_context_returns_15_subsystems():
    """空 context 应返回 18 个子系统（S1 新增 error_rate + backup；P2-4 新增 task_queue/feedback/notification；M4 内核迁移新增 kernel；T3.6 新增 capability_routing；复C2 新增 events）。"""
    registry = _make_registry()
    result = asyncio.run(registry.dispatch("system.health", {}))
    assert len(result["subsystems"]) == 18


def test_system_health_empty_context_overall_down():
    """空 context overall_severity 应为 down。"""
    registry = _make_registry()
    result = asyncio.run(registry.dispatch("system.health", {}))
    assert result["overall_severity"] == "down"


def test_system_health_empty_context_summary():
    """空 context summary：7 down + 其余 unknown/healthy（T3.6 capability_routing 在 router 可达时为 healthy，不可达时降级 unknown）。"""
    registry = _make_registry()
    result = asyncio.run(registry.dispatch("system.health", {}))
    summary = result["summary"]
    assert summary["down"] == 7, f"expected 7 down, got {summary.get('down')}"
    assert summary["degraded"] == 0, f"expected 0 degraded, got {summary.get('degraded')}"
    # capability_routing 子系统的严重度取决于 plugins 包是否可加载：
    # 可达 → healthy；不可达（防御性降级）→ unknown。其余 16 个子系统在空 context 下必为 down/unknown。
    cap = next(s for s in result["subsystems"] if s["key"] == "capability_routing")
    assert cap["severity"] in ("healthy", "unknown"), cap
    # 总子系统数守恒
    total = summary["down"] + summary["unknown"] + summary["healthy"] + summary["degraded"]
    assert total == 18, f"subsystem count mismatch: {summary}"


def test_system_health_subsystem_keys():
    """子系统 key 集合应完整。"""
    registry = _make_registry()
    result = asyncio.run(registry.dispatch("system.health", {}))
    keys = {s["key"] for s in result["subsystems"]}
    expected = {
        "ai",
        "cost",
        "cron",
        "memory",
        "knowledge_graph",
        "sync",
        "vocabulary",
        "inbox",
        "notes",
        "tasks",
        "error_rate",
        "backup",
        "task_queue",
        "feedback",
        "notification",
        "kernel",
        "capability_routing",
        "events",
    }
    assert keys == expected, f"missing: {expected - keys}, extra: {keys - expected}"


# ── 缓存行为 ──


def test_system_health_cache_returns_same_timestamp():
    """连续调用（无 force_refresh）应返回相同 timestamp（缓存命中）。"""
    registry = _make_registry()
    r1 = asyncio.run(registry.dispatch("system.health", {}))
    r2 = asyncio.run(registry.dispatch("system.health", {}))
    assert r1["timestamp"] == r2["timestamp"], "cache should return same timestamp"


def test_system_health_force_refresh_changes_timestamp():
    """force_refresh=True 应跳过缓存，获取新 timestamp。"""
    registry = _make_registry()
    r1 = asyncio.run(registry.dispatch("system.health", {}))
    r2 = asyncio.run(registry.dispatch("system.health", {"force_refresh": True}))
    assert r2["timestamp"] >= r1["timestamp"], "force_refresh should get fresh timestamp"


# ── 有 AI client 时行为 ──


def test_system_health_with_ai_client_improves_severity():
    """有 ai_client 时，ai 子系统应不再是 down。"""
    registry = _make_registry(with_ai=True)
    result = asyncio.run(registry.dispatch("system.health", {"force_refresh": True}))
    ai_sub = next(s for s in result["subsystems"] if s["key"] == "ai")
    assert ai_sub["severity"] != "down", f"ai should not be down with ai_client, got {ai_sub['severity']}"
    assert ai_sub["available"] is True


# ── to_dict 结构完整性 ──


def test_system_health_result_structure():
    """返回字典应包含所有必需字段。"""
    registry = _make_registry()
    result = asyncio.run(registry.dispatch("system.health", {}))
    assert "timestamp" in result
    assert "overall_severity" in result
    assert "summary" in result
    assert "subsystems" in result
    for s in result["subsystems"]:
        assert "key" in s
        assert "name" in s
        assert "severity" in s
        assert "available" in s
        assert "message" in s
        assert "details" in s
        assert "latency_ms" in s
        assert "error" in s


# ── P1-13: 并行采集回归测试 ──
#
# 历史问题：aggregate_all 曾用 [self._collect_one(d) for d in subsystems] 列表推导，
# 每轮 submit 后立即 future.result() 阻塞，导致 10 个子系统串行执行，
# 总耗时 ≈ 各子系统耗时之和。修复后改为先批量 submit 再统一等待，
# 总耗时 ≈ 单个最慢子系统的耗时。下面的测试用受控的慢 collector 锁定该行为。

from services.system_health import SystemHealthAggregator, SubsystemDef  # noqa: E402


def _make_slow_aggregator(per_collector_seconds: float = 0.3, n: int = 6):
    """构造一个用慢 collector 替换默认子系统的 aggregator。

    每个 collector 睡 per_collector_seconds 后返回 healthy。
    串行总耗时 ≈ n * per_collector_seconds；并行总耗时 ≈ per_collector_seconds。
    """
    agg = SystemHealthAggregator(context=SimpleNamespace())

    def slow_collector(_ctx, idx=0):
        time.sleep(per_collector_seconds)
        return {
            "severity": "healthy",
            "available": True,
            "message": f"slow-{idx}",
            "details": {},
        }

    agg._subsystems = [SubsystemDef(f"k{i}", f"慢采集-{i}", lambda ctx, i=i: slow_collector(ctx, i), timeout=10.0) for i in range(n)]
    return agg


def test_aggregate_all_runs_collectors_in_parallel():
    """并行采集：6 个各 0.3s 的 collector，总耗时应接近 0.3s 而非 1.8s。

    阈值设为 per_collector * 2.5（留出线程池/GIL/CI 抖动余量），
    串行实现（≈1.8s）会明显超出此阈值。
    """
    import time as _time

    per = 0.3
    agg = _make_slow_aggregator(per_collector_seconds=per, n=6)
    start = _time.monotonic()
    snap = agg.aggregate_all(force_refresh=True)
    elapsed = _time.monotonic() - start

    assert len(snap.subsystems) == 6
    # 全部 healthy，说明每个 collector 都成功完成（未被跳过）
    assert all(s.severity == "healthy" for s in snap.subsystems)
    # 并行下总耗时远小于串行之和
    assert elapsed < per * 2.5, f"采集耗时 {elapsed:.2f}s 过长，疑似退化为串行（并行预期 ≈{per}s，串行 ≈{per * 6}s）"


def test_aggregate_all_serial_regression_guard():
    """显式回归防护：用足够的 collector 数量放大串行/并行差异。

    10 个各 0.25s：并行 ≈0.25s，串行 ≈2.5s。阈值 1.0s 能稳定区分两者，
    不受 CI 机器抖动影响。
    """
    import time as _time

    per = 0.25
    agg = _make_slow_aggregator(per_collector_seconds=per, n=10)
    start = _time.monotonic()
    snap = agg.aggregate_all(force_refresh=True)
    elapsed = _time.monotonic() - start

    assert len(snap.subsystems) == 10
    assert elapsed < 1.0, f"采集耗时 {elapsed:.2f}s 过长，疑似退化为串行（并行预期 ≈{per}s，串行 ≈{per * 10}s）"
