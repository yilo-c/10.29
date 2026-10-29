"""单实例文件锁与僵死进程接管（P0-1）。

职责：
- msvcrt 文件锁防止多后端实例
- 锁文件记录持有者 PID，供后续僵死探测
- 获取失败时的接管策略（三重校验：PID 存活 + 端口可达 + 进程名）：
  - 锁内无 PID（旧格式锁文件）→ 不接管，维持原拒绝行为
  - PID 已死 → 清除残留锁文件后重试
  - PID 存活但 WS 端口宽限探测后持续不可达，且进程为 python →
    终止该进程后重试（受 ``SIYI_LOCK_TAKEOVER`` 控制，默认开）

设计原则：
- 宁可拒绝接管也不误杀活进程（三重校验 + 宽限期多次探测）
- 与 headless_main 分离，避免测试拉起整条 AppContext 依赖链
"""

from __future__ import annotations

import logging
import os
import socket
import subprocess
import sys
import time

from core.subprocess_text import run_text

try:
    import msvcrt

    HAS_MSVCRT = True
except ImportError:
    HAS_MSVCRT = False

logger = logging.getLogger("siyi.backend.lock")

LOCK_FILE_NAME = "siyi_headless.lock"

# 僵死探测：宽限探测次数与间隔（端口不可达需持续 N 次才认定僵死）
_TAKEOVER_PROBES = 3
_TAKEOVER_PROBE_INTERVAL = 2.0
_TERMINATE_WAIT_SECONDS = 5.0


def _data_dir_key() -> str:
    """锁粒度标识 = 数据目录（2026-09-13 并行测试矩阵改造）。

    旧实现全机器一把锁，导致「不同数据目录的隔离后端」互相接管误杀（当日实测
    事故）。改为按数据目录派生锁名：生产默认目录仍全局单实例；测试用隔离
    SIYI_DATA_DIR 各持各锁，可安全并行。
    """
    import hashlib

    data_dir = os.environ.get("SIYI_DATA_DIR", "").strip()
    if not data_dir:
        return "default"
    key = os.path.abspath(data_dir).lower().replace("/", "\\")
    return hashlib.md5(key.encode("utf-8")).hexdigest()[:10]


def lock_path() -> str:
    """锁文件路径（TEMP 目录下，按数据目录派生）。"""
    name = LOCK_FILE_NAME if _data_dir_key() == "default" else f"siyi_headless_{_data_dir_key()}.lock"
    return os.path.join(os.environ.get("TEMP", os.path.expanduser("~")), name)


def lock_path_for_data_dir(data_dir: str) -> str:
    """供外部（如 v1_acceptance 清理陈旧锁）按指定数据目录推导锁路径。"""
    import hashlib

    key = os.path.abspath(data_dir).lower().replace("/", "\\")
    return os.path.join(
        os.environ.get("TEMP", os.path.expanduser("~")),
        f"siyi_headless_{hashlib.md5(key.encode('utf-8')).hexdigest()[:10]}.lock",
    )


def _ws_port() -> int:
    return int(os.environ.get("SIYI_WS_PORT", "18765"))


def _try_acquire(path: str) -> int | None:
    """尝试获取文件锁；成功返回 fd，失败返回 None。"""
    fd = None
    try:
        fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o644)
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        logger.debug("single instance lock acquired: %s", path)
        return fd
    except (OSError, BlockingIOError):
        if fd is not None:
            try:
                os.close(fd)
            except OSError:
                pass
        return None


def _pid_sidecar(path: str) -> str:
    """PID 旁路文件路径。

    Windows 下锁文件被持有者以独占共享模式打开（任何模式都无法再打开），
    因此持有者 PID 记录在同目录的旁路文件中，供后续僵死探测读取。
    """
    return path + ".pid"


def _write_holder_pid(path: str) -> None:
    """在旁路文件中记录持有者 PID。"""
    try:
        with open(_pid_sidecar(path), "w", encoding="ascii") as f:
            f.write(str(os.getpid()))
    except OSError as exc:
        logger.warning("failed to write lock holder PID sidecar: %s", exc)


def read_lock_pid(path: str) -> int | None:
    """读取旁路文件中记录的持有者 PID；无记录或非法返回 None。"""
    try:
        with open(_pid_sidecar(path), "r", encoding="ascii", errors="ignore") as f:
            content = f.read().strip()
        return int(content) if content.isdigit() else None
    except OSError:
        return None


def is_pid_alive(pid: int) -> bool:
    """Windows 下通过 OpenProcess 判断进程是否存活。"""
    if pid <= 0:
        return False
    if sys.platform != "win32":
        # 非 Windows 平台保守返回 False（当前部署仅 Windows）
        return False
    import ctypes

    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    ERROR_ACCESS_DENIED = 5
    kernel32 = ctypes.windll.kernel32
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if handle:
        kernel32.CloseHandle(handle)
        return True
    # OpenProcess 失败：进程不存在，或无权限（无权限说明进程存在）
    return kernel32.GetLastError() == ERROR_ACCESS_DENIED


def ws_port_reachable(port: int | None = None) -> bool:
    """探测后端 WS 端口是否可 TCP 连接。"""
    target = port if port is not None else _ws_port()
    try:
        with socket.create_connection(("127.0.0.1", target), timeout=1.5):
            return True
    except OSError:
        return False


def looks_like_python(pid: int) -> bool:
    """通过 tasklist 确认进程映像名含 python（防止 PID 复用误杀无关进程）。"""
    try:
        out = run_text(["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"], timeout=5).stdout.lower()
        return "python" in out
    except Exception as exc:  # noqa: BLE001
        logger.warning("tasklist probe failed for PID=%d: %s", pid, exc)
        return False


def terminate_pid(pid: int) -> bool:
    """强制终止指定 PID 并等待其退出。"""
    try:
        subprocess.run(
            ["taskkill", "/PID", str(pid), "/F"],
            capture_output=True,
            timeout=10,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("failed to terminate zombie PID=%d: %s", pid, exc)
        return False
    deadline = time.time() + _TERMINATE_WAIT_SECONDS
    while time.time() < deadline:
        if not is_pid_alive(pid):
            return True
        time.sleep(0.3)
    return not is_pid_alive(pid)


def attempt_stale_takeover(path: str) -> bool:
    """获取锁失败后的僵死接管判定。返回 True 表示已清理/终止旧持有者，可重试获取。

    三重校验：PID 存活 + 端口宽限探测 + 进程名含 python；任一环节存疑即放弃接管。
    """
    pid = read_lock_pid(path)
    if pid is None:
        logger.debug("no holder PID recorded (sidecar missing) — refusing takeover (legacy lock)")
        return False

    if not is_pid_alive(pid):
        logger.warning("lock holder PID=%d is dead — removing stale lock files", pid)
        for stale in (path, _pid_sidecar(path)):
            try:
                os.remove(stale)
            except OSError as exc:
                logger.warning("failed to remove stale lock file %s: %s", stale, exc)
        return True

    if os.environ.get("SIYI_LOCK_TAKEOVER", "1") != "1":
        logger.debug("SIYI_LOCK_TAKEOVER disabled — refusing takeover of live PID=%d", pid)
        return False

    # PID 存活：宽限探测端口，持续不可达才认定僵死
    for _ in range(_TAKEOVER_PROBES):
        if ws_port_reachable():
            logger.debug("lock holder PID=%d is serving on WS port — healthy, no takeover", pid)
            return False
        time.sleep(_TAKEOVER_PROBE_INTERVAL)
    if ws_port_reachable():
        return False

    if not looks_like_python(pid):
        logger.error(
            "lock holder PID=%d alive and port down, but process is not python — refusing takeover (possible PID reuse)",
            pid,
        )
        return False

    logger.warning(
        "zombie backend detected (PID=%d alive, WS port unreachable) — terminating for takeover",
        pid,
    )
    return terminate_pid(pid)


def acquire() -> int | None:
    """获取单实例锁，返回锁文件 fd；失败返回 None。

    获取失败时执行僵死接管判定（见 attempt_stale_takeover），接管成功后重试一次。
    """
    if not HAS_MSVCRT:
        return None
    path = lock_path()
    fd = _try_acquire(path)
    if fd is None and attempt_stale_takeover(path):
        fd = _try_acquire(path)
    if fd is None:
        logger.error("another instance is already running (lock: %s)", path)
        return None
    _write_holder_pid(path)
    return fd


def release(fd: int | None) -> None:
    """释放单实例锁并清理 PID 旁路文件。"""
    if fd is None or not HAS_MSVCRT:
        return
    try:
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
        os.close(fd)
    except Exception as exc:  # noqa: BLE001
        # P1 修复：文件锁释放失败无日志，残留锁文件可能导致下次启动失败
        logger.warning("Failed to release instance lock: %s", exc, exc_info=True)
    try:
        os.remove(_pid_sidecar(lock_path()))
    except OSError:
        pass  # 旁路文件残留无害（下次 acquire 会重写）
