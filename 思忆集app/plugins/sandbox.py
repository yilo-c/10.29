"""Plugin isolation and sandbox.

Phase 1: In-process isolation with error boundaries, execution timeouts,
and resource usage monitoring.

Phase 2: Sub-process isolation via JSON-RPC over stdin/stdout
(implemented; opt-in via ``SIYI_PLUGIN_ISOLATION=subprocess``, see
:func:`choose_isolation_mode`).
"""
from __future__ import annotations

import contextlib
import logging
import os
import threading
from collections.abc import Callable
from typing import Any, TypeVar

logger = logging.getLogger("siyi.plugin.sandbox")

T = TypeVar("T")

# Default limits
DEFAULT_EXECUTION_TIMEOUT = 30  # seconds
MAX_EXECUTION_TIMEOUT = 300  # hard cap
DEFAULT_MEMORY_LIMIT_MB = 256


class PluginSandboxError(RuntimeError):
    """Raised when a plugin operation fails inside the sandbox."""


class ExecutionTimeoutError(PluginSandboxError):
    """Raised when a plugin execution exceeds the time limit."""


class ResourceLimitError(PluginSandboxError):
    """Raised when a plugin exceeds a resource limit."""


# ── Error boundary ───────────────────────────────────────────────────────────


@contextlib.contextmanager
def error_boundary(plugin_id: str, operation: str = ""):
    """Context manager that catches all exceptions from plugin code.

    Plugin crashes are caught and wrapped in PluginSandboxError so they
    never propagate as unhandled exceptions to the main application.

    Usage::

        with error_boundary("my-plugin", "execute"):
            plugin.execute(params)
    """
    try:
        yield
    except PluginSandboxError:
        raise  # Don't re-wrap sandbox errors
    except Exception as exc:
        label = f"plugin '{plugin_id}'"
        if operation:
            label += f" during {operation}"
        logger.error("%s: %s: %s", label, type(exc).__name__, exc)
        raise PluginSandboxError(f"{label} failed: {exc}") from exc


# ── Timeout execution ────────────────────────────────────────────────────────


class _TimeoutResult:
    """Container for execution result or exception."""

    def __init__(self) -> None:
        self.result: Any = None
        self.exception: BaseException | None = None


def execute_with_timeout(
    func: Callable[..., T],
    timeout: int = DEFAULT_EXECUTION_TIMEOUT,
    *args: Any,
    **kwargs: Any,
) -> T:
    """Execute a function with a timeout in a separate thread.

    If the function does not return within *timeout* seconds,
    raises ExecutionTimeoutError.

    The timeout is clamped to MAX_EXECUTION_TIMEOUT.
    """
    effective_timeout = min(max(1, timeout), MAX_EXECUTION_TIMEOUT)
    container = _TimeoutResult()
    thread = threading.Thread(target=_run_in_thread, args=(func, container, args, kwargs), daemon=True)
    thread.start()
    thread.join(timeout=effective_timeout)

    if thread.is_alive():
        raise ExecutionTimeoutError(
            f"execution timed out after {effective_timeout}s"
        )

    if container.exception is not None:
        raise container.exception  # type: ignore[misc]

    return container.result


def _run_in_thread(
    func: Callable[..., T],
    container: _TimeoutResult,
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
) -> None:
    """Target function for timeout thread execution."""
    try:
        container.result = func(*args, **kwargs)
    except BaseException as exc:
        container.exception = exc


# ── Resource monitoring ──────────────────────────────────────────────────────


class ResourceMonitor:
    """Monitors resource usage of the current process.

    Provides estimates of memory and CPU usage for plugin operations.
    """

    @staticmethod
    def current_memory_mb() -> float:
        """Estimate current process memory usage in MB."""
        try:
            import psutil
            process = psutil.Process(os.getpid())
            mem_info = process.memory_info()
            return mem_info.rss / (1024 * 1024)
        except ImportError:
            return 0.0

    @staticmethod
    def check_memory_limit(limit_mb: int = DEFAULT_MEMORY_LIMIT_MB) -> None:
        """Check if current memory usage exceeds the limit.

        Raises ResourceLimitError if the limit is exceeded.
        """
        current = ResourceMonitor.current_memory_mb()
        if current > limit_mb:
            raise ResourceLimitError(
                f"memory usage {current:.1f}MB exceeds limit {limit_mb}MB"
            )


# ── Sandboxed execution (convenience) ────────────────────────────────────────


def sandboxed_execute(
    plugin_id: str,
    func: Callable[..., T],
    *args: Any,
    timeout: int = DEFAULT_EXECUTION_TIMEOUT,
    memory_limit_mb: int = DEFAULT_MEMORY_LIMIT_MB,
    **kwargs: Any,
) -> T:
    """Execute a plugin function with full sandbox protection.

    Combines error boundary, timeout, and memory limit checks.

    Usage::

        result = sandboxed_execute("my-plugin", plugin.execute, params)
    """
    with error_boundary(plugin_id, func.__name__):
        ResourceMonitor.check_memory_limit(memory_limit_mb)
        return execute_with_timeout(func, timeout, *args, **kwargs)


# ── Module-level whitelist control (Phase 1 in-process isolation) ────────────

# In Phase 1, plugins cannot import these internal modules directly.
# This is enforced at the registry level, not here.
INTERNAL_MODULES_BLOCKED = frozenset({
    "ui_main_window",
    "DatabaseHandler",
    "app_context",
    "assistant_security_layer",
    "assistant_autonomy",
})


# ── Sub-process isolation (Phase 2, opt-in) ─────────────────────────────────


def choose_isolation_mode() -> str:
    """返回插件执行隔离模式。

    - ``"sandbox"``：进程内沙箱（默认，零回归基准——能力路由先 plugin 优先，兜底 native）；
    - ``"subprocess"``：独立子进程（通过 ``SIYI_PLUGIN_ISOLATION=subprocess`` 显式开启）。

    T4.1 灰度策略：先只在 info 链路（infoclassifier/dedup）显式开启 subprocess 观察
    T3.6 遥测，确认无功能回退后再决定是否翻转默认值。未设环境变量时恒为进程内沙箱。
    """
    return "subprocess" if os.environ.get("SIYI_PLUGIN_ISOLATION") == "subprocess" else "sandbox"


_subprocess_host: Any = None
_subprocess_host_lock = threading.Lock()


def _get_subprocess_host():
    """单例化子进程宿主（惰性创建，避免每调用 spawn 一次 worker）。"""
    global _subprocess_host
    with _subprocess_host_lock:
        if _subprocess_host is None:
            from plugins.subprocess_host import SubprocessPluginHost

            host = SubprocessPluginHost()
            host.start()
            _subprocess_host = host
        return _subprocess_host


def run_plugin_isolated(
    plugin_id: str,
    entry_point: str,
    params: dict[str, Any],
    required_permissions: list[str] | None = None,
    timeout: int = DEFAULT_EXECUTION_TIMEOUT,
) -> Any:
    """在独立子进程中执行插件 ``execute(params)``。

    与 :func:`sandboxed_execute` 等价的能力入口，区别在于隔离到子进程：
      - 崩溃隔离：插件崩溃不会影响主进程；
      - 资源配额：Windows Job Object / POSIX setrlimit；
      - 权限透传：``required_permissions`` 在子进程内经同一权限管理器校验。

    返回插件 execute 的原始结果（与进程内 sandboxed_execute 一致），保证
    ``_plugin_tools._invoker`` 两种模式可无缝切换。

    Raises:
        RuntimeError: worker 返回非 ok 时携带 error 文本。
    """
    host = _get_subprocess_host()
    return host.execute_plugin(
        entry_point,
        dict(params or {}),
        timeout=timeout,
        required_permissions=list(required_permissions) if required_permissions else None,
        plugin_id=plugin_id,
    )
