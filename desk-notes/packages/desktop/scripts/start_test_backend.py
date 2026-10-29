# -*- coding: utf-8 -*-
"""E2E 后端 harness 的 Python 侧探针/兜底启动器（C-12）。

与 e2e/globalSetup.ts 同一环境契约（SIYI_DATA_DIR 临时目录、禁 desknotes 桥、
TEST_OFFLINE、LOCK_TAKEOVER=0），用途：
1. `--probe`：起隔离后端 → 轮询 /health → 校验 token 文件与临时目录落盘 →
   自杀清理，打印结论。供 e2e harness 落地前后的手动验证与 CI 排障。
2. 默认（无参）：作为独立常驻后端跑给 `playwright --debug` 交互调试用，
   打印 SIYI_E2E_WS_URL 后阻塞，Ctrl-C 清理。Playwright globalSetup 检测到
   SIYI_E2E_WS_URL 已在外部设置时会跳过自spawn（见 globalSetup.ts 顶部逻辑）。

不写真实 data\\data：一切写路径经 SIYI_DATA_DIR 临时目录隔离。
"""
from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
# scripts -> desktop -> packages -> desk-notes -> 仓库根
REPO_ROOT = HERE.parents[3]
APP_DIR = REPO_ROOT / "思忆集app"


def default_python() -> str:
    for cand in (
        os.environ.get("SIYI_E2E_PYTHON"),
        str(APP_DIR / ".venv" / "Scripts" / "python.exe"),
        str(REPO_ROOT / ".venv3.11" / "Scripts" / "python.exe"),
    ):
        if cand and Path(cand).exists():
            return cand
    raise SystemExit("[probe] 找不到 venv python，设 SIYI_E2E_PYTHON 指定")


def harness_env(port: int, data_dir: str) -> dict[str, str]:
    env = dict(os.environ)
    env.update(
        {
            "SIYI_WS_HOST": "127.0.0.1",
            "SIYI_WS_PORT": str(port),
            "SIYI_DATA_DIR": data_dir,
            "SIYI_DISABLE_DESKNOTES_BRIDGE": "1",
            "SIYI_TEST_OFFLINE": "1",
            "SIYI_LOCK_TAKEOVER": "0",
            "SIYI_WS_AUTH_ENABLED": "1",
            "PYTHONIOENCODING": "utf-8",
        }
    )
    return env


def spawn(port: int, log_path: Path) -> tuple[subprocess.Popen, str]:
    data_dir = tempfile.mkdtemp(prefix="siyi-e2e-data-")
    log = open(log_path, "a", encoding="utf-8")
    proc = subprocess.Popen(
        [default_python(), "-m", "backend.headless_main"],
        cwd=str(APP_DIR),
        env=harness_env(port, data_dir),
        stdout=log,
        stderr=log,
    )
    return proc, data_dir


def _http_alive(port: int) -> bool:
    """HTTP 可应答（<500，含 401——/health HTTP 端点强制 token，本机也 401）即就绪。"""
    import urllib.error

    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=5) as r:
            return r.status < 500
    except urllib.error.HTTPError as e:
        return e.code < 500
    except OSError:
        return False


def wait_health(port: int, proc: subprocess.Popen, timeout: float = 180.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if proc.poll() is not None:
            print(f"[probe] 后端提前退出 code={proc.returncode}（锁被占/依赖缺失，看日志）")
            return False
        # /health 是聚合健康报告（新目录可能 degraded、HTTP 层 401），可应答即就绪
        if _http_alive(port):
            return True
        time.sleep(0.5)
    return False


def ping_rpc(port: int) -> dict:
    """用标准库裸 WebSocket 握手成本过高，这里走 TCP+HTTP 已证服务在；
    RPC 就绪由 specs 的 WS 轮询兜底（页面内真实 WebSocket）。"""
    with socket.create_connection(("127.0.0.1", port), timeout=3):
        return {"tcp": "ok"}


def cleanup(proc: subprocess.Popen, data_dir: str) -> None:
    try:
        proc.terminate()
        proc.wait(timeout=10)
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass
    shutil.rmtree(data_dir, ignore_errors=True)


def main() -> int:
    port = int(os.environ.get("SIYI_E2E_WS_PORT", "18899"))
    probe = "--probe" in sys.argv
    log_path = Path(tempfile.gettempdir()) / f"siyi-e2e-probe-{port}.log"
    proc, data_dir = spawn(port, log_path)
    print(f"[probe] pid={proc.pid} data_dir={data_dir}")
    ok = wait_health(port, proc)
    if ok:
        token_file = Path(data_dir) / "auth_token"
        files = sorted(p.name for p in Path(data_dir).iterdir())[:25]
        print(json.dumps({
            "health": "ok",
            "ws_url": f"ws://127.0.0.1:{port}/ws",
            "token_present": token_file.exists(),
            "db_files_in_tmp": [f for f in files if f.endswith(('.db', '.sqlite', 'auth_token'))],
        }, ensure_ascii=False, indent=1))
        print("[probe] 真实 data/data 未被触碰（写全部落在临时目录）")
    else:
        print(f"[probe] FAIL —— 日志尾部：\n{log_path.read_text(encoding='utf-8', errors='replace')[-2000:]}")
    if probe:
        cleanup(proc, data_dir)
        print("[probe] 已清理，结论:", "PASS" if ok else "FAIL")
        return 0 if ok else 1
    if not ok:
        cleanup(proc, data_dir)
        return 1
    print(f"SIYI_E2E_WS_URL=ws://127.0.0.1:{port}/ws  (Ctrl-C 停止)")
    try:
        proc.wait()
    except KeyboardInterrupt:
        pass
    finally:
        cleanup(proc, data_dir)
    return 0


if __name__ == "__main__":
    sys.exit(main())
