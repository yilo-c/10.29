"""Tests for persistent WorkerCodeExecutor."""
from __future__ import annotations

import time

from assistant.bridging.code_worker import WorkerCodeExecutor


class TestWorkerBasic:
    def test_execute_print(self):
        worker = WorkerCodeExecutor(timeout=10)
        try:
            result = worker.execute('print("hello from worker")')
            assert result["error"] is None
            assert "hello from worker" in result["output"]
        finally:
            worker.close()

    def test_execute_math(self):
        worker = WorkerCodeExecutor(timeout=10)
        try:
            result = worker.execute('print(2 ** 10)')
            assert result["error"] is None
            assert "1024" in result["output"]
        finally:
            worker.close()

    def test_state_persistence(self):
        """变量在多次 execute 间持续保持。"""
        worker = WorkerCodeExecutor(timeout=10)
        try:
            r1 = worker.execute("x = 42")
            assert r1["error"] is None

            r2 = worker.execute("print(x)")
            assert r2["error"] is None
            assert "42" in r2["output"]
        finally:
            worker.close()

    def test_import_persistence(self):
        """import 在多次 execute 间持续保持。"""
        worker = WorkerCodeExecutor(timeout=10)
        try:
            r1 = worker.execute("import json")
            assert r1["error"] is None

            r2 = worker.execute('print(json.dumps({"a": 1}))')
            assert r2["error"] is None
            assert '{"a": 1}' in r2["output"]
        finally:
            worker.close()

    def test_multiple_sequential(self):
        """多次按序执行。"""
        worker = WorkerCodeExecutor(timeout=10)
        try:
            for i in range(5):
                result = worker.execute(f"print({i})")
                assert result["error"] is None
                assert str(i) in result["output"]
        finally:
            worker.close()


class TestWorkerSafety:
    def test_blocked_eval(self):
        worker = WorkerCodeExecutor(timeout=10)
        try:
            result = worker.execute('eval("1+1")')
            assert result["error"] is not None
        finally:
            worker.close()

    def test_blocked_exec(self):
        worker = WorkerCodeExecutor(timeout=10)
        try:
            result = worker.execute('exec("print(1)")')
            assert result["error"] is not None
        finally:
            worker.close()

    def test_blocked_import_ctypes(self):
        worker = WorkerCodeExecutor(timeout=10)
        try:
            result = worker.execute("import ctypes")
            assert result["error"] is not None
        finally:
            worker.close()

    def test_allowed_imports(self):
        worker = WorkerCodeExecutor(timeout=10)
        try:
            for mod in ("json", "re", "pathlib", "datetime", "math", "random"):
                result = worker.execute(f"import {mod}; print({mod}.__name__)")
                assert result["error"] is None, f"import {mod} failed: {result['error']}"
        finally:
            worker.close()

    def test_timeout_kills_worker(self):
        """无限循环应超时并重启 Worker。"""
        worker = WorkerCodeExecutor(timeout=2)
        try:
            result = worker.execute("while True: pass")
            assert result["timed_out"] is True
            assert result["error"] is not None
            assert "超时" in result["error"]

            # 超时后 Worker 应自动重启，后续可用
            r2 = worker.execute("print('still alive')")
            assert r2["error"] is None
            assert "still alive" in r2["output"]
        finally:
            worker.close()


class TestWorkerLifecycle:
    def test_ping(self):
        worker = WorkerCodeExecutor(timeout=10)
        try:
            assert worker.ping() is True
        finally:
            worker.close()

    def test_ping_after_close(self):
        worker = WorkerCodeExecutor(timeout=10)
        worker.close()
        assert worker.ping() is False

    def test_close_then_execute_raises(self):
        worker = WorkerCodeExecutor(timeout=10)
        worker.close()
        import pytest
        with pytest.raises(RuntimeError, match="已关闭"):
            worker.execute("print('x')")

    def test_context_manager(self):
        with WorkerCodeExecutor(timeout=10) as worker:
            result = worker.execute("print('ctx mgr')")
            assert result["error"] is None
            assert "ctx mgr" in result["output"]

    def test_restart_after_crash(self):
        """模拟 Worker 崩溃后自动重启。"""
        worker = WorkerCodeExecutor(timeout=10)
        try:
            # 让 Worker 崩溃
            worker.execute("import sys; sys.exit(1)")
            time.sleep(0.5)

            # 应自动重启
            result = worker.execute("print('recovered')")
            assert result["error"] is None
            assert "recovered" in result["output"]
        finally:
            worker.close()


class TestWorkerDuration:
    def test_duration_tracked(self):
        worker = WorkerCodeExecutor(timeout=10)
        try:
            result = worker.execute("print('fast')")
            assert result["duration_ms"] >= 0
            assert not result["timed_out"]
        finally:
            worker.close()

    def test_error_result(self):
        worker = WorkerCodeExecutor(timeout=10)
        try:
            result = worker.execute("1/0")
            assert result["error"] is not None
            assert "ZeroDivisionError" in result["error"]
        finally:
            worker.close()
