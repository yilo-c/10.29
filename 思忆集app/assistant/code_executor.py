"""安全沙箱 Python 代码执行器。

思忆集 Code-as-Action 模式的核心：
AI 写 Python 代码来完成任务，而非调用预定义工具目录。
exec() 在受限命名空间中运行，保证安全性。
"""
from __future__ import annotations

import builtins
import io
import logging
import os as _os_mod
import re
import sys
import threading
import time
from typing import Any

logger = logging.getLogger("siyi.assistant.code_executor")

# ── 白名单配置 ──────────────────────────────────────────

ALLOWED_MODULES: frozenset[str] = frozenset({
    # 文件系统
    "pathlib", "glob",
    # 数据处理
    "json", "csv", "re", "textwrap", "string",
    "datetime", "math", "random", "collections", "itertools", "typing",
    "hashlib", "base64", "io", "tempfile",
    # 网络 (只读)
    "requests",
    # 实用工具
    "functools", "operator", "pprint",
})

ALLOWED_SUBPROCESS_ARGS: frozenset[str] = frozenset({
    # Windows 安全命令
    "start", "explorer", "notepad", "calc", "mspaint",
    "echo", "dir", "type", "where", "findstr", "more",
    "whoami", "hostname", "ver", "systeminfo", "tasklist",
    # 跨平台安全命令
    "python", "pip", "git", "code", "code-insiders",
})

# 被禁止的 builtins 名称——即使被 LLM 间接调用也不行
_FORBIDDEN_BUILTINS: frozenset[str] = frozenset({
    "eval", "exec", "compile", "__import__", "open",
    "breakpoint", "input",
})

# 安全 builtins — 只暴露读+计算能力
_SAFE_BUILTIN_NAMES: frozenset[str] = frozenset({
    "print", "range", "int", "float", "str", "bool",
    "list", "dict", "set", "tuple", "frozenset",
    "len", "min", "max", "sum", "abs", "sorted",
    "reversed", "enumerate", "zip", "map", "filter",
    "any", "all", "isinstance", "issubclass", "type",
    "hasattr", "getattr", "setattr", "delattr",
    "format", "chr", "ord", "hex", "oct", "bin", "repr",
    "True", "False", "None", "Ellipsis",
    "Exception", "ValueError", "TypeError", "KeyError",
    "IndexError", "StopIteration", "RuntimeError",
    "AttributeError", "NameError", "FileNotFoundError",
    "PermissionError", "OSError", "ModuleNotFoundError",
    "bytes", "bytearray", "memoryview", "slice",
    "iter", "next", "property", "staticmethod",
    "classmethod", "super", "object", "callable",
    "id", "round", "pow", "divmod", "hash", "dir",
    "vars", "help", "issubclass",
})


def _build_safe_builtins() -> dict[str, Any]:
    safe = {}
    for name in _SAFE_BUILTIN_NAMES:
        if hasattr(builtins, name):
            safe[name] = getattr(builtins, name)
    return safe


def _is_dangerous_code(text: str) -> bool:
    """快速检测明显危险的代码模式。

    P0/C3: 扩展检测模式，覆盖更多沙箱逃逸向量。
    注意：这是第一道防线，exec() 在受限命名空间中运行是第二道防线。
    requests 在 ALLOWED_MODULES 中，不在此检测；compile/eval/exec 已在
    _FORBIDDEN_BUILTINS 中过滤，此处作为双保险。
    """
    dangerous_patterns = [
        # 原始：外部库逃逸
        r"\bctypes\b",
        r"\bsocket\b",
        r"\bpty\b",
        r"\bwin32api\b",
        r"\bpywin32\b",
        # P0/C3: 子进程直接调用（绕过受限 subprocess）
        r"\bsubprocess\b",
        r"\bos\.system\b",
        r"\bos\.popen\b",
        r"\bos\.exec[lv]p?e?\b",
        r"\bos\.spawn[lv]p?e?\b",
        # P0/C3: 动态导入绕过白名单
        r"\b__import__\s*\(",
        r"\bimportlib\b",
        # P0/C3: 内置函数逃逸
        r"\bbuiltins\b",
        # P0/C3: 动态代码执行（双保险，已在 _FORBIDDEN_BUILTINS 过滤）
        r"\bcompile\s*\(",
        r"\beval\s*\(",
        r"\bexec\s*\(",
        # P0/C3: 文件系统底层操作（绕过受限 os）
        r"\bos\.remove\b",
        r"\bos\.unlink\b",
        r"\bos\.rmdir\b",
        r"\bshutil\.rmtree\b",
        # P0/C3: 网络底层（requests 允许，但底层 http/urllib 不允许）
        r"\bhttp\.client\b",
        r"\burllib\b",
    ]
    return any(re.search(p, text) for p in dangerous_patterns)


def _sanitize_command_args(args: list[str]) -> list[str]:
    """检查子进程参数是否在白名单中。"""
    if not args:
        return args
    cmd = args[0].lower().split(_os_mod.sep)[-1].split("/")[-1]
    if cmd not in ALLOWED_SUBPROCESS_ARGS:
        raise ValueError(
            f"命令 '{cmd}' 不在白名单中。允许: {sorted(ALLOWED_SUBPROCESS_ARGS)}"
        )
    return args


class CodeResult:
    """代码执行结果。"""

    def __init__(
        self,
        output: str = "",
        error: str | None = None,
        timed_out: bool = False,
        duration_ms: float = 0,
    ):
        self.output = output
        self.error = error
        self.timed_out = timed_out
        self.duration_ms = duration_ms

    @property
    def success(self) -> bool:
        return self.error is None and not self.timed_out

    def to_dict(self) -> dict[str, Any]:
        return {
            "output": self.output,
            "error": self.error,
            "timed_out": self.timed_out,
            "duration_ms": self.duration_ms,
            "success": self.success,
        }

    def format_for_llm(self) -> str:
        """格式化为 LLM 可读的文本（类似 tool result）。"""
        parts = []
        if self.timed_out:
            parts.append("[执行超时]")
        if self.error:
            parts.append(f"[执行错误]\n{self.error}")
        if self.output:
            if len(self.output) > 4000:
                parts.append(self.output[:4000] + "\n\n... [输出已截断]")
            else:
                parts.append(self.output)
        if not parts:
            parts.append("(无输出)")
        text = "\n".join(parts)
        if self.duration_ms > 100:
            text += f"\n\n[执行耗时: {self.duration_ms:.0f}ms]"
        return text


class CodeExecutor:
    """安全沙箱代码执行器。

    用法::

        exe = CodeExecutor(timeout=30)
        result = exe.execute('''print("hello")''')
        print(result.format_for_llm())
    """

    def __init__(self, timeout: int = 30):
        self._timeout = timeout
        self._audit_lock = threading.Lock()
        self._audit_log: list[dict[str, Any]] = []
        self._preloaded: dict[str, Any] = {}
        self._load_modules()

    def _load_modules(self) -> None:
        for name in sorted(ALLOWED_MODULES):
            try:
                self._preloaded[name] = __import__(name)
            except ImportError:
                logger.debug("模块不可用（不影响运行）: %s", name)

    @property
    def audit_log(self) -> list[dict[str, Any]]:
        with self._audit_lock:
            return list(self._audit_log)

    # ── 安全的 subprocess 包装 ───────────────────────────

    @staticmethod
    def _safe_popen(*args, **kwargs):
        """受限制的 subprocess.Popen。——使用 subprocess 代替。"""
        import subprocess as _sp
        cmd = kwargs.pop("cmd", None) or (args[0] if args else None)
        if cmd is None:
            raise ValueError("缺少命令")
        if isinstance(cmd, str):
            cmd_list = cmd.split()
        else:
            cmd_list = list(cmd)
        _sanitize_command_args(cmd_list)
        return _sp.Popen(cmd_list, *args[1:], **kwargs)

    @staticmethod
    def _safe_subprocess_run(cmd, *args, **kwargs):
        """受限制的 subprocess.run。"""
        import subprocess as _sp
        if isinstance(cmd, str):
            cmd_list = cmd.split()
        else:
            cmd_list = list(cmd)
        _sanitize_command_args(cmd_list)
        kwargs.pop("shell", None)  # 禁止 shell=True
        return _sp.run(cmd_list, *args, **kwargs)

    def _make_restricted_subprocess(self) -> object:
        """创建一个安全的 subprocess 模块替身。"""
        class RestrictedSubprocess:
            run = staticmethod(self._safe_subprocess_run)
            Popen = staticmethod(self._safe_popen)
            CalledProcessError = None
            TimeoutExpired = None

        try:
            import subprocess as _sp
            RestrictedSubprocess.CalledProcessError = _sp.CalledProcessError
            RestrictedSubprocess.TimeoutExpired = _sp.TimeoutExpired
        except ImportError:
            pass
        return RestrictedSubprocess()

    def _make_restricted_os(self) -> object:
        """创建一个受限制的 os 模块替身。

        允许路径操作、环境变量读取、文件信息查询。
        禁止：os.system, os.popen, os.remove, os.unlink, os.rmdir, os.chmod 等。
        """
        try:
            import os as _real_os
        except ImportError:
            return None

        class RestrictedOS:
            # 允许
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

            # path 模块
            path = _real_os.path

            # 大小写不敏感的路径比较（Windows）
            if hasattr(_real_os, "path"):
                path = _real_os.path

        return RestrictedOS()

    # ── 核心执行 ─────────────────────────────────────────

    def execute(
        self,
        code: str,
        context: dict[str, Any] | None = None,
    ) -> CodeResult:
        """在受限沙箱中执行 Python 代码。

        参数:
            code: 要执行的 Python 代码
            context: 额外注入到命名空间的变量

        返回:
            CodeResult 包含 stdout 输出、错误和超时状态
        """
        start = time.perf_counter()

        # 快速安全扫描
        if _is_dangerous_code(code):
            return CodeResult(
                error="代码包含被禁止的操作（ctypes/socket/subprocess/eval/exec 等）",
                duration_ms=(time.perf_counter() - start) * 1000,
            )

        # 构建安全命名空间
        safe_builtins = _build_safe_builtins()

        safe_globals: dict[str, Any] = {
            "__builtins__": safe_builtins,
            "__name__": "__sandbox__",
            "subprocess": self._make_restricted_subprocess(),
            "os": self._make_restricted_os(),
        }

        # 注入预加载模块 + 额外上下文
        for name, mod in self._preloaded.items():
            safe_globals[name] = mod
        if context:
            safe_globals.update(context)

        # 自定义安全的 __import__
        def safe_import(name, *args, **kwargs):
            if name not in ALLOWED_MODULES:
                raise ImportError(
                    f"模块 '{name}' 不在白名单中。允许: {sorted(ALLOWED_MODULES)}"
                )
            return self._preloaded.get(name) or __import__(name)

        safe_builtins["__import__"] = safe_import

        fake_stdout = io.StringIO()
        error: Exception | None = None
        timed_out = False

        def run() -> None:
            nonlocal error
            old_stdout = sys.stdout
            try:
                sys.stdout = fake_stdout
                exec(code, safe_globals)
            except Exception as exc:
                error = exc
            finally:
                sys.stdout = old_stdout

        thread = threading.Thread(target=run, daemon=True)
        thread.start()
        thread.join(timeout=self._timeout)

        if thread.is_alive():
            timed_out = True

        duration_ms = (time.perf_counter() - start) * 1000
        output = fake_stdout.getvalue()

        result = CodeResult(
            output=output,
            error=str(error) if error else None,
            timed_out=timed_out,
            duration_ms=duration_ms,
        )

        # 审计日志
        with self._audit_lock:
            self._audit_log.append({
                "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
                "code_preview": code[:120],
                "success": result.success,
                "error": result.error,
                "timed_out": timed_out,
                "duration_ms": duration_ms,
                "output_size": len(output),
            })

        logger.info(
            "代码执行 %s: %.1fms output=%d chars",
            "✓" if result.success else "✗",
            duration_ms, len(output),
        )
        return result


__all__ = ["CodeExecutor", "CodeResult", "ALLOWED_MODULES", "ALLOWED_SUBPROCESS_ARGS"]
