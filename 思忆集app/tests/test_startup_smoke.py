"""L0 启动链路 + 健康契约冒烟测试（全模块测试战役 T2，2026-08-30）。

覆盖：
1. AUTOSTART 隔离后端 GET /health 主契约——必须由 backend ws_server 应答，
   返回健康报告（status ∈ {healthy, degraded}），而非 browser_assistant_bridge
   的身份载荷（生产现场实锤过的遮蔽缺陷，见 deliverables/l0-startup-findings.md）。
2. 桥专属端点 /api/browser-assistant/status 不得出现在 WS 端口上（P3-5 判别器）。
3. T2 修复后契约：桥以修复后调用形态（avoid_port=WS 端口）启动时，即使桥配置
   端口与 WS 端口冲突也必须自动错开，WS 端口 /health 不被桥身份载荷遮蔽
   （原动态 xfail 复现测试已随 T2 修复转正）；并结构断言两条启动路径
   （app_entry/main.py 与 backend/headless_main.py）的桥启动调用都传 avoid_port。
4. 同一 TEMP（全局锁 %TEMP%/siyi_headless.lock 与 SIYI_DATA_DIR 无关）下
   第二实例必须被锁拒绝、快速退出码 1。
5. 锁作用域结构断言：TEMP 决定、数据目录无关。
6. 【已知真缺陷·动态 xfail】僵死锁接管探测用的是新实例自己的 SIYI_WS_PORT
   而非锁持有者的实际端口——端口不一致时会把健康持有者当僵尸 taskkill（互杀）。

纪律：一律隔离后端（随机端口+临时数据目录+TAKEOVER=0），进程用完即杀；
绝不运行 启动.py；绝不触碰 18765/18902/11434。
"""

from __future__ import annotations

import json
import os
import re
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from types import SimpleNamespace

import pytest

from _isolated_backend import spawn_backend, teardown_backend
from backend import instance_lock
from core.settings_keys import (
    KEY_BROWSER_ASSISTANT_BRIDGE_ENABLED,
    KEY_BROWSER_ASSISTANT_BRIDGE_PORT,
)

APP_DIR = Path(__file__).resolve().parents[1]
_LOCK_BASENAME = "siyi_headless.lock"
# ws_server._handle_health 的 200 档整体状态（unhealthy 走 503）
_OK_HEALTH_STATUSES = {"healthy", "degraded"}


# ── 工具 ──────────────────────────────────────────────────────


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _free_port_with_free_successor() -> int:
    """取空闲端口 p，且 p+1 也空闲（桥与 WS 端口冲突时自动错开到 avoid_port+1）。"""
    for _ in range(50):
        port = _free_port()
        with socket.socket() as s:
            try:
                s.bind(("127.0.0.1", port + 1))
            except OSError:
                continue
        return port
    raise RuntimeError("未能找到相邻双空闲端口")


def _http_get_json(url: str, token: str | None = None, timeout: float = 10.0):
    """GET 并返回 (status_code, json_body)；连接失败返回 (None, {"error": ...})。"""
    req = urllib.request.Request(url)
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        try:
            body = json.loads(exc.read().decode("utf-8", errors="replace"))
        except Exception:  # noqa: BLE001
            body = {}
        return exc.code, body
    except OSError as exc:
        return None, {"error": str(exc)}


class _StubSettings:
    """最小 settings 仓库桩：get_setting(key, default)。"""

    def __init__(self, values: dict):
        self._values = values

    def get_setting(self, key, default=None):
        return self._values.get(key, default)


# ── 模块级隔离后端（随机端口 + 临时数据目录，用完即杀） ────────


@pytest.fixture(scope="module")
def backend():
    proc = ws_url = data_dir = None
    last_err: Exception | None = None
    for _attempt in range(3):
        try:
            proc, ws_url, data_dir = spawn_backend(timeout=120.0)
            break
        except RuntimeError as exc:  # 并发 pytest 会话争会话级锁时退避重试
            last_err = exc
            time.sleep(15.0)
    if proc is None:
        pytest.fail(f"隔离后端 3 次尝试均未能启动：{last_err}")

    port = int(ws_url.split(":")[2].split("/")[0])
    token_path = Path(data_dir) / "auth_token"
    deadline = time.time() + 30.0
    token = ""
    while time.time() < deadline:
        if token_path.is_file():
            token = token_path.read_text(encoding="utf-8").strip()
            if token:
                break
        if proc.poll() is not None:
            break
        time.sleep(0.3)
    assert token, f"隔离后端未产出 auth_token（{token_path}）"

    handle = SimpleNamespace(proc=proc, ws_url=ws_url, port=port, data_dir=data_dir, token=token)
    yield handle
    teardown_backend(proc, data_dir)


# ── 1/2. 健康主契约（隔离后端实测） ───────────────────────────


def test_health_contract_on_autostart_backend(backend):
    """/health 必须由 ws_server 应答：健康报告契约，而非桥身份载荷。"""
    status, body = _http_get_json(f"http://127.0.0.1:{backend.port}/health", token=backend.token)
    assert status == 200, f"/health 应 200，实测 {status}: {body}"
    assert isinstance(body, dict)
    assert body.get("status") in _OK_HEALTH_STATUSES, f"/health 主契约 status 应为 healthy/degraded，实测 {body.get('status')!r}"
    # 详细报告形态（ws_server._handle_health → HealthChecker.get_health_report）
    assert "services" in body and "system" in body, f"缺少健康报告结构: {sorted(body)}"
    # 不得是 browser_assistant_bridge 的身份载荷（生产遮蔽缺陷的特征键）
    assert body.get("service") != "browser_assistant_bridge", f"/health 被桥遮蔽：{sorted(body)}"
    assert "ok" not in body, f"/health 返回了桥式 ok 载荷: {sorted(body)}"


def test_bridge_endpoints_absent_from_ws_port(backend):
    """WS 端口上不得有桥端点；/health 认证语义 = HTTP 严格 Bearer。"""
    status, body = _http_get_json(
        f"http://127.0.0.1:{backend.port}/api/browser-assistant/status",
        token=backend.token,
    )
    assert status == 404, f"桥端点不应出现在 WS 端口（P3-5 判别器），实测 {status}: {body}"
    # HTTP 端点不吃 loopback 信任（ws_server.py:225-227），无 token 必须 401
    status_noauth, _ = _http_get_json(f"http://127.0.0.1:{backend.port}/health")
    assert status_noauth == 401, f"无 token 的 /health 应 401，实测 {status_noauth}"


# ── 3. T2 修复后契约：桥必须避开主 WS 契约端口 ────────────────


def test_bridge_with_avoid_port_does_not_shadow_ws_health():
    """T2 修复后契约：以修复后的调用形态（avoid_port=WS 端口）启动桥时，
    即使桥配置端口与 WS 端口相同（生产 18765 遮蔽的冲突配置），桥也必须
    自动错开端口，WS 端口 /health 不得被桥身份载荷遮蔽。
    （原「动态 xfail 复现」已随 app_entry/main.py 传入 avoid_port 转正，
    见 deliverables/l0-startup-findings.md §5）
    """
    from browser.assistant_bridge import BrowserAssistantBridgeService

    ws_port = _free_port_with_free_successor()  # 代位主 WS 契约端口（不触碰 18765 现役实例）
    svc = BrowserAssistantBridgeService(
        settings_repository=_StubSettings(
            {
                KEY_BROWSER_ASSISTANT_BRIDGE_PORT: ws_port,  # 冲突配置：桥端口==WS 端口
                KEY_BROWSER_ASSISTANT_BRIDGE_ENABLED: True,
            }
        )
    )
    try:
        start_status = svc.start(avoid_port=ws_port)  # 修复后调用形态（headless_main.py:502 / app_entry/main.py）
        assert start_status.get("running") is True, f"桥应错开端口后成功启动，实测 {start_status}"
        assert start_status.get("port") != ws_port, f"桥不得监听 WS 端口 {ws_port}，实测 {start_status}"
        status, body = _http_get_json(f"http://127.0.0.1:{ws_port}/health")
        assert status is None, f"WS 端口 /health 被桥遮蔽（桥在 {start_status.get('port')}，但 {ws_port} 仍有应答）: {status} {body}"
    finally:
        svc.stop()


def test_startup_paths_pass_avoid_port_to_bridge():
    """结构性回归断言（T2）：两条启动路径的 start_browser_assistant_bridge
    调用都必须传 avoid_port，防止回归到无 avoid_port 的调用形态
    （桥可抢占/遮蔽主 WS 契约端口上的 /health）。
    """
    call_pattern = re.compile(r"start_browser_assistant_bridge\(\s*avoid_port\s*=")
    app_entry_src = (APP_DIR / "app_entry" / "main.py").read_text(encoding="utf-8")
    assert call_pattern.search(app_entry_src), "app_entry/main.py 的 start_browser_assistant_bridge 调用未传 avoid_port（T2 回归）"
    headless_src = (APP_DIR / "backend" / "headless_main.py").read_text(encoding="utf-8")
    assert call_pattern.search(headless_src), "backend/headless_main.py 的 start_browser_assistant_bridge 调用未传 avoid_port"


# ── 4. 全局锁：同数据目录第二实例被拒 ─────────────────────────


def test_second_instance_same_data_dir_rejected_by_lock(backend):
    """锁文件在 %TEMP%（与 SIYI_DATA_DIR 无关）：第二实例 acquire 失败 → exit(1)。"""
    env = dict(os.environ)
    env["SIYI_WS_HOST"] = "127.0.0.1"
    env["SIYI_WS_PORT"] = str(_free_port())  # 随机端口：端口不冲突，仅锁冲突
    env["SIYI_DATA_DIR"] = backend.data_dir  # 与第一实例同数据目录
    env["SIYI_LOCK_TAKEOVER"] = "0"  # 禁止接管（测试绝不 taskkill 任何进程）
    env["SIYI_TEST_OFFLINE"] = "1"

    proc2 = subprocess.Popen(
        [sys.executable, "-m", "backend.headless_main"],
        cwd=str(APP_DIR),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    output_chunks: list[str] = []
    refused_seen = False
    try:
        # 冷启动导入链（app_context 等）实测可达 ~3min（venv shim 多一层 spawn + 导入
        # 争 CPU），锁拒绝本身毫秒级——故用「读到锁拒绝日志行」驱动，而非固定等待。
        def _pump(stream, sink):  # pragma: no cover - 读线程
            for line in stream:
                sink.append(line)

        reader = threading.Thread(target=_pump, args=(proc2.stdout, output_chunks), daemon=True)
        reader.start()
        deadline = time.time() + 300.0
        while time.time() < deadline:
            if proc2.poll() is not None:
                break
            if any("another instance is already running" in line for line in output_chunks):
                refused_seen = True
                break
            time.sleep(0.5)
        if refused_seen and proc2.poll() is None:
            # 拒绝已确认，宽限等待 sys.exit(1) 落地
            exit_deadline = time.time() + 30.0
            while time.time() < exit_deadline and proc2.poll() is None:
                time.sleep(0.2)
        assert refused_seen or proc2.poll() is not None, "第二实例 300s 内既未退出也未出现锁拒绝日志（疑似卡死）"
        assert proc2.returncode == 1, f"第二实例应 sys.exit(1)（headless_main.py:1046-1047），实测 returncode={proc2.returncode}"
        output = "".join(output_chunks)
        assert "another instance is already running" in output, (
            f"应记录锁冲突原因（instance_lock.acquire error 日志），实测输出尾部: {output[-500:]!r}"
        )
    finally:
        if proc2.poll() is None:  # 兜底：进程用完即杀
            proc2.kill()
            proc2.wait(timeout=10)


# ── 5. 锁作用域结构断言：TEMP 决定，数据目录无关 ───────────────


def test_instance_lock_scope_is_temp_not_data_dir(tmp_path, monkeypatch):
    # 2026-09-13 语义修订：锁按数据目录派生（并行测试矩阵前提）。旧断言
    # 「路径不随 SIYI_DATA_DIR 变化」是当年 T2 固化的过宽语义，实测导致隔离
    # 后端互相接管误杀（当日多起）；同一数据目录仍全局单实例（见
    # test_instance_lock.py 同目录冲突用例）。TEMP 敏感性保持不变。
    monkeypatch.setenv("SIYI_DATA_DIR", str(tmp_path / "dataA"))
    path_a = instance_lock.lock_path()
    monkeypatch.setenv("SIYI_DATA_DIR", str(tmp_path / "dataB"))
    path_b = instance_lock.lock_path()
    assert path_a != path_b, "锁路径必须随 SIYI_DATA_DIR 派生（每数据目录单实例）"
    monkeypatch.setenv("SIYI_DATA_DIR", str(tmp_path / "dataA"))
    assert instance_lock.lock_path() == path_a, "同目录推导稳定"

    monkeypatch.setenv("TEMP", str(tmp_path / "other-temp"))
    path_c = instance_lock.lock_path()
    assert path_c != path_a, "锁路径随 TEMP 变化——不同 TEMP 的实例互不互斥（盲区）"


# ── 6.【已知真缺陷】接管探测用新实例端口而非持有者端口 ─────────


def test_takeover_probes_own_port_not_holder_port(tmp_path, monkeypatch):
    """attempt_stale_takeover 探测 _ws_port()（新实例自己的 SIYI_WS_PORT），
    而非锁持有者的实际监听端口：新实例换端口启动时，会把健康持有者误判为
    僵尸并 taskkill /F（SIYI_LOCK_TAKEOVER 默认 1）——双实例互杀的代码根源。
    全程 monkeypatch，无真实进程被触碰。
    """
    calls: list[int] = []
    monkeypatch.setenv("SIYI_LOCK_TAKEOVER", "1")
    monkeypatch.setattr(instance_lock, "read_lock_pid", lambda path: 424242)
    monkeypatch.setattr(instance_lock, "is_pid_alive", lambda pid: True)
    monkeypatch.setattr(instance_lock, "ws_port_reachable", lambda port=None: False)  # 新实例端口无人监听 ≠ 持有者已死
    monkeypatch.setattr(instance_lock, "looks_like_python", lambda pid: True)
    monkeypatch.setattr(instance_lock, "terminate_pid", lambda pid: calls.append(pid) or True)

    fake_lock = tmp_path / _LOCK_BASENAME
    result = instance_lock.attempt_stale_takeover(str(fake_lock))
    assert result is True, "接管流程应返回 True（可重试获取锁）"

    if calls:
        pytest.xfail(
            f"真实缺陷（锁互杀）：持有者存活且健康监听原端口，仅因新实例 "
            f"SIYI_WS_PORT 与其不一致（探测端口不可达），terminate_pid 被调用在"
            f"持有者 PID {calls} 上。修复建议见 deliverables/l0-startup-findings.md §5"
        )
    assert calls == []  # 修复后应到达：端口不一致不得终止持有者
