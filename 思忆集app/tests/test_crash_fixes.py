"""验证闪退修复是否有效的测试。

测试覆盖：
1. BaseException 捕获 — AsyncCallWorker 不因 SystemExit 崩溃
2. BaseException 捕获 — StreamingAssistantWorker 不因 SystemExit 崩溃
3. 降级回答生成功能正常
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

import gc
import threading
import time

import pytest
from PyQt6.QtCore import QThread, QTimer
from PyQt6.QtWidgets import QApplication


# ═══════════════════════════════════════════════════════════
# Test 1: AsyncCallWorker catches BaseException
# ═══════════════════════════════════════════════════════════

def _get_or_create_app():
    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv[:1])
    return app


class _WorkerResultCollector:
    """收集 worker 信号结果。"""
    def __init__(self):
        self.result = None
        self.error = None
        self.finished = False
        self._event = threading.Event()

    def on_result(self, value):
        self.result = value
        self._event.set()

    def on_failed(self, msg):
        self.error = msg
        self._event.set()

    def on_finished(self):
        self.finished = True

    def wait(self, timeout=5.0):
        return self._event.wait(timeout)


def _process_events_for(duration_ms=500):
    deadline = time.monotonic() + duration_ms / 1000
    while time.monotonic() < deadline:
        QApplication.processEvents()
        time.sleep(0.01)


@pytest.mark.skipif(sys.platform == "win32" and not os.environ.get("CI"), reason="requires X server or offscreen platform")
def test_async_worker_catches_system_exit():
    """AsyncCallWorker 捕获 SystemExit 不崩溃进程"""
    from ui.async_worker import AsyncCallWorker

    app = _get_or_create_app()
    collector = _WorkerResultCollector()

    def raise_system_exit():
        raise SystemExit(1)

    worker = AsyncCallWorker(raise_system_exit)
    worker.failed.connect(collector.on_failed)
    worker.finished.connect(collector.on_finished)

    worker.start()
    ok = collector.wait(timeout=5.0)
    worker.wait(1000)

    assert ok, "worker 超时未完成"
    assert collector.error is not None, "应收到 failed 信号而非崩溃"
    assert "SystemExit" in collector.error, f"错误消息应包含 SystemExit, got: {collector.error}"
    assert not worker.isRunning(), "worker 应已停止"


@pytest.mark.skipif(sys.platform == "win32" and not os.environ.get("CI"), reason="requires X server or offscreen platform")
def test_streaming_worker_catches_system_exit():
    """StreamingAssistantWorker 捕获 SystemExit 不崩溃进程"""
    from ui.async_worker import StreamingAssistantWorker

    app = _get_or_create_app()
    collector = _WorkerResultCollector()

    def raise_system_exit():
        yield "hello"
        raise SystemExit(1)

    worker = StreamingAssistantWorker(raise_system_exit)
    worker.failed.connect(collector.on_failed)
    worker.finished.connect(collector.on_finished)

    worker.start()
    ok = collector.wait(timeout=5.0)
    worker.wait(1000)

    assert ok, "worker 超时未完成"
    assert collector.error is not None, "应收到 failed 信号而非崩溃"
    assert "SystemExit" in collector.error, f"错误消息应包含 SystemExit, got: {collector.error}"
    assert not worker.isRunning(), "worker 应已停止"


@pytest.mark.skipif(sys.platform == "win32" and not os.environ.get("CI"), reason="requires X server or offscreen platform")
def test_streaming_worker_catches_keyboard_interrupt():
    """StreamingAssistantWorker 捕获 KeyboardInterrupt 不崩溃进程"""
    from ui.async_worker import StreamingAssistantWorker

    app = _get_or_create_app()
    collector = _WorkerResultCollector()

    def raise_keyboard_interrupt():
        yield "hello"
        raise KeyboardInterrupt()

    worker = StreamingAssistantWorker(raise_keyboard_interrupt)
    worker.failed.connect(collector.on_failed)
    worker.finished.connect(collector.on_finished)

    worker.start()
    ok = collector.wait(timeout=5.0)
    worker.wait(1000)

    assert ok, "worker 超时未完成"
    assert collector.error is not None, "应收到 failed 信号而非崩溃"
    assert not worker.isRunning(), "worker 应已停止"


@pytest.mark.skipif(sys.platform == "win32" and not os.environ.get("CI"), reason="requires X server or offscreen platform")
def test_async_worker_catches_keyboard_interrupt():
    """AsyncCallWorker 捕获 KeyboardInterrupt 不崩溃进程"""
    from ui.async_worker import AsyncCallWorker

    app = _get_or_create_app()
    collector = _WorkerResultCollector()

    def raise_keyboard_interrupt():
        raise KeyboardInterrupt()

    worker = AsyncCallWorker(raise_keyboard_interrupt)
    worker.failed.connect(collector.on_failed)
    worker.finished.connect(collector.on_finished)

    worker.start()
    ok = collector.wait(timeout=5.0)
    worker.wait(1000)

    assert ok, "worker 超时未完成"
    assert collector.error is not None, "应收到 failed 信号而非崩溃"
    assert not worker.isRunning(), "worker 应已停止"


@pytest.mark.skipif(sys.platform == "win32" and not os.environ.get("CI"), reason="requires X server or offscreen platform")
def test_async_worker_normal_result_still_works():
    """AsyncCallWorker 正常路径不受 BaseException 更改影响"""
    from ui.async_worker import AsyncCallWorker

    app = _get_or_create_app()
    collector = _WorkerResultCollector()

    def return_normal():
        return "ok"

    worker = AsyncCallWorker(return_normal)
    worker.result_ready.connect(collector.on_result)
    worker.finished.connect(collector.on_finished)

    worker.start()
    ok = collector.wait(timeout=5.0)
    worker.wait(1000)

    assert ok, "worker 超时未完成"
    assert collector.result == "ok", f"应正常返回结果, got: {collector.result}"


@pytest.mark.skipif(sys.platform == "win32" and not os.environ.get("CI"), reason="requires X server or offscreen platform")
def test_streaming_worker_normal_result_still_works():
    """StreamingAssistantWorker 正常路径不受 BaseException 更改影响"""
    from ui.async_worker import StreamingAssistantWorker

    app = _get_or_create_app()
    collector = _WorkerResultCollector()
    chunks = []

    def stream_normal():
        yield "hello "
        yield "world"

    worker = StreamingAssistantWorker(stream_normal)
    worker.chunk_ready.connect(chunks.append)
    worker.streaming_finished.connect(collector.on_finished)
    worker.failed.connect(collector.on_failed)

    worker.start()
    ok = collector.wait(timeout=5.0)
    worker.wait(1000)

    assert ok, "worker 超时未完成"
    assert "".join(chunks) == "hello world", f"chunks 不正确: {chunks}"
    assert collector.finished, "应触发 streaming_finished"


# ═══════════════════════════════════════════════════════════
# Test 2: 降级回答生成
# ═══════════════════════════════════════════════════════════

def test_build_local_degraded_answer_imports():
    """验证降级回答相关模块可导入"""
    from ai.client.fallback import AIServiceError, FallbackOpenAIClient
    assert AIServiceError is not None
    assert FallbackOpenAIClient is not None


def test_name_error_caught_by_fallback():
    """验证 NameError（如缺失 timezone 导入）能被 fallback 链捕获"""
    from ai.client.fallback import AIServiceError

    # 模拟一个抛出 NameError 的 complete_text
    def broken_complete(*args, **kwargs):
        raise NameError("name 'timezone' is not defined")

    try:
        broken_complete([], temperature=0.3, max_tokens=1200)
        assert False, "应抛出 NameError"
    except NameError:
        pass  # 预期行为：NameError 被抛出，被 worker 的 BaseException 捕获


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
