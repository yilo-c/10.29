"""Phase 2 子进程隔离：JSON-RPC over stdio。

主进程把低信任插件放进独立进程运行，经 stdin/stdout 的换行分隔 JSON-RPC
通信，实现崩溃隔离（插件崩溃不影响主进程）。

相比 Phase 1 的进程内 ``sandboxed_execute``，子进程隔离额外提供：
  - 崩溃自愈：子进程意外退出后自动重启（restart_on_crash）
  - 孤儿回收：close() / 解释器退出时确保子进程被回收（atexit + __del__）
  - 进程存活检测：每次 RPC 前探测，死亡则重建
  - 权限透传：execute 时可携带 required_permissions，由子进程内权限管理器校验
  - 资源配额：Linux/macOS 子进程内经 ``resource.setrlimit`` 设 CPU/内存上限；
    Windows 主进程侧经 Job Object（ctypes，无需 pywin32）为子进程设同样上限

协议（每行一条 JSON，UTF-8）：
  请求: {"id": <int>, "method": "ping"|"execute", "params": {...}}
  响应: {"id": <int>, "ok": true, "result": <any>}
        {"id": <int>, "ok": false, "error": "<str>"}

execute.params:
  {"entry_point": "module:Class", "call_params": {...},
   "required_permissions": ["network:api.x"], "plugin_id": "com.siyi.x"}
"""

from __future__ import annotations

import atexit
import json
import logging
import os
import subprocess
import sys
import threading
from typing import Any

logger = logging.getLogger("siyi.plugin.subprocess_host")

DEFAULT_WORKER_TIMEOUT = 30
STARTUP_TIMEOUT = 10.0
# 子进程软资源上限（Linux/macOS 生效；Windows 仅记录，依赖 OS 容器另行兜底）
DEFAULT_MEMORY_LIMIT_MB = 256


def _import_plugin_class(entry_point: str):
    """entry_point 形如 'plugins.installed.textstats.plugin:TextStatsPlugin'。"""
    module_path, _, class_name = entry_point.partition(":")
    if not module_path or not class_name:
        raise ValueError(f"invalid entry_point: {entry_point}")
    import importlib

    module = importlib.import_module(module_path)
    cls = getattr(module, class_name, None)
    if cls is None:
        raise AttributeError(f"{class_name} not found in {module_path}")
    return cls


def _apply_resource_limits(limit_mb: int) -> None:
    """尽力为子进程设置资源上限（仅 Linux/macOS）。Windows 静默跳过。"""
    if os.name != "posix":
        return
    try:
        import resource  # type: ignore

        bytes_limit = int(limit_mb) * 1024 * 1024
        resource.setrlimit(resource.RLIMIT_AS, (bytes_limit, bytes_limit))
        cpu = int(os.environ.get("SIYI_SUBPROCESS_CPU_LIMIT", "60"))  # 秒（CPU 时间）
        resource.setrlimit(resource.RLIMIT_CPU, (cpu, cpu))
    except Exception as exc:  # noqa: BLE001 - 资源限制失败不应导致 worker 不可用
        logger.warning("failed to apply resource limits in worker: %s", exc)


def _apply_windows_job_limits(proc: subprocess.Popen) -> None:
    """Windows 下用 Job Object 为子进程设置资源上限（内存 + CPU 时间）。

    ``resource.setrlimit`` 在 win32 为静默 no-op（见 :func:`_apply_resource_limits`），
    故主进程侧改用 Job Object 实现等价约束。以纯 ``ctypes`` 调用 Kernel32，避免引入
    pywin32 依赖。内存上限沿用 ``DEFAULT_MEMORY_LIMIT_MB``（256MB），CPU 时间沿用
    ``SIYI_SUBPROCESS_CPU_LIMIT``（默认 60s，单位 CPU 秒）。

    无弹窗/无继承限制：仅设进程内存与 CPU 时间上限，不限制 UI/KillOnJobClose，
    避免误杀挂起的 worker 或诱发界面异常。任何一步失败仅记 WARNING，不阻断
    worker 启动（与 POSIX 处的尽力而为策略一致）。
    """
    if os.name != "nt" or proc is None:
        return
    try:
        process_handle = int(proc._handle)  # Popen 内部内核句柄
    except Exception:  # noqa: BLE001
        logger.exception("获取子进程内核句柄失败，跳过 Job Object 资源限制")
        return
    try:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

        # JOBOBJECT_BASIC_LIMIT_INFORMATION（Windows 定义的对齐布局）
        class JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
            _fields_ = [
                ("PerProcessUserTimeLimit", wintypes.LARGE_INTEGER),
                ("PerJobUserTimeLimit", wintypes.LARGE_INTEGER),
                ("LimitFlags", wintypes.DWORD),
                ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t),
                ("ActiveProcessLimit", wintypes.DWORD),
                ("Affinity", ctypes.c_size_t),  # ULONG_PTR
                ("PriorityClass", wintypes.DWORD),
                ("SchedulingClass", wintypes.DWORD),
            ]

        class IO_COUNTERS(ctypes.Structure):
            _fields_ = [
                ("ReadOperationCount", ctypes.c_ulonglong),
                ("WriteOperationCount", ctypes.c_ulonglong),
                ("OtherOperationCount", ctypes.c_ulonglong),
                ("ReadTransferCount", ctypes.c_ulonglong),
                ("WriteTransferCount", ctypes.c_ulonglong),
                ("OtherTransferCount", ctypes.c_ulonglong),
            ]

        class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
            _fields_ = [
                ("BasicLimitInformation", JOBOBJECT_BASIC_LIMIT_INFORMATION),
                ("IoInfo", IO_COUNTERS),
                ("ProcessMemoryLimit", ctypes.c_size_t),
                ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t),
                ("PeakJobMemoryUsed", ctypes.c_size_t),
            ]

        JOB_OBJECT_LIMIT_PROCESS_TIME = 0x0002
        JOB_OBJECT_LIMIT_PROCESS_MEMORY = 0x0100
        JOB_OBJECT_LIMIT_JOB_MEMORY = 0x0200
        JobObjectExtendedLimitInformation = 9

        job = kernel32.CreateJobObjectW(None, None)
        if not job:
            err = ctypes.get_last_error()
            logger.warning("CreateJobObjectW failed: %s", err)
            return
        try:
            limit_job_mem_mb = int(os.environ.get("SIYI_SUBPROCESS_MEMORY_LIMIT_MB", str(DEFAULT_MEMORY_LIMIT_MB)))
        except ValueError:
            limit_job_mem_mb = DEFAULT_MEMORY_LIMIT_MB
        try:
            cpu_sec = int(os.environ.get("SIYI_SUBPROCESS_CPU_LIMIT", "60"))
        except ValueError:
            cpu_sec = 60
        # CPU 时间上限转 100ns 单位
        cpu_100ns = cpu_sec * 10_000_000

        info = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
        info.BasicLimitInformation.LimitFlags = (
            JOB_OBJECT_LIMIT_PROCESS_TIME | JOB_OBJECT_LIMIT_PROCESS_MEMORY | JOB_OBJECT_LIMIT_JOB_MEMORY
        )
        info.BasicLimitInformation.PerProcessUserTimeLimit = cpu_100ns
        info.ProcessMemoryLimit = limit_job_mem_mb * 1024 * 1024
        info.JobMemoryLimit = limit_job_mem_mb * 1024 * 1024

        ok = kernel32.SetInformationJobObject(
            job,
            JobObjectExtendedLimitInformation,
            ctypes.byref(info),
            ctypes.sizeof(info),
        )
        if not ok:
            logger.warning("SetInformationJobObject failed: %s", ctypes.get_last_error())
            return
        ok = kernel32.AssignProcessToJobObject(job, ctypes.c_void_p(process_handle))
        if not ok:
            logger.warning("AssignProcessToJobObject failed: %s", ctypes.get_last_error())
    except Exception as exc:  # noqa: BLE001 - 资源配额失败仅降级，不阻断 worker
        logger.warning("failed to apply Windows job limits: %s", exc)


def _worker_handle(method: str, params: dict[str, Any]) -> Any:
    if method == "ping":
        return {"pong": True}
    if method == "execute":
        entry_point = params.get("entry_point", "")
        call_params = params.get("call_params", {}) or {}
        plugin_id = params.get("plugin_id", "")
        required = params.get("required_permissions") or []
        # 子进程内权限校验：与进程内 sandboxed_execute 同一套管理器
        if required:
            try:
                from plugins.permissions import get_permission_manager

                manager = get_permission_manager()
                if manager is not None:
                    for perm in required:
                        manager.require_permission(plugin_id, perm)
            except Exception as exc:  # noqa: BLE001 - 敏感：权限缺失须明确透传给主进程
                raise PermissionError(f"permission denied in subprocess: {exc}")
        cls = _import_plugin_class(entry_point)
        plugin = cls()
        return plugin.execute(call_params)
    raise ValueError(f"unknown method: {method}")


def _run_worker_stdio() -> int:
    """子进程 worker 入口（由 __main__ 调用）。返回退出码。"""
    # 子进程内不再向上抛；任何致命错误仅影响本进程，不污染主进程。
    try:
        _apply_resource_limits(DEFAULT_MEMORY_LIMIT_MB)
    except Exception:  # noqa: BLE001
        pass
    for raw in sys.stdin:
        raw = raw.strip()
        if not raw:
            continue
        try:
            req = json.loads(raw)
        except json.JSONDecodeError as exc:
            sys.stdout.write(json.dumps({"id": None, "ok": False, "error": f"bad json: {exc}"}) + "\n")
            sys.stdout.flush()
            continue
        req_id = req.get("id")
        method = req.get("method", "")
        params = req.get("params", {}) or {}
        try:
            result = _worker_handle(method, params)
            out = {"id": req_id, "ok": True, "result": result}
        except Exception as exc:  # noqa: BLE001 - worker 内异常必须回传，不崩溃
            out = {"id": req_id, "ok": False, "error": f"{type(exc).__name__}: {exc}"}
        try:
            # 结果不可序列化（如含 lambda/对象）时，明确回传错误而非让 worker 崩溃
            # （worker 崩溃会让主进程侧表现为挂起/超时的 "静默失败"）。
            line = json.dumps(out, ensure_ascii=False)
        except (TypeError, ValueError) as exc:
            line = json.dumps({
                "id": req_id, "ok": False,
                "error": f"NonSerializableResult: {exc}",
            }, ensure_ascii=False)
        sys.stdout.write(line + "\n")
        sys.stdout.flush()
    return 0


class _Future:
    """极简同步 Future（PoC 用，避免引入额外依赖）。"""

    def __init__(self) -> None:
        self._event = threading.Event()
        self._resp: dict | None = None

    def set(self, resp: dict) -> None:
        self._resp = resp
        self._event.set()

    def wait(self, timeout: float) -> dict:
        if not self._event.wait(timeout):
            raise TimeoutError(f"plugin subprocess call timed out after {timeout}s")
        assert self._resp is not None
        return self._resp


class SubprocessPluginHost:
    """主进程侧的子进程宿主：启动 worker、发送 JSON-RPC、回收进程。

    增强版特性：
      - restart_on_crash：子进程意外退出后按需重建
      - 进程存活检测：每个 call 前 poll，已死则重建
      - 孤儿回收：close() 与解释器退出（atexit）双保险
    """

    def __init__(
        self,
        python: str | None = None,
        startup_timeout: float = STARTUP_TIMEOUT,
        restart_on_crash: bool = True,
    ) -> None:
        self._python = python or sys.executable
        self._startup_timeout = startup_timeout
        self._restart_on_crash = restart_on_crash
        self._proc: subprocess.Popen | None = None
        self._next_id = 1
        self._lock = threading.Lock()
        self._reader_thread: threading.Thread | None = None
        self._pending: dict[int, _Future] = {}
        self._closed = False
        atexit.register(self.close)

    def _spawn(self) -> None:
        # 显式锚定 worker 工作目录到代码库根（思忆集app）：python -m 会把该目录置入
        # sys.path[0]，使 worker 总能 import plugins./services.。不继承父进程运行时 cwd，
        # 以免业务模块初始化（如数据目录单例）chdir 漂移导致 worker 找不到插件模块。
        app_root = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
        self._proc = subprocess.Popen(
            [self._python, "-m", "plugins.subprocess_host", "--worker"],
            cwd=app_root,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            bufsize=1,
        )
        # Windows 下用 Job Object 为子进程设资源配额（POSIX 由 worker 内 setrlimit 承担）
        _apply_windows_job_limits(self._proc)
        self._reader_thread = threading.Thread(target=self._read_loop, daemon=True)
        self._reader_thread.start()
        self._stderr_thread = threading.Thread(target=self._pump_stderr, daemon=True)
        self._stderr_thread.start()
        # 启动握手：ping 确认 worker 就绪
        self.call("ping", {}, timeout=self._startup_timeout)

    def _pump_stderr(self) -> None:
        """转发 worker stderr 到主进程日志，便于诊断子进程崩溃。"""
        assert self._proc is not None and self._proc.stderr is not None
        for line in self._proc.stderr:
            line = line.strip()
            if line:
                logger.warning("[subprocess worker] %s", line)

    def start(self) -> None:
        if self._closed:
            raise RuntimeError("host already closed")
        if self._proc is None:
            self._spawn()

    def _read_loop(self) -> None:
        assert self._proc is not None and self._proc.stdout is not None
        for raw in self._proc.stdout:
            raw = raw.strip()
            if not raw:
                continue
            logger.debug("[subprocess worker ->] %s", raw)
            try:
                resp = json.loads(raw)
            except json.JSONDecodeError as exc:
                logger.warning("non-json from worker: %r (%s)", raw, exc)
                continue
            req_id = resp.get("id")
            fut = self._pending.pop(req_id, None) if req_id is not None else None
            if fut is not None:
                fut.set(resp)

    def _ensure_alive(self) -> None:
        """进程不存在或已死则重建；重建失败时按 restart_on_crash 决定行为。"""
        if self._proc is None or self._proc.poll() is not None:
            if self._proc is not None:
                logger.warning("subprocess worker died (code=%s); restarting", self._proc.returncode)
            if self._restart_on_crash:
                self._spawn()
            else:
                raise RuntimeError("subprocess worker not running")

    def call(self, method: str, params: dict[str, Any], timeout: float = DEFAULT_WORKER_TIMEOUT) -> Any:
        with self._lock:
            if self._closed:
                raise RuntimeError("host already closed")
            self._ensure_alive()
            req_id = self._next_id
            self._next_id += 1
            fut = _Future()
            self._pending[req_id] = fut
            req = json.dumps({"id": req_id, "method": method, "params": params})
            assert self._proc.stdin is not None
            self._proc.stdin.write(req + "\n")
            self._proc.stdin.flush()
        try:
            resp = fut.wait(timeout)
        except TimeoutError:
            # 超时后必须清掉 pending 条目，否则 future 永久滞留、_pending 无限增长
            # （worker 可能一直无响应或已死，残余 future 无人消费）。慢响应后续到达
            # 时 _read_loop 的 pop 会得到 None 并忽略，安全。
            with self._lock:
                self._pending.pop(req_id, None)
            raise TimeoutError(
                f"plugin subprocess call '{method}' timed out after {timeout}s"
            ) from None
        # 若本次调用导致进程崩溃（响应前进程退出），尝试自愈一次重试
        if not resp.get("ok", False):
            self._maybe_rebuild_after_error()
        if "ok" not in resp and "error" not in resp and "result" not in resp:
            # 防御：响应体异常为空，尝试把 worker 剩余输出透出以便诊断
            try:
                if self._proc is not None and self._proc.stdout is not None:
                    self._proc.stdout.flush()
            except Exception:
                pass
            logger.warning("empty worker response for method=%s: %r", method, resp)
        return resp

    def _maybe_rebuild_after_error(self) -> None:
        if self._proc is not None and self._proc.poll() is not None and self._restart_on_crash:
            try:
                self._spawn()
            except Exception:  # noqa: BLE001
                logger.exception("子进程自愈重建失败")

    def execute_plugin(
        self,
        entry_point: str,
        call_params: dict[str, Any],
        timeout: float = DEFAULT_WORKER_TIMEOUT,
        required_permissions: list[str] | None = None,
        plugin_id: str = "",
    ) -> Any:
        params = {
            "entry_point": entry_point,
            "call_params": call_params,
            "required_permissions": required_permissions or [],
            "plugin_id": plugin_id,
        }
        resp = self.call("execute", params, timeout=timeout)
        if not resp.get("ok", False):
            raise RuntimeError(resp.get("error", "unknown worker error"))
        return resp.get("result")

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        proc = self._proc
        self._proc = None
        if proc is None:
            return
        try:
            if proc.stdin is not None:
                proc.stdin.close()
        except Exception:
            logger.exception("关闭子进程 stdin 失败")
        try:
            proc.terminate()
            proc.wait(timeout=5)
        except Exception:
            logger.exception("终止子进程失败，尝试强制 kill")
            try:
                proc.kill()
            except Exception:
                logger.exception("强制终止子进程失败")

    def __del__(self) -> None:
        try:
            self.close()
        except Exception:
            logger.exception("析构时关闭子进程宿主失败")


def main() -> int:
    if "--worker" in sys.argv:
        return _run_worker_stdio()
    # 非 worker 模式：简要自测（仅用于手动验证）
    host = SubprocessPluginHost()
    try:
        host.start()
        print("ping:", host.call("ping", {}))
        print("execute:", host.execute_plugin(
            "plugins.installed.textstats.plugin:TextStatsPlugin",
            {"text": "hello 你好"},
        ))
    finally:
        host.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
