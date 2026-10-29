"""持久化 Python Worker 子进程 — 有状态的代码执行。

架构：
  Parent (CodeAgent)           Child (Worker 子进程)
  ┌─────────────────┐         ┌──────────────────────┐
  │ WorkerCodeExecutor │──JSON→│ exec(code, _globals)  │
  │  stdin/stdout     │←JSON──│ 状态保持，import一次  │
  └─────────────────┘         └──────────────────────┘

同一个 _globals 字典跨多次 exec 复用，变量/import 都持续累积。
"""
from __future__ import annotations

import json
import logging
import os
import queue
import subprocess
import sys
import threading
import time
from typing import Any

logger = logging.getLogger("siyi.assistant.code_worker")


# ═══════════════════════════════════════════════════════════
# Worker 进程入口（python -m assistant.code_worker）
# ═══════════════════════════════════════════════════════════

def _build_restricted_globals() -> dict[str, Any]:
    """构建受限全局命名空间（Worker 进程内调用一次，持续复用）。"""
    import builtins as _b

    safe_names = {
        "print", "range", "int", "float", "str", "bool",
        "list", "dict", "set", "tuple", "frozenset",
        "len", "min", "max", "sum", "abs", "sorted",
        "reversed", "enumerate", "zip", "map", "filter",
        "any", "all", "isinstance", "issubclass", "type",
        "hasattr", "getattr", "setattr", "delattr",
        "format", "chr", "ord", "hex", "oct", "bin", "repr",
        "True", "False", "None",
        "Exception", "ValueError", "TypeError", "KeyError",
        "IndexError", "StopIteration", "RuntimeError",
        "AttributeError", "NameError", "FileNotFoundError",
        "PermissionError", "OSError",
        "bytes", "bytearray", "memoryview", "slice",
        "iter", "next", "property", "staticmethod",
        "classmethod", "super", "object", "callable",
        "id", "round", "pow", "divmod", "hash", "dir", "vars",
    }

    safe_builtins = {n: getattr(_b, n) for n in safe_names if hasattr(_b, n)}

    output_buf: list[str] = []

    def _sb_print(*args: Any, sep: str = " ", end: str = "\n") -> None:
        output_buf.append(sep.join(str(a) for a in args) + end)

    globals_dict: dict[str, Any] = {
        "__builtins__": safe_builtins,
        "__name__": "__sandbox__",
        "print": _sb_print,
    }

    # 预加载白名单模块
    allowed_modules = {
        "pathlib", "glob", "json", "csv", "re", "textwrap", "string",
        "datetime", "math", "random", "collections", "itertools", "typing",
        "hashlib", "base64", "io", "tempfile", "functools", "operator", "pprint",
        "copy",  # 常用
        "requests",
    }
    for mod_name in allowed_modules:
        try:
            globals_dict[mod_name] = __import__(mod_name)
        except ImportError:
            pass

    # 安全的 __import__
    _real_import = safe_builtins.get("__import__", _b.__import__)

    def _safe_import(name: str, *args: Any, **kwargs: Any) -> Any:
        if name not in allowed_modules:
            raise ImportError(f"模块 '{name}' 不在白名单中")
        return _real_import(name, *args, **kwargs)

    safe_builtins["__import__"] = _safe_import

    # 安全的 subprocess
    _allowed_cmds = frozenset({
        "start", "explorer", "notepad", "calc", "mspaint",
        "echo", "dir", "type", "where", "findstr", "more",
        "whoami", "hostname", "ver", "systeminfo", "tasklist",
        "python", "pip", "git", "code", "code-insiders",
    })

    def _safe_run(cmd, *args, **kwargs):
        import subprocess as _sp
        cmd_list = list(cmd) if not isinstance(cmd, str) else cmd.split()
        cmd_name = cmd_list[0].lower().split(os.sep)[-1].split("/")[-1]
        if cmd_name not in _allowed_cmds:
            raise ValueError(f"命令 '{cmd_name}' 不在白名单中")
        kwargs.pop("shell", None)
        return _sp.run(cmd_list, *args, **kwargs)

    class _RestrictedSP:
        run = staticmethod(_safe_run)

    globals_dict["subprocess"] = _RestrictedSP()

    # 受限的 os
    import os as _real_os

    class _RestrictedOS:
        name = _real_os.name
        sep = _real_os.sep
        pathsep = _real_os.pathsep
        linesep = _real_os.linesep
        curdir = _real_os.curdir
        pardir = _real_os.pardir
        environ = dict(_real_os.environ)
        getcwd = staticmethod(_real_os.getcwd)
        listdir = staticmethod(_real_os.listdir)
        stat = staticmethod(_real_os.stat)
        walk = staticmethod(_real_os.walk)
        makedirs = staticmethod(_real_os.makedirs)
        startfile = staticmethod(_real_os.startfile)
        remove = staticmethod(_real_os.remove)
        rename = staticmethod(_real_os.rename)
        rmdir = staticmethod(_real_os.rmdir)
        path = _real_os.path

    globals_dict["os"] = _RestrictedOS()

    return globals_dict, output_buf


def _execute_in_globals(code: str, globals_dict: dict, output_buf: list[str]) -> dict:
    """在持久化全局命名空间中执行代码，返回结果 dict。"""
    output_buf.clear()
    error: str | None = None
    start = time.perf_counter()

    # P0/C3: 预检危险代码模式（审查修复：统一从 code_executor 导入，消除重复定义）
    if _is_dangerous_code(code):
        return {
            "output": "",
            "error": "代码包含被禁止的操作（ctypes/socket/subprocess/eval/exec 等）",
            "duration_ms": 0,
        }

    try:
        exec(code, globals_dict)
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"

    duration_ms = (time.perf_counter() - start) * 1000
    return {
        "output": "".join(output_buf),
        "error": error,
        "duration_ms": round(duration_ms, 1),
    }


# 审查修复：从 code_executor 导入 _is_dangerous_code，消除重复定义和错误消息漂移
from assistant.code_executor import _is_dangerous_code


if __name__ == "__main__":
    """Worker REPL 主循环：从 stdin 读 JSON → 执行 → stdout 写 JSON。"""

    _globals, _output_buf = _build_restricted_globals()

    for _line in sys.stdin:
        _line = _line.strip()
        if not _line:
            continue
        try:
            _msg = json.loads(_line)
        except json.JSONDecodeError:
            continue

        _msg_type = _msg.get("type", "")

        if _msg_type == "ping":
            _result = {"type": "pong", "id": _msg.get("id", 0)}
        elif _msg_type == "exec":
            _code = _msg.get("code", "")
            _msg_id = _msg.get("id", 0)
            _exec_result = _execute_in_globals(_code, _globals, _output_buf)
            _result = {
                "type": "result",
                "id": _msg_id,
                **_exec_result,
            }
        elif _msg_type == "shutdown":
            break
        else:
            continue

        sys.__stdout__.write(json.dumps(_result, ensure_ascii=False) + "\n")
        sys.__stdout__.flush()


# ═══════════════════════════════════════════════════════════
# WorkerCodeExecutor — 父进程端管理器
# ═══════════════════════════════════════════════════════════

class WorkerCodeExecutor:
    """持久化 Worker 子进程管理器。

    用法::

        worker = WorkerCodeExecutor(timeout=30)
        r1 = worker.execute("x = 42; print(x)")   # → "42"
        r2 = worker.execute("print(x + 1)")        # → "43"（状态保持！）
        worker.close()
    """

    def __init__(self, timeout: int = 30, project_root: str | None = None):
        self._timeout = timeout
        self._project_root = project_root or self._find_project_root()
        self._process: subprocess.Popen | None = None
        self._next_id = 0
        self._lock = threading.Lock()
        self._result_queue: queue.Queue = queue.Queue()
        self._reader_thread: threading.Thread | None = None
        self._closed = False
        self._start()

    @staticmethod
    def _find_project_root() -> str:
        # 本模块可能在顶层（assistant/code_worker.py）或子包
        # （assistant/bridging/code_worker.py）。两种情况下都要找到
        # 思忆集app/（assistant/ 的父目录）以支持 `python -m assistant.code_worker`。
        here = os.path.dirname(os.path.abspath(__file__))
        if os.path.basename(here) == "assistant":
            return os.path.dirname(here)
        # bridging/ 下：上一级是 assistant/
        return os.path.dirname(os.path.dirname(here))

    # ── 进程生命周期 ──

    def _start(self) -> None:
        """启动 Worker 子进程。"""
        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"
        pythonpath = env.get("PYTHONPATH", "")
        paths = [self._project_root]
        if pythonpath:
            paths.extend(pythonpath.split(os.pathsep))
        env["PYTHONPATH"] = os.pathsep.join(paths)

        try:
            self._process = subprocess.Popen(
                [sys.executable, "-u", "-m", "assistant.bridging.code_worker"],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=env,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
            )
        except Exception as exc:
            logger.error("Worker 进程启动失败: %s", exc)
            raise

        self._start_reader()
        logger.debug("Worker 子进程已启动 (pid=%d)", self._process.pid)

    def _start_reader(self) -> None:
        """启动 stdout 读取线程。"""
        self._result_queue = queue.Queue()

        def _reader() -> None:
            assert self._process is not None
            assert self._process.stdout is not None
            try:
                for line in self._process.stdout:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        msg = json.loads(line)
                        self._result_queue.put(msg)
                    except json.JSONDecodeError:
                        logger.debug("Worker 输出非 JSON: %.60s", line)
            except ValueError:
                pass  # 管道关闭时的正常异常

        self._reader_thread = threading.Thread(target=_reader, daemon=True)
        self._reader_thread.start()

    def _is_alive(self) -> bool:
        """Worker 进程是否存活。"""
        return self._process is not None and self._process.poll() is None

    def _ensure_alive(self) -> None:
        """确保 Worker 存活，必要时重启。"""
        if self._closed:
            raise RuntimeError("WorkerCodeExecutor 已关闭")
        if not self._is_alive():
            logger.warning("Worker 进程已退出（code=%d），重启中", self._process.poll() if self._process else -1)
            self._start()

    def _restart(self) -> None:
        """强制重启 Worker。"""
        self._kill()
        self._start()

    def _kill(self) -> None:
        """终止 Worker 进程。"""
        if self._process and self._process.poll() is None:
            try:
                self._process.kill()
                self._process.wait(timeout=5)
            except Exception:
                pass
        self._process = None

    # ── 核心执行 ──

    def execute(self, code: str) -> dict[str, Any]:
        """向 Worker 发送代码并等待结果。

        Args:
            code: 要执行的 Python 代码

        Returns:
            {"output": str, "error": str|None, "duration_ms": float, "timed_out": bool}
        """
        with self._lock:
            self._ensure_alive()

            msg_id = self._next_id
            self._next_id += 1
            request = json.dumps({"type": "exec", "code": code, "id": msg_id})

            try:
                assert self._process is not None
                assert self._process.stdin is not None
                self._process.stdin.write(request + "\n")
                self._process.stdin.flush()
            except (BrokenPipeError, OSError):
                logger.warning("Worker 管道断开，重启后重试")
                self._restart()
                assert self._process is not None
                assert self._process.stdin is not None
                self._process.stdin.write(request + "\n")
                self._process.stdin.flush()

            return self._wait_result(msg_id)

    def _wait_result(self, expected_id: int) -> dict[str, Any]:
        """等待匹配的结果消息（带超时）。"""
        deadline = time.monotonic() + self._timeout

        while time.monotonic() < deadline:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            try:
                msg = self._result_queue.get(timeout=min(remaining, 0.1))
            except queue.Empty:
                continue

            if msg.get("type") == "result" and msg.get("id") == expected_id:
                return {
                    "output": msg.get("output", ""),
                    "error": msg.get("error"),
                    "duration_ms": msg.get("duration_ms", 0),
                    "timed_out": False,
                }

        # 超时：杀死 Worker 并重启
        logger.warning("Worker 执行超时 (%ds)，强制重启", self._timeout)
        self._restart()
        return {
            "output": "",
            "error": f"执行超时（{self._timeout}s）",
            "duration_ms": self._timeout * 1000,
            "timed_out": True,
        }

    # ── 健康检查 ──

    def ping(self, timeout: float = 3.0) -> bool:
        """检查 Worker 是否存活。"""
        with self._lock:
            if not self._is_alive():
                return False
            try:
                msg_id = self._next_id
                self._next_id += 1
                assert self._process is not None
                assert self._process.stdin is not None
                self._process.stdin.write(json.dumps({"type": "ping", "id": msg_id}) + "\n")
                self._process.stdin.flush()

                deadline = time.monotonic() + timeout
                while time.monotonic() < deadline:
                    try:
                        msg = self._result_queue.get(timeout=0.3)
                    except queue.Empty:
                        continue
                    if msg.get("type") == "pong" and msg.get("id") == msg_id:
                        return True
                return False
            except Exception:
                return False

    # ── 清理 ──

    def close(self) -> None:
        """关闭 Worker。"""
        self._closed = True
        try:
            if self._process and self._process.poll() is None:
                try:
                    assert self._process.stdin is not None
                    self._process.stdin.write(json.dumps({"type": "shutdown"}) + "\n")
                    self._process.stdin.flush()
                except Exception:
                    pass
                self._process.wait(timeout=3)
        except Exception:
            pass
        self._kill()

    def __del__(self):
        self.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
