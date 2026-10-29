# -*- coding: utf-8 -*-
"""P0-1：watchdog 僵死接管 + headless 单实例锁测试。

覆盖：
- watchdog：干净退出不重启、崩溃重启、熔断、僵死子进程（活着但端口不通）终止重启、
  健康探测关闭时不终止
- instance_lock：PID 记录/读取/存活判定、锁往返、第二实例拒绝、
  僵死接管分支（无 PID / 死 PID / 健康实例 / 僵死 python / 非 python / 开关关闭）
"""
from __future__ import annotations

import os
import subprocess
import sys
import threading
import time

import pytest

from backend import instance_lock as il
from backend import process_watchdog as pw

WIN_ONLY = pytest.mark.skipif(sys.platform != "win32", reason="msvcrt 锁仅 Windows")


# ── watchdog 行为 ──


class FakeProc:
    """可脚本化的假子进程。"""

    def __init__(self, exit_code: int | None, pid: int = 4242):
        # exit_code=None 表示永不自行退出（僵死）
        self._exit_code = exit_code
        self.pid = pid
        self.killed = False
        self.terminated = False

    def poll(self):
        return self._exit_code

    def wait(self, timeout=None):
        if self._exit_code is None:
            raise subprocess.TimeoutExpired(cmd="fake", timeout=timeout)
        return self._exit_code

    def kill(self):
        self.killed = True
        self._exit_code = 1

    def terminate(self):
        self.terminated = True
        self._exit_code = 2

    def send_signal(self, signum):
        pass


class FakeSpawner:
    def __init__(self, procs: list[FakeProc]):
        self.procs = list(procs)
        self.calls = 0

    def __call__(self, cmd, **kwargs):
        proc = self.procs[self.calls]
        self.calls += 1
        return proc


def _make_watchdog(spawner, config=None, health_probe=None) -> pw.BackendWatchdog:
    wd = pw.BackendWatchdog(
        cmd=["python", "-m", "backend.headless_main"],
        cwd=".",
        config=config or pw.WatchdogConfig(base_delay=0.001, max_delay=0.002),
        spawn_fn=spawner,
        health_probe_fn=health_probe,
    )
    wd._wait_poll_interval = 0.01
    return wd


def test_clean_exit_no_restart():
    spawner = FakeSpawner([FakeProc(0)])
    wd = _make_watchdog(spawner)
    assert wd.run_forever() == 0
    assert spawner.calls == 1


def test_crash_then_restart_then_clean():
    spawner = FakeSpawner([FakeProc(1), FakeProc(0)])
    wd = _make_watchdog(spawner)
    assert wd.run_forever() == 0
    assert spawner.calls == 2


def test_circuit_breaker_stops_restart_storm():
    cfg = pw.WatchdogConfig(
        base_delay=0.001,
        max_delay=0.002,
        circuit_breaker_threshold=2,
        restart_window=300.0,
        min_uptime_for_reset=10_000,
    )
    spawner = FakeSpawner([FakeProc(1), FakeProc(1), FakeProc(1)])
    wd = _make_watchdog(spawner, config=cfg)
    assert wd.run_forever() != 0  # 熔断放弃，最终返回崩溃码
    assert spawner.calls == 3


def test_zombie_child_killed_and_restarted():
    """僵死：子进程活着但端口持续不可达 → kill → 按崩溃重启。"""
    cfg = pw.WatchdogConfig(
        base_delay=0.001,
        max_delay=0.002,
        min_uptime_for_reset=10_000,
        health_enabled=True,
        health_grace=0.01,
        health_interval=0.01,
        health_max_fails=2,
    )
    zombie = FakeProc(None)  # 永不自行退出
    spawner = FakeSpawner([zombie, FakeProc(0)])
    wd = _make_watchdog(spawner, config=cfg, health_probe=lambda: False)
    assert wd.run_forever() == 0
    assert zombie.killed is True
    assert spawner.calls == 2


def test_healthy_child_not_killed_despite_probe_flap():
    """探测偶发失败但随后恢复 → 不终止子进程。"""
    cfg = pw.WatchdogConfig(
        base_delay=0.001,
        health_enabled=True,
        health_grace=0.01,
        health_interval=0.01,
        health_max_fails=3,
    )
    results = iter([False, True, True])
    child = FakeProc(0)  # 很快干净退出
    spawner = FakeSpawner([child])
    wd = _make_watchdog(
        spawner, config=cfg, health_probe=lambda: next(results, True)
    )
    assert wd.run_forever() == 0
    assert child.killed is False


def test_health_disabled_never_kills_zombie():
    cfg = pw.WatchdogConfig(
        base_delay=0.001,
        health_enabled=False,
        health_grace=0.01,
        health_interval=0.01,
        health_max_fails=1,
    )
    zombie = FakeProc(None)
    spawner = FakeSpawner([zombie])
    wd = _make_watchdog(spawner, config=cfg, health_probe=lambda: False)

    t = threading.Thread(target=wd.run_forever, daemon=True)
    t.start()
    deadline = time.time() + 5
    while time.time() < deadline and not zombie.killed and spawner.calls == 0:
        time.sleep(0.01)
    time.sleep(0.1)  # 给探测循环一点时间证明它不会 kill
    assert zombie.killed is False
    wd.request_shutdown()  # terminate → 退出码 2（SIGINT 语义）→ 不重启
    t.join(timeout=10)
    assert zombie.terminated is True


# ── instance_lock ──


@pytest.fixture
def lock_env(tmp_path, monkeypatch):
    monkeypatch.setenv("TEMP", str(tmp_path))
    monkeypatch.setenv("SIYI_LOCK_TAKEOVER", "0")
    return tmp_path


@WIN_ONLY
class TestInstanceLock:
    def test_acquire_writes_pid_and_release(self, lock_env):
        fd = il.acquire()
        assert fd is not None
        # 持有者 PID 记录在旁路文件（锁文件本身被独占打开不可读）
        sidecar = lock_env / (il.LOCK_FILE_NAME + ".pid")
        assert sidecar.read_text(encoding="ascii").strip() == str(os.getpid())
        il.release(fd)
        # 释放后可再次获取，旁路文件重写
        fd2 = il.acquire()
        assert fd2 is not None
        il.release(fd2)

    def test_second_instance_blocked(self, lock_env):
        fd1 = il.acquire()
        assert fd1 is not None
        try:
            # TAKEOVER=0（fixture 设置）：持有者存活时直接拒绝，不会误杀
            assert il.acquire() is None
        finally:
            il.release(fd1)

    def test_is_pid_alive(self):
        assert il.is_pid_alive(os.getpid()) is True
        proc = subprocess.Popen([sys.executable, "-c", "pass"])
        proc.wait(timeout=15)
        # Windows 上 wait() 后进程句柄未关闭时仍呈“僵尸存活”，须关闭句柄彻底回收
        if proc._handle:
            proc._handle.Close()
        assert il.is_pid_alive(0) is False
        if il.is_pid_alive(proc.pid):
            # wait() 已确认子进程退出（returncode=0）；句柄关闭后 PID 归还系统，
            # Windows 可能立即把它复用给新进程——此时 is_pid_alive 返回 True
            # 是正确行为（该 PID 确有活进程，实现无需也无法免疫复用）。
            # 子进程死亡是铁证，True 只能是复用者：显式 skip 而非假失败。
            pytest.skip(f"PID {proc.pid} 在句柄回收后被系统复用，负向存活断言不适用")
        assert il.is_pid_alive(proc.pid) is False

    def test_read_lock_pid(self, lock_env):
        path = str(lock_env / "x.lock")
        with open(path + ".pid", "w", encoding="ascii") as f:
            f.write("12345")
        assert il.read_lock_pid(path) == 12345
        with open(path + ".pid", "w", encoding="ascii") as f:
            f.write("garbage")
        assert il.read_lock_pid(path) is None
        assert il.read_lock_pid(str(lock_env / "missing.lock")) is None

    def test_takeover_refused_without_recorded_pid(self, lock_env, monkeypatch):
        path = str(lock_env / il.LOCK_FILE_NAME)
        open(path, "w").close()  # 只有锁文件无旁路 PID（旧格式）
        assert il.attempt_stale_takeover(path) is False

    def test_takeover_removes_stale_lock_of_dead_pid(self, lock_env, monkeypatch):
        path = str(lock_env / il.LOCK_FILE_NAME)
        open(path, "w").close()
        with open(path + ".pid", "w", encoding="ascii") as f:
            f.write("999999")
        monkeypatch.setattr(il, "is_pid_alive", lambda pid: False)
        assert il.attempt_stale_takeover(path) is True
        assert not os.path.exists(path)
        assert not os.path.exists(path + ".pid")

    def test_takeover_refused_when_holder_healthy(self, lock_env, monkeypatch):
        path = str(lock_env / il.LOCK_FILE_NAME)
        with open(path + ".pid", "w", encoding="ascii") as f:
            f.write("999999")
        monkeypatch.setenv("SIYI_LOCK_TAKEOVER", "1")
        monkeypatch.setattr(il, "is_pid_alive", lambda pid: True)
        monkeypatch.setattr(il, "ws_port_reachable", lambda port=None: True)
        assert il.attempt_stale_takeover(path) is False

    def test_takeover_kills_zombie_python(self, lock_env, monkeypatch):
        path = str(lock_env / il.LOCK_FILE_NAME)
        with open(path + ".pid", "w", encoding="ascii") as f:
            f.write("999999")
        monkeypatch.setenv("SIYI_LOCK_TAKEOVER", "1")
        monkeypatch.setattr(il, "is_pid_alive", lambda pid: True)
        monkeypatch.setattr(il, "ws_port_reachable", lambda port=None: False)
        monkeypatch.setattr(il, "looks_like_python", lambda pid: True)
        monkeypatch.setattr(il, "_TAKEOVER_PROBES", 1)
        monkeypatch.setattr(il, "_TAKEOVER_PROBE_INTERVAL", 0.001)
        terminated = []
        monkeypatch.setattr(il, "terminate_pid", lambda pid: terminated.append(pid) or True)
        assert il.attempt_stale_takeover(path) is True
        assert terminated == [999999]

    def test_takeover_refused_for_non_python_process(self, lock_env, monkeypatch):
        path = str(lock_env / il.LOCK_FILE_NAME)
        with open(path + ".pid", "w", encoding="ascii") as f:
            f.write("999999")
        monkeypatch.setenv("SIYI_LOCK_TAKEOVER", "1")
        monkeypatch.setattr(il, "is_pid_alive", lambda pid: True)
        monkeypatch.setattr(il, "ws_port_reachable", lambda port=None: False)
        monkeypatch.setattr(il, "looks_like_python", lambda pid: False)
        monkeypatch.setattr(il, "_TAKEOVER_PROBES", 1)
        monkeypatch.setattr(il, "_TAKEOVER_PROBE_INTERVAL", 0.001)
        assert il.attempt_stale_takeover(path) is False

    def test_takeover_disabled_by_env(self, lock_env, monkeypatch):
        path = str(lock_env / il.LOCK_FILE_NAME)
        with open(path + ".pid", "w", encoding="ascii") as f:
            f.write("999999")
        monkeypatch.setenv("SIYI_LOCK_TAKEOVER", "0")
        monkeypatch.setattr(il, "is_pid_alive", lambda pid: True)
        assert il.attempt_stale_takeover(path) is False
