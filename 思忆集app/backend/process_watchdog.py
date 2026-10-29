"""后端进程看门狗 — 异常退出自动重启 + 指数退避 + 熔断。

P1.1 任务：包裹 ``backend.headless_main``，在子进程异常退出时按指数退避
重启，连续崩溃触发熔断并上报，避免无限重启循环。

退出码语义（与 ``sys.exit()`` / POSIX signal 对齐）：
- ``0``：干净退出 → 不重启
- ``2`` / ``130``：用户中断（SIGINT / Ctrl+C）→ 不重启
- 其他非零：崩溃 → 按策略重启

熔断规则：在 ``restart_window`` 秒内累计重启次数超过 ``circuit_breaker_threshold``
时停止重启（通常是配置错误或环境问题，无限重启没有意义）。

P0-1 增强：端口存活探测 —— 子进程存活但 WS 端口持续不可达（宽限期后
连续 ``health_max_fails`` 次探测失败）时判定为僵死，终止子进程并按崩溃
流程重启（受 ``health_enabled`` / SIYI_WATCHDOG_HEALTH_ENABLED 控制）。

使用方式：
    # 直接运行（推荐用于生产部署）
    python -m backend.process_watchdog

    # 在 启动.py 中作为子进程拉起
    from backend.process_watchdog import BackendWatchdog
    wd = BackendWatchdog(cmd=[PYTHON, "-m", "backend.headless_main"], cwd=BACKEND_DIR)
    exit_code = wd.run_forever()

设计原则：
- 不引入外部依赖（不依赖 supervisor / systemd / pm2）
- 单实例锁仍由 ``headless_main.acquire_lock`` 负责（defense in depth —
  即使 watchdog 误判重启，第二个实例也会因锁冲突退出）
- 日志使用 ``siyi.watchdog`` 命名空间，便于排查
- 可被单元测试注入 ``spawn_fn`` 替换真实子进程
"""
from __future__ import annotations

import logging
import os
import signal
import subprocess
import sys
import time
from dataclasses import dataclass
from typing import Any, Callable

logger = logging.getLogger("siyi.watchdog")

EXIT_CLEAN = 0
EXIT_SIGINT_BASE = 2
EXIT_SIGINT_POSIX = 130
EXIT_SIGTERM = 143


@dataclass
class WatchdogConfig:
    """看门狗配置。"""
    max_restarts: int = 5
    restart_window: float = 300.0
    base_delay: float = 1.0
    max_delay: float = 60.0
    circuit_breaker_threshold: int = 3
    min_uptime_for_reset: float = 60.0
    # P0-1：端口存活探测（僵死检测）
    health_enabled: bool = True
    health_port: int = 18765
    health_grace: float = 90.0      # 启动宽限期（AppContext 初始化耗时）
    health_interval: float = 10.0   # 探测间隔
    health_max_fails: int = 6       # 连续失败次数阈值（约 60s 不可达判僵死）


class BackendWatchdog:
    """后端进程看门狗。

    Args:
        cmd: 启动后端的命令列表，如 ``[python, "-m", "backend.headless_main"]``
        cwd: 子进程工作目录
        env: 子进程环境变量（None 表示继承当前进程）
        log_file: 日志文件路径（None 表示继承父进程 stdout/stderr）
        config: 看门狗配置（None 使用默认值）
        spawn_fn: 注入用 — 自定义 spawn 函数，返回 Popen 对象。
                  测试时可替换为 fake。None 时使用默认 subprocess.Popen。
        health_probe_fn: 注入用 — 后端健康探测函数（返回 True 表示端口可达）。
                  测试时可替换为 fake。None 时按 config.health_port 构造 TCP 探测。
    """

    def __init__(
        self,
        cmd: list[str],
        cwd: str,
        env: "dict[str, str] | None" = None,
        log_file: "str | None" = None,
        config: "WatchdogConfig | None" = None,
        spawn_fn: "Callable[..., subprocess.Popen] | None" = None,
        health_probe_fn: "Callable[[], bool] | None" = None,
    ):
        self.cmd = list(cmd)
        self.cwd = cwd
        self.env = env
        self.log_file = log_file
        self.config = config or WatchdogConfig()
        self._spawn_fn = spawn_fn or subprocess.Popen
        self._health_probe = health_probe_fn or _default_health_probe(self.config.health_port)
        self._shutdown_requested = False
        self._child: "subprocess.Popen | None" = None
        # 子进程等待轮询间隔（测试可调小加速）
        self._wait_poll_interval = 1.0

    def run_forever(self) -> int:
        """运行后端并监控，按需重启。

        Returns:
            最终退出码（0 表示干净退出，非零表示最终失败）
        """
        restart_history: list[float] = []
        attempt = 0
        while True:
            start_time = time.time()
            exit_code = self._spawn_and_wait()
            uptime = time.time() - start_time

            if exit_code == EXIT_CLEAN:
                logger.info("backend exited cleanly (code=0) — no restart needed")
                return EXIT_CLEAN

            if exit_code in (EXIT_SIGINT_BASE, EXIT_SIGINT_POSIX):
                logger.info("backend interrupted by user (SIGINT, code=%d) — no restart", exit_code)
                return exit_code

            if exit_code == EXIT_SIGTERM:
                logger.info("backend terminated by SIGTERM (code=%d) — no restart", exit_code)
                return exit_code

            if self._shutdown_requested:
                logger.info("watchdog shutdown requested — no restart")
                return exit_code

            if uptime >= self.config.min_uptime_for_reset:
                logger.info(
                    "backend ran stably for %.1fs before crash — resetting restart counter",
                    uptime,
                )
                attempt = 0
                restart_history.clear()

            now = time.time()
            restart_history = [t for t in restart_history if now - t < self.config.restart_window]

            if len(restart_history) >= self.config.circuit_breaker_threshold:
                logger.critical(
                    "CIRCUIT BREAKER: %d restarts within %.0fs window — giving up "
                    "(likely config/env issue, not transient crash). Last exit_code=%d",
                    len(restart_history),
                    self.config.restart_window,
                    exit_code,
                )
                return exit_code

            if attempt >= self.config.max_restarts:
                logger.critical(
                    "max_restarts (%d) reached — giving up. Last exit_code=%d",
                    self.config.max_restarts,
                    exit_code,
                )
                return exit_code

            delay = min(
                self.config.base_delay * (2 ** attempt),
                self.config.max_delay,
            )
            logger.warning(
                "backend crashed (exit_code=%d, uptime=%.1fs) — restart attempt %d/%d in %.1fs",
                exit_code,
                uptime,
                attempt + 1,
                self.config.max_restarts,
                delay,
            )
            self._sleep_or_interruptible(delay)
            if self._shutdown_requested:
                logger.info("shutdown requested during backoff — abort restart")
                return exit_code

            restart_history.append(time.time())
            attempt += 1

    def request_shutdown(self) -> None:
        """请求关闭：下次循环不再重启，并终止当前子进程。"""
        self._shutdown_requested = True
        if self._child is None:
            return
        if self._child.poll() is not None:
            return
        try:
            self._child.terminate()
        except Exception as exc:  # noqa: BLE001
            logger.warning("failed to terminate child during shutdown: %s", exc)

    def _spawn_and_wait(self) -> int:
        """启动子进程并等待其退出。返回退出码。"""
        kwargs: dict[str, Any] = {"cwd": self.cwd, "env": self.env}
        log_fh = None
        if self.log_file is not None:
            log_fh = open(self.log_file, "a", encoding="utf-8")  # noqa: SIM115
            kwargs["stdout"] = log_fh
            kwargs["stderr"] = subprocess.STDOUT

        logger.info("spawning backend: %s (cwd=%s)", " ".join(self.cmd), self.cwd)
        proc = self._spawn_fn(self.cmd, **kwargs)
        self._child = proc
        self._install_signal_forwarders(proc)
        try:
            exit_code = self._wait_with_health_monitor(proc)
            logger.info("backend process exited with code=%s", exit_code)
        except KeyboardInterrupt:
            logger.info("watchdog received KeyboardInterrupt — terminating child")
            if self._child is not None and self._child.poll() is None:
                self._child.terminate()
                try:
                    self._child.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    self._child.kill()
            exit_code = EXIT_SIGINT_BASE
        except Exception as exc:  # noqa: BLE001
            logger.exception("watchdog spawn/wait failed: %s", exc)
            exit_code = 1
        finally:
            self._child = None
            if log_fh is not None:
                try:
                    log_fh.close()
                except Exception:  # noqa: BLE001
                    pass
        return exit_code

    def _wait_with_health_monitor(self, proc: subprocess.Popen) -> int:
        """等待子进程退出，同时做端口存活探测（P0-1 僵死检测）。

        子进程存活但 WS 端口宽限期后持续不可达 → 判定僵死，终止子进程
        并返回非零退出码（交给 run_forever 的重启/熔断流程）。
        """
        cfg = self.config
        start_wait = time.time()
        next_probe_at = start_wait + cfg.health_grace
        consecutive_fails = 0
        while True:
            try:
                return proc.wait(timeout=self._wait_poll_interval)
            except subprocess.TimeoutExpired:
                pass
            code = proc.poll()
            if code is not None:
                return code
            if not (cfg.health_enabled and self._health_probe is not None):
                continue
            if time.time() < next_probe_at:
                continue
            next_probe_at = time.time() + cfg.health_interval
            try:
                healthy = self._health_probe()
            except Exception as exc:  # noqa: BLE001
                logger.warning("health probe raised (treating as unhealthy): %s", exc)
                healthy = False
            if healthy:
                consecutive_fails = 0
                continue
            consecutive_fails += 1
            logger.warning(
                "backend port unreachable (%d/%d consecutive, uptime=%.0fs)",
                consecutive_fails,
                cfg.health_max_fails,
                time.time() - start_wait,
            )
            if consecutive_fails >= cfg.health_max_fails:
                logger.error(
                    "backend zombie: alive but WS port unreachable for %d consecutive probes "
                    "— killing child to trigger restart flow",
                    consecutive_fails,
                )
                try:
                    proc.kill()
                    proc.wait(timeout=10)
                except Exception as exc:  # noqa: BLE001
                    logger.warning("failed to kill zombie child: %s", exc)
                code = proc.poll()
                return code if code is not None else 1

    def _install_signal_forwarders(self, proc: subprocess.Popen) -> None:
        """安装信号转发：父进程收到 SIGINT/SIGTERM 时转发给子进程。"""
        import threading as _threading

        if not hasattr(signal, "SIGINT"):
            return
        if _threading.current_thread() is not _threading.main_thread():
            # signal.signal 只能在主线程调用（生产入口恒为主线程；测试线程场景跳过）
            return

        def _forward(signum: int, frame) -> None:
            self._shutdown_requested = True
            if proc.poll() is not None:
                return
            logger.info("forwarding signal %d to child PID=%d", signum, proc.pid)
            try:
                proc.send_signal(signum)
            except Exception as exc:  # noqa: BLE001
                logger.warning("failed to forward signal %d: %s", signum, exc)

        signal.signal(signal.SIGINT, _forward)
        if hasattr(signal, "SIGTERM"):
            signal.signal(signal.SIGTERM, _forward)

    def _sleep_or_interruptible(self, seconds: float) -> None:
        """可被 shutdown_requested 中断的 sleep。"""
        deadline = time.time() + seconds
        while time.time() < deadline:
            if self._shutdown_requested:
                return
            time.sleep(min(0.5, deadline - time.time()))


def _default_backend_cmd() -> "tuple[list[str], str]":
    """构造默认后端启动命令与工作目录。"""
    python = sys.executable or "python"
    cmd = [python, "-m", "backend.headless_main"]
    cwd = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return cmd, cwd


def _default_health_probe(port: int) -> "Callable[[], bool]":
    """构造 TCP 端口探测函数（后端 WS 端口可达 = 健康）。"""
    import socket

    def _probe() -> bool:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=2):
                return True
        except OSError:
            return False

    return _probe


def main() -> int:
    """``python -m backend.process_watchdog`` 入口。"""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] [%(name)s] %(message)s",
        stream=sys.stderr,
    )

    cmd, cwd = _default_backend_cmd()
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    log_file = os.environ.get("SIYI_BACKEND_LOG") or None

    config = WatchdogConfig(
        max_restarts=int(os.environ.get("SIYI_WATCHDOG_MAX_RESTARTS", "5")),
        restart_window=float(os.environ.get("SIYI_WATCHDOG_RESTART_WINDOW", "300")),
        base_delay=float(os.environ.get("SIYI_WATCHDOG_BASE_DELAY", "1.0")),
        max_delay=float(os.environ.get("SIYI_WATCHDOG_MAX_DELAY", "60.0")),
        circuit_breaker_threshold=int(os.environ.get("SIYI_WATCHDOG_CIRCUIT_BREAKER", "3")),
        min_uptime_for_reset=float(os.environ.get("SIYI_WATCHDOG_MIN_UPTIME_RESET", "60")),
        # P0-1：端口存活探测（僵死检测）
        health_enabled=os.environ.get("SIYI_WATCHDOG_HEALTH_ENABLED", "1") == "1",
        health_port=int(os.environ.get(
            "SIYI_WATCHDOG_HEALTH_PORT", os.environ.get("SIYI_WS_PORT", "18765")
        )),
        health_grace=float(os.environ.get("SIYI_WATCHDOG_HEALTH_GRACE", "90")),
        health_interval=float(os.environ.get("SIYI_WATCHDOG_HEALTH_INTERVAL", "10")),
        health_max_fails=int(os.environ.get("SIYI_WATCHDOG_HEALTH_FAILS", "6")),
    )

    watchdog = BackendWatchdog(cmd, cwd, env, log_file, config)
    return watchdog.run_forever()


if __name__ == "__main__":
    sys.exit(main())
