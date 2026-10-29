"""T4.1 子进程隔离测试：隔离模式选择、单例宿主、崩溃自愈、IPC 序列化健壮性。

子进程隔离（subprocess_host）是插件化深化（阶段四 M4）的核心交付。本文覆盖：
- choose_isolation_mode 的环境变量分支（默认进程内沙箱，显式开启子进程）；
- run_plugin_isolated 端到端经真实子进程执行插件；
- 杀 worker 后自动重建、业务无感（restart_on_crash）；
- 不可序列化返回在 worker 侧被转为明确错误而非崩溃/挂起。
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

import pytest

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))


# ── choose_isolation_mode ───────────────────────────────────────────────────


class TestChooseIsolationMode:
    def test_default_is_sandbox(self, monkeypatch):
        monkeypatch.delenv("SIYI_PLUGIN_ISOLATION", raising=False)
        from plugins.sandbox import choose_isolation_mode

        assert choose_isolation_mode() == "sandbox"

    def test_opt_in_subprocess(self, monkeypatch):
        monkeypatch.setenv("SIYI_PLUGIN_ISOLATION", "subprocess")
        from plugins.sandbox import choose_isolation_mode

        assert choose_isolation_mode() == "subprocess"

    def test_unknown_value_falls_back_to_sandbox(self, monkeypatch):
        monkeypatch.setenv("SIYI_PLUGIN_ISOLATION", "bogus")
        from plugins.sandbox import choose_isolation_mode

        assert choose_isolation_mode() == "sandbox"


# ── run_plugin_isolated（真实子进程）───────────────────────────────────────


class TestRunPluginIsolated:
    def test_execute_via_subprocess(self):
        """经子进程执行 textstats 插件并返回结构化结果。"""
        from plugins.sandbox import run_plugin_isolated

        result = run_plugin_isolated(
            "com.siyi.test",
            "plugins.installed.textstats.plugin:TextStatsPlugin",
            {"text": "hello 你好 world!"},
        )
        assert isinstance(result, dict)
        assert "chars" in result  # textstats 返回结构含统计字段
        assert result["chars"] == 15

    def test_worker_crash_recovery(self):
        """杀掉 worker 进程后，下一次 call 应自动重启并使业务无感。"""
        from plugins.sandbox import _get_subprocess_host

        host = _get_subprocess_host()
        assert host._proc is not None
        pid = host._proc.pid
        os.kill(pid, 9)  # 模拟强杀 worker
        deadline = time.time() + 10
        while time.time() < deadline:
            try:
                resp = host.execute_plugin(
                    "plugins.installed.textstats.plugin:TextStatsPlugin",
                    {"text": "hello"},
                )
            except Exception:
                time.sleep(0.3)
                continue
            assert isinstance(resp, dict)
            return  # 自愈成功
        pytest.fail("worker 被杀后未能自动重建/恢复")


# ── worker 序列化健壮性（含不可序列化返回）────────────────────────────────


class TestWorkerSerialization:
    def test_injected_nonserializable_reports_clean_error(self):
        """替换 _worker_handle 返回含 lambda 的对象，验证序列化为明确错误。"""
        from plugins import subprocess_host as s

        original = s._worker_handle
        captured: list[str] = []

        def fake_handle(method, params):
            return {"bad": lambda: 1}  # lambda 不可 JSON 序列化

        s._worker_handle = fake_handle  # type: ignore[assignment]
        real_stdout, real_stdin = sys.stdout, sys.stdin

        class _FakeOut:
            def write(self, x: str) -> None:
                captured.append(x)

            def flush(self) -> None:
                pass

        class _FakeIn:
            def __iter__(self):
                yield '{"id": 1, "method": "execute", "params": {}}\n'

        sys.stdout = _FakeOut()  # type: ignore[assignment]
        sys.stdin = _FakeIn()  # type: ignore[assignment]
        try:
            s._run_worker_stdio()
        finally:
            sys.stdout = real_stdout  # type: ignore[assignment]
            sys.stdin = real_stdin  # type: ignore[assignment]
            s._worker_handle = original

        assert captured, "worker 应输出一行"
        resp = json.loads(captured[0])
        assert resp.get("ok") is False
        assert "NonSerializableResult" in resp.get("error", "")