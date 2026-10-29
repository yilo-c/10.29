"""CapabilityRouter 并发契约测试（T4.2）。

验证共享状态（遥测计数 _hits）在并发下不丢更新：
- N 线程并发 invoke（plugin 命中 + fallback 命中混合），计数精确等于调用次数；
- 读快照（get_capability_stats）与写（_record_hit）并发时无崩溃/无脏数据；
- 计数在锁下为原子，无 GIL 释放窗口导致的丢失。
"""
from __future__ import annotations

import sys
import threading
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from plugins.capability_router import CapabilityRouter


def _make_router():
    router = CapabilityRouter()

    def _plugin(params=None):
        return {"ok": True, "path": "plugin"}

    def _fallback(params=None):
        return {"ok": True, "path": "fallback"}

    router.register_plugin_invoker("c_plugin", _plugin)
    router.register_fallback("c_plugin", _fallback)
    router.register_fallback("c_fallback_only", _fallback)
    return router


def test_concurrent_plugin_hits_exact():
    """N 线程并发 invoke plugin 命中，plugin_hits 精确等于 N。"""
    router = _make_router()
    n = 500
    errors = []

    def _worker(_i):
        try:
            for _ in range(20):
                result = router.invoke("c_plugin", {})
                assert result["path"] == "plugin"
        except Exception as exc:  # pragma: no cover
            errors.append(exc)

    threads = [threading.Thread(target=_worker, args=(i,)) for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors
    stats = router.get_capability_stats()
    # 每次 invoke 成功走 plugin 命中：n * 20
    assert stats["c_plugin"]["plugin_hits"] == n * 20
    assert stats["c_plugin"].get("fallback_hits", 0) == 0


def test_concurrent_fallback_hits_exact():
    """并发 invoke 仅 fallback 的能力，fallback_hits 精确。"""
    router = _make_router()
    n = 300

    def _worker(_i):
        for _ in range(10):
            result = router.invoke("c_fallback_only", {})
            assert result["path"] == "fallback"

    threads = [threading.Thread(target=_worker, args=(i,)) for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    stats = router.get_capability_stats()
    assert stats["c_fallback_only"]["fallback_hits"] == n * 10


def test_concurrent_reads_during_writes_no_error():
    """写计数与读快照并发执行，无崩溃、无脏数据。"""
    router = _make_router()
    stop = threading.Event()
    write_errors = []
    read_errors = []

    def _writer(_i):
        try:
            while not stop.is_set():
                router.invoke("c_plugin", {})
        except Exception as exc:  # pragma: no cover
            write_errors.append(exc)

    def _reader(_i):
        try:
            while not stop.is_set():
                stats = router.get_capability_stats()
                assert isinstance(stats, dict)
        except Exception as exc:  # pragma: no cover
            read_errors.append(exc)

    writers = [threading.Thread(target=_writer, args=(i,)) for i in range(4)]
    readers = [threading.Thread(target=_reader, args=(i,)) for i in range(4)]
    all_threads = writers + readers
    for t in all_threads:
        t.start()
    import time
    time.sleep(0.3)
    stop.set()
    for t in all_threads:
        t.join()

    assert not write_errors
    assert not read_errors


def test_mixed_plugin_and_error_counts_stable():
    """plugin 抛异常转 fallback 时，plugin_hits + plugin_errors + fallback_hits 总和精确。"""
    router = CapabilityRouter()
    calls = 0
    lock = threading.Lock()

    def _flaky_plugin(params=None):
        nonlocal calls
        with lock:
            calls += 1
            if calls % 2 == 0:  # 一半成功，一半抛异常转 fallback
                return {"ok": True}
            raise RuntimeError("flaky")

    def _fallback(params=None):
        return {"ok": True, "path": "fallback"}

    router.register_plugin_invoker("c_flaky", _flaky_plugin)
    router.register_fallback("c_flaky", _fallback)

    n = 200

    def _worker(_i):
        for _ in range(5):
            router.invoke("c_flaky", {})

    threads = [threading.Thread(target=_worker, args=(i,)) for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    stats = router.get_capability_stats()["c_flaky"]
    # 每次调用：成功 → plugin_hits(+1)；失败 → plugin_errors(+1) 且转 fallback → fallback_hits(+1)。
    # 因此 plugin_hits + plugin_errors == 调用数；plugin_errors == fallback_hits。
    assert stats["plugin_hits"] + stats["plugin_errors"] == n * 5
    assert stats["plugin_errors"] == stats["fallback_hits"]  # 每次异常转一次 fallback
    assert stats["plugin_errors"] > 0  # 确保确实发生了失败路径
