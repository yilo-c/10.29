"""集成测试用「独立后端实例」辅助模块。

对外 API（被 test_rate_limit / test_security_injection /
test_ws_version_negotiation / test_headless_cold_start 依赖）：

- ``spawn_backend() -> (proc, ws_url, data_dir)``
      拉起一个隔离的 headless 后端（随机端口 + 临时数据目录），**阻塞至 TCP 就绪**
      后返回。注意：只保证 TCP 就绪，不保证 RPC 就绪——调用方若需 RPC 就绪应
      自行轮询 ping（见 test_headless_cold_start._wait_ping_ready）。
- ``teardown_backend(proc, data_dir)``
      终止进程并清理临时数据目录。
- ``rpc(ws_url, method, params=None, timeout=30) -> dict``
      协程：发一条 JSON-RPC 请求并返回**完整响应 dict**（含 result / error）。

历史教训（2026-08-29）：本模块曾以"会话本地产物"形式存在，**从未入 git**，
会话结束后丢失 → 4 个测试文件 ImportError、pytest 收集阶段即中断，本地门禁
整体全红且无人察觉（数日）。现重新实现并入版本控制，由
``tests/test_test_infra_contract.py`` 结构性守护其存在与签名。
"""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Sequence

APP_DIR = Path(__file__).resolve().parents[1]

# 与 tests/conftest.py 的 AUTOSTART 后端保持一致的环境约定
_DEFAULT_HOST = "127.0.0.1"
_READY_TIMEOUT = 90.0

def _free_port(host: str = _DEFAULT_HOST) -> int:
    with socket.socket() as s:
        s.bind((host, 0))
        return int(s.getsockname()[1])


def spawn_backend(
    host: str = _DEFAULT_HOST,
    env_extra: dict[str, str] | None = None,
    timeout: float = _READY_TIMEOUT,
):
    """拉起隔离后端，返回 ``(proc, ws_url, data_dir)``。

    阻塞直到端口可连接（TCP 就绪）。失败会终止进程并清理目录后抛错——
    不接受"静默返回以便测试自行 skip"（P2-13 / 门禁红优于假绿的原则）。
    """
    port = _free_port(host)
    data_dir = tempfile.mkdtemp(prefix="siyi-isolated-")
    env = dict(os.environ)
    env["SIYI_WS_HOST"] = host
    env["SIYI_WS_PORT"] = str(port)
    env["SIYI_DATA_DIR"] = data_dir
    env["SIYI_LOCK_TAKEOVER"] = "0"  # 测试环境禁止接管他人锁
    env["SIYI_TEST_OFFLINE"] = "1"  # 跳过 RSS 采集 / autonomy AI 调度
    if env_extra:
        env.update(env_extra)

    proc = subprocess.Popen(
        [sys.executable, "-m", "backend.headless_main"],
        cwd=str(APP_DIR),
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    deadline = time.time() + timeout
    while time.time() < deadline:
        if proc.poll() is not None:
            teardown_backend(proc, data_dir)
            raise RuntimeError(f"隔离后端进程提前退出（returncode={proc.returncode}）")
        try:
            with socket.create_connection((host, port), timeout=1):
                return proc, f"ws://{host}:{port}/ws", data_dir
        except OSError:
            time.sleep(0.3)

    teardown_backend(proc, data_dir)
    raise RuntimeError(f"隔离后端在 {timeout}s 内未就绪（端口 {port}）")


def teardown_backend(proc, data_dir: str | None) -> None:
    """终止后端进程并清理临时数据目录（幂等，允许 proc/data_dir 为 None）。"""
    if proc is not None:
        try:
            proc.terminate()
            proc.wait(timeout=10)
        except Exception:  # noqa: BLE001 - 清理路径不得抛出
            try:
                proc.kill()
            except Exception:  # noqa: BLE001
                pass
    if data_dir:
        shutil.rmtree(data_dir, ignore_errors=True)


async def rpc(
    ws_url: str,
    method: str,
    params: dict[str, Any] | Sequence[Any] | None = None,
    timeout: float = 30.0,
) -> dict[str, Any]:
    """向 ``ws_url`` 发一条 JSON-RPC 请求，返回完整响应 dict。

    loopback 直连由后端 `_verify_auth` 本机信任分支放行，无需 token。
    超时返回 ``{"error": {"code": -32000, "message": "rpc timeout ..."}}``
    而非抛出，便于测试断言"超时后后端仍存活"（test_security_injection）。
    """
    # 每次 rpc 调用都开一条新连接（本 helper 不做连接复用），因此请求 id 固定为 1：
    # 既满足"响应必须回显请求 id"的信封契约（test_ws_version_negotiation 断言 id==1），
    # 也不会在同一连接上出现 id 冲突。若将来改为连接复用，此处需换成连接内递增计数。
    req_id = 1
    payload = {"jsonrpc": "2.0", "id": req_id, "method": method}
    if params is not None:
        payload["params"] = params

    try:
        import websockets  # 延迟导入：非集成测试路径不支付导入成本
    except ImportError as exc:  # pragma: no cover - 依赖缺失应fail而非skip
        raise RuntimeError("集成测试需要 websockets 依赖") from exc

    async def _once() -> dict[str, Any]:
                # M2.1 R4：声明 client_version（与桌面 backend-relay 同源），否则按
        # legacy_client 处理，R4 方法直接 -32010，pending→approve 链路走不到。
        url = ws_url + ("&" if "?" in ws_url else "?") + "client_version=1.2.0"
        async with websockets.connect(url, open_timeout=timeout) as ws:
            await ws.send(json.dumps(payload))
            # 后端会在同一连接上推送事件帧（EventBridge），必须先跳过它们，
            # 否则拿到的是 notes.changed 之类的事件而非本次调用的响应。
            while True:
                raw = await asyncio.wait_for(ws.recv(), timeout=timeout)
                if isinstance(raw, bytes):
                    raw = raw.decode("utf-8", errors="replace")
                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    return {"error": {"code": -32700, "message": f"non-json response: {raw[:200]}"}}
                if isinstance(msg, dict) and msg.get("type") == "event":
                    continue
                if isinstance(msg, dict) and (
                    msg.get("id") == req_id or "result" in msg or "error" in msg
                ):
                    return msg

    try:
        return await asyncio.wait_for(_once(), timeout=timeout + 5)
    except (asyncio.TimeoutError, TimeoutError):
        return {"error": {"code": -32000, "message": f"rpc timeout after {timeout}s: {method}"}}
    except OSError as exc:
        return {"error": {"code": -32001, "message": f"rpc connection error: {exc}"}}
    except Exception as exc:  # noqa: BLE001 - 服务端拒收超大消息会关闭连接
        # websockets 的连接关闭类异常不继承 OSError，必须在此兜住：
        # test_security_injection 期望"被拒绝 → 返回 error 且后端仍存活"。
        if type(exc).__module__.startswith("websockets"):
            return {"error": {"code": -32002, "message": f"rpc transport error: {type(exc).__name__}: {exc}"}}
        raise


async def rpc_approved(
    ws_url: str,
    method: str,
    params: dict[str, Any] | Sequence[Any] | None = None,
    timeout: float = 30.0,
    max_hops: int = 3,
) -> dict[str, Any]:
    """``rpc`` 的审批感知版本：走完"pending → sandbox.approve → 重放"链路。

    R4 破坏性方法（notes.delete / cron.remove / task_queue.purge_completed /
    usage.purge 等，见 backend/handlers/__init__.py 的 _R4_METHODS）在 P0-6 之后
    会先返回 ``{"pending": True, "pending_id": ...}``，必须经 sandbox.approve
    批准才真正执行——这正是真实前端的链路。集成测试若直接用 rpc 调删除类方法，
    会拿到 pending 且删除并未发生（test_security_injection 曾因此假失败）。
    """
    resp = await rpc(ws_url, method, params, timeout=timeout)
    for _ in range(max_hops):
        result = resp.get("result")
        if not (isinstance(result, dict) and result.get("pending")):
            return resp
        pid = result.get("pending_id")
        if not pid:
            return resp
        resp = await rpc(ws_url, "sandbox.approve", {"pending_id": pid}, timeout=timeout)
    return resp
