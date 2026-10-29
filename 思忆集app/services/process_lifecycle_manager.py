"""ProcessLifecycleManager — 追踪由动态工具启动的外部进程。

提供健康检查、stdout/stderr 捕获、进程终止等能力，
主要服务于 detach 模式启动的 GUI/长期运行工具（如 zcode）。
"""
from __future__ import annotations

import logging
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger("siyi.services.process_lifecycle")

_MAX_OUTPUT_LINES = 200
_HEALTH_CHECK_INTERVAL_SECONDS = 30


@dataclass
class TrackedProcess:
    """一个被追踪的进程快照。"""

    process_id: str
    pid: int
    action_id: str
    command: list[str]
    started_at: str
    stdout: deque[str] = field(default_factory=lambda: deque(maxlen=_MAX_OUTPUT_LINES))
    stderr: deque[str] = field(default_factory=lambda: deque(maxlen=_MAX_OUTPUT_LINES))
    last_health_check: str = ""
    alive: bool = True
    returncode: int | None = None
    terminated_by_user: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "process_id": self.process_id,
            "pid": self.pid,
            "action_id": self.action_id,
            "command": self.command,
            "started_at": self.started_at,
            "last_health_check": self.last_health_check,
            "alive": self.alive,
            "returncode": self.returncode,
            "terminated_by_user": self.terminated_by_user,
            "stdout_line_count": len(self.stdout),
            "stderr_line_count": len(self.stderr),
        }


class ProcessLifecycleManager:
    """管理动态工具启动的进程生命周期。"""

    def __init__(self):
        self._processes: dict[str, TrackedProcess] = {}
        self._procs: dict[str, Any] = {}
        self._lock = threading.RLock()
        self._reader_threads: list[threading.Thread] = []
        self._shutdown = False
        self._health_thread = threading.Thread(
            target=self._health_check_loop, daemon=True, name="process-health"
        )
        self._health_thread.start()

    def register_process(
        self,
        process_id: str,
        pid: int,
        action_id: str,
        command: list[str],
        proc: Any,
        capture_output: bool = True,
    ) -> TrackedProcess:
        """注册一个已启动的进程并开始追踪。"""
        tracked = TrackedProcess(
            process_id=process_id,
            pid=pid,
            action_id=action_id,
            command=list(command),
            started_at=datetime.now(timezone.utc).isoformat(),
        )
        with self._lock:
            self._processes[process_id] = tracked
            self._procs[process_id] = proc

        if capture_output:
            self._start_output_readers(process_id, proc, tracked)

        logger.info("Registered process %s (pid=%s, action=%s)", process_id, pid, action_id)
        return tracked

    def list_processes(self, alive_only: bool = False) -> list[dict[str, Any]]:
        """列出所有被追踪的进程。"""
        with self._lock:
            result = []
            for tracked in self._processes.values():
                if alive_only and not tracked.alive:
                    continue
                result.append(tracked.to_dict())
            return result

    def get_status(self, process_id: str) -> dict[str, Any] | None:
        """获取单个进程的详细状态与最近输出。"""
        with self._lock:
            tracked = self._processes.get(process_id)
            if tracked is None:
                return None
            self._update_alive_locked(tracked)
            snapshot = tracked.to_dict()
            snapshot["stdout"] = list(tracked.stdout)
            snapshot["stderr"] = list(tracked.stderr)
            return snapshot

    def get_recent_output(
        self, process_id: str, stream: str = "stdout", limit: int = 50
    ) -> list[str]:
        """获取指定进程的最近输出行。"""
        with self._lock:
            tracked = self._processes.get(process_id)
            if tracked is None:
                return []
            buffer = tracked.stdout if stream == "stdout" else tracked.stderr
            return list(buffer)[-limit:]

    def terminate(self, process_id: str) -> dict[str, Any]:
        """终止指定进程。"""
        with self._lock:
            tracked = self._processes.get(process_id)
            proc = self._procs.get(process_id)
            if tracked is None:
                return {"success": False, "error": "process not found"}

            tracked.terminated_by_user = True
            self._update_alive_locked(tracked)
            if not tracked.alive:
                return {"success": True, "process_id": process_id, "already_terminated": True}

            killed = self._kill_process(proc)
            self._update_alive_locked(tracked)
            tracked.returncode = proc.returncode if proc is not None else None
            return {
                "success": killed,
                "process_id": process_id,
                "pid": tracked.pid,
                "alive": tracked.alive,
            }

    def health_check(self, process_id: str) -> dict[str, Any] | None:
        """手动触发一次健康检查。"""
        with self._lock:
            tracked = self._processes.get(process_id)
            if tracked is None:
                return None
            self._update_alive_locked(tracked)
            tracked.last_health_check = datetime.now(timezone.utc).isoformat()
            return tracked.to_dict()

    def shutdown(self) -> None:
        """关闭管理器：停止健康检查线程，可选终止所有存活进程。"""
        self._shutdown = True
        with self._lock:
            for process_id, tracked in list(self._processes.items()):
                if tracked.alive:
                    proc = self._procs.get(process_id)
                    self._kill_process(proc)
                    tracked.terminated_by_user = True
                    self._update_alive_locked(tracked)

    # ------------------------------------------------------------------
    # 内部实现
    # ------------------------------------------------------------------

    def _start_output_readers(self, process_id: str, proc: Any, tracked: TrackedProcess) -> None:
        """启动后台线程读取 stdout/stderr。"""

        def _read_stream(stream, buffer: deque[str], name: str):
            try:
                if stream is None:
                    return
                if hasattr(stream, "readline"):
                    for line in iter(stream.readline, b""):
                        if not line:
                            break
                        text = line.decode("utf-8", errors="replace").rstrip()
                        if text:
                            buffer.append(text)
                elif hasattr(stream, "read"):
                    data = stream.read()
                    if data:
                        text = data.decode("utf-8", errors="replace")
                        for line in text.splitlines():
                            if line:
                                buffer.append(line)
            except Exception:
                logger.debug("process %s %s reader exited", process_id, name, exc_info=True)

        stdout = getattr(proc, "stdout", None)
        stderr = getattr(proc, "stderr", None)
        if stdout is not None:
            t = threading.Thread(
                target=_read_stream,
                args=(stdout, tracked.stdout, "stdout"),
                daemon=True,
                name=f"proc-{process_id}-stdout",
            )
            t.start()
            self._reader_threads.append(t)
        if stderr is not None:
            t = threading.Thread(
                target=_read_stream,
                args=(stderr, tracked.stderr, "stderr"),
                daemon=True,
                name=f"proc-{process_id}-stderr",
            )
            t.start()
            self._reader_threads.append(t)

    def _health_check_loop(self) -> None:
        while not self._shutdown:
            try:
                self._run_health_check_round()
            except Exception:
                logger.debug("process health check round failed", exc_info=True)
            time.sleep(_HEALTH_CHECK_INTERVAL_SECONDS)

    def _run_health_check_round(self) -> None:
        with self._lock:
            for tracked in self._processes.values():
                self._update_alive_locked(tracked)
                tracked.last_health_check = datetime.now(timezone.utc).isoformat()

    def _update_alive_locked(self, tracked: TrackedProcess) -> None:
        proc = self._procs.get(tracked.process_id)
        if proc is None:
            tracked.alive = False
            return
        try:
            if hasattr(proc, "poll"):
                returncode = proc.poll()
                tracked.alive = returncode is None
                if returncode is not None:
                    tracked.returncode = returncode
            elif hasattr(proc, "returncode"):
                tracked.alive = proc.returncode is None
                if proc.returncode is not None:
                    tracked.returncode = proc.returncode
            else:
                tracked.alive = False
        except Exception:
            tracked.alive = False

    def _kill_process(self, proc: Any) -> bool:
        if proc is None:
            return False
        try:
            if hasattr(proc, "kill"):
                proc.kill()
                return True
            if hasattr(proc, "terminate"):
                proc.terminate()
                return True
        except Exception:
            logger.warning("failed to kill process", exc_info=True)
        return False
