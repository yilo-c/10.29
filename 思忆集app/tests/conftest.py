import hashlib
import os
import shutil
import tempfile
import time
from pathlib import Path

# ── WebSocket RPC 集成测试 fixture ──
import asyncio
import json
from typing import Any

import aiohttp
import pytest

# 离线基元（PD-ISO-b）：AUTOSTART 子进程环境必须携带的开关名。
# 单一真源在 core.isolation，此处只引用常量，不再复制字面量。
from core.isolation import OFFLINE_ENV_KEY

# 本会话临时目录（pytest_sessionstart 建立，pytest_sessionfinish 只删这一个）
_SESSION_TMP_DIR: str | None = None
# 同根下孤儿会话目录（崩溃残留）的回收阈值
_SESSION_TMP_MAX_AGE_SECONDS = 6 * 60 * 60

# ── 遗留 PyQt6 孤儿测试排除清单（2026-08-29 建立；2026-09-23 审计补充删除波收敛）──
# 思忆集app/ui/ 整个包已在 6aa8a369「M1+M2 收尾总账 · ui测试清理」中删除
# （桌面 UI 现只走 Electron，见 AGENTS.md）。曾 import ui.* 导致模块级 ImportError
# 会中断 pytest **收集阶段整体** 的 6 个孤儿测试（test_assistant_task_ui /
# test_attachment_layer / test_long_term_evolution / test_mobile_context_ui /
# test_status_bar_viewmodel / test_ui_import_smoke）已随 §9-7 取证确认目标消失后删除。
# 仍保留下列排除：其目标模块仍在（workbench.card_renderer / build.smoke_check），
# 仅因 headless venv 有意不装 desktop extras（PyQt6）而 skip，去留待统一决策。
# 注：app_entry/main.py 与 build/smoke_check.py 也仍引用 ui.*，一并待决策
#（见 deliverables/usability-stability-review-2026-08-29.md）。
LEGACY_PYQT_UI_TESTS = (
    "test_smoke_quality_gate.py",
    "test_workbench_card_renderer.py",
    # codex_mission 构造 AppContext 时经 workbench 链拉起 ui 包（PyQt6），
    # 无头 venv 有意不装 desktop extras（AGENTS 口径）——与上列同属环境排除。
    # 待 PyQt6/desktop extras 统一决策后一并恢复。
    "test_codex_mission.py",
)
collect_ignore = list(LEGACY_PYQT_UI_TESTS)


# ── 生产数据泄漏守卫（2026-09-17 事故根除）────────────────────────────
# 背景：本仓曾两次把探针/测试数据写进**生产** 思忆集app/data/（知识图谱被
# 回写、transfer_history.json + transfer_files/ + ai_cache.json 被 WS 探针写
# 入）。`data/**` 在 .gitignore 内 —— `git status` 完全看不见这类写入，只能靠
# 内容/时间戳正向探针发现，所以这里把它固化成一道自动门禁：
#
#   1) 会话级（pytest_sessionstart 采基线，pytest_sessionfinish 总对比）：
#      覆盖模块/会话级 fixture 在 setup 期就构造真实 AppContext 的写穿；
#   2) 测试级（函数级 autouse fixture）：把改动归属到具体测试并打印前后
#      mtime/size，便于精确定位泄漏源。
#
# 成本控制：主判据是 (size, mtime_ns) —— 任何写都会改 mtime；只对 ≤2MiB 的
# 文件追加 md5（transfer_history.json 达 8.5MB，仅 stat 不算哈希）。
#
# 逃逸口：
#   - `@pytest.mark.allow_production_write`：确有必要写生产路径的测试放行
#     （必须在 docstring 说明理由）；
#   - 环境变量 `SIYI_LEAK_GUARD=0`：整体关闭（例如对着真实生产后端跑集成
#     测试时，外部后端进程自身的写入会与本守卫无法区分，会产生噪声）。

_LEAK_GUARD_ENV = "SIYI_LEAK_GUARD"
_APP_ROOT = Path(__file__).resolve().parents[1]
_PROD_DATA_ROOT = _APP_ROOT / "data"

# 守护文件（存在则比对；由测试新建出来同样算泄漏）
_GUARDED_PROD_FILES = (
    _PROD_DATA_ROOT / "knowledge_graph.json",
    _PROD_DATA_ROOT / "info_cache.json",
    _PROD_DATA_ROOT / "ai_cache.json",
    _PROD_DATA_ROOT / "memory_fts.db",
    _PROD_DATA_ROOT / "data" / "transfer_history.json",
)
# 守护目录：只做「条目数 + 目录 mtime」，不做递归（成本可控）
_GUARDED_PROD_DIRS = (_PROD_DATA_ROOT / "data" / "transfer_files",)

# md5 只对小文件做（大文件只 stat，避免每测试哈希 megabytes）
_GUARD_MD5_MAX_BYTES = 2 * 1024 * 1024

_SESSION_PROD_BASELINE: dict[str, Any] | None = None
# 已归属到某个测试的改动（key → 当时的指纹），用于避免同一处泄漏对后续
# 测试重复报红（模块级 fixture 构造的写穿会波及同模块每个测试）。
_GUARD_ATTRIBUTED: dict[str, Any] = {}
# 被 @pytest.mark.allow_production_write 放行的路径：会话级总账不再对其报红
# （放行者是显式声明自己会写生产并经 docstring 说明理由的测试/模块）。
_GUARD_EXEMPT: set[str] = set()


def _leak_guard_enabled() -> bool:
    return os.environ.get(_LEAK_GUARD_ENV, "1").strip().lower() not in ("0", "off", "false", "no")


def _guard_signature(path: Path) -> dict[str, Any] | None:
    """单个受保护路径的轻量指纹。None 表示当前不存在。"""
    try:
        st = path.stat()
    except OSError:
        return None
    sig: dict[str, Any] = {"size": st.st_size, "mtime_ns": st.st_mtime_ns}
    if path.is_dir():
        try:
            sig["entries"] = len(os.listdir(path))
        except OSError:
            sig["entries"] = -1
    elif st.st_size <= _GUARD_MD5_MAX_BYTES:
        try:
            sig["md5"] = hashlib.md5(path.read_bytes()).hexdigest()
        except OSError:
            sig["md5"] = "unreadable"
    return sig


def _guard_snapshot() -> dict[str, dict[str, Any] | None]:
    snap = {str(p): _guard_signature(p) for p in _GUARDED_PROD_FILES}
    for d in _GUARDED_PROD_DIRS:
        snap[str(d)] = _guard_signature(d)
    return snap


def _fmt_sig(sig: dict[str, Any] | None) -> str:
    if sig is None:
        return "不存在"
    parts = [f"size={sig.get('size')}", f"mtime_ns={sig.get('mtime_ns')}"]
    if "entries" in sig:
        parts.append(f"entries={sig.get('entries')}")
    if "md5" in sig:
        parts.append(f"md5={sig.get('md5')}")
    return " ".join(parts)


def _render_leak_report(changes: dict[str, tuple[Any, Any]], where: str) -> str:
    lines = [
        "",
        "=" * 78,
        f"【生产数据泄漏守卫】{where}",
        f"检测到生产数据目录 {_PROD_DATA_ROOT} 下的受保护路径被写入：",
    ]
    for key, (before, after) in changes.items():
        lines.append(f"  · {key}")
        lines.append(f"      before: {_fmt_sig(before)}")
        lines.append(f"      after : {_fmt_sig(after)}")
    lines += [
        "修复方向：测试不要构造回落到生产 data/ 的构件，改用 tmp_path 或",
        "SIYI_DATA_DIR + data_base_dir= 双保险（参考 tests/test_r2a_context_isolation_20260917.py）。",
        "确有必要写生产路径者，请在 docstring 说明理由并加 @pytest.mark.allow_production_write。",
        "=" * 78,
        "",
    ]
    return "\n".join(lines)


def _is_content_change(before: dict[str, Any] | None, after: dict[str, Any] | None) -> bool:
    """指纹差异是否属于**内容层面**改动（而非仅 mtime 变化）。

    会话级总账只对内容改动报红：`test_kg_probe_pd_20260917.py` 这类"快照 →
    会话末字节回滚"的红线 fixture 会改 mtime 但内容逐字节一致，属于净效果为零的
    写入。mtime-only 差异仍会打印告警供人判断，但不改变退出码。
    """
    if before is None or after is None:
        return True  # 出现 / 消失都算内容变化
    if before.get("size") != after.get("size"):
        return True
    if "md5" in before or "md5" in after:
        return before.get("md5") != after.get("md5")
    if "entries" in before or "entries" in after:
        return before.get("entries") != after.get("entries")
    return False


def pytest_configure(config):
    """注册泄漏守卫的逃逸口 marker（pyproject 未声明时也可用，不产生 unknown mark 警告）。"""
    config.addinivalue_line(
        "markers",
        "allow_production_write: 允许该测试写生产 data/（须在 docstring 说明理由；生产数据泄漏守卫放行）",
    )


@pytest.fixture(autouse=True)
def _production_data_leak_guard(request):
    """函数级 autouse：测试结束后比对生产数据指纹，被改则失败并打印前后 mtime/size。"""
    global _GUARD_ATTRIBUTED
    if not _leak_guard_enabled() or _SESSION_PROD_BASELINE is None:
        yield
        return
    if request.node.get_closest_marker("allow_production_write"):
        # 逃逸口：该测试（或其所在模块）已声明会写生产路径。除了自身放行，
        # 还把这些路径从**会话级总账**里豁免——因为模块/会话级 fixture 的
        # 回滚写入发生在函数级 fixture teardown 之后，只能靠会话级兜底。
        _GUARD_EXEMPT.update(_SESSION_PROD_BASELINE)
        yield
        return

    yield

    after = _guard_snapshot()
    fresh: dict[str, tuple[Any, Any]] = {}
    for key, sig in after.items():
        base = _SESSION_PROD_BASELINE.get(key)
        if sig == base:
            continue
        if _GUARD_ATTRIBUTED.get(key) == sig:
            # 同一处改动已归属到更早的测试（如模块级 fixture 构造期写穿）
            continue
        fresh[key] = (base, sig)
    if fresh:
        _GUARD_ATTRIBUTED.update({k: v[1] for k, v in fresh.items()})
        pytest.fail(
            _render_leak_report(fresh, f"测试 {request.node.nodeid} 期间"),
            pytrace=False,
        )


SIYI_WS_URL = os.environ.get("SIYI_WS_URL", "ws://127.0.0.1:18765/ws")
# RPC 接收超时：部分方法（如 inbox.add 触发 AI 分类）在机器负载高时
# 耗时可达 30s+，30s 超时线过紧易抖；放宽到 60s 仅拉长等待不掩盖真实失败
SIYI_RPC_TIMEOUT = float(os.environ.get("SIYI_RPC_TIMEOUT", "60"))


def _auth_headers() -> dict[str, str]:
    """解析 WS 认证 token（V2.1 批次1#4 兼容性迁移）。

    后台已强制 WS 认证，无 token 的 WS 连接会被拒。解析优先级：
      SIYI_DIAG_TOKEN → SIYI_DATA_DIR/auth_token → 默认 data/data/auth_token
    """
    from pathlib import Path

    tok = os.environ.get("SIYI_DIAG_TOKEN", "").strip()
    if not tok:
        candidates: list[Path] = []
        data_env = os.environ.get("SIYI_DATA_DIR", "").strip()
        if data_env:
            candidates.append(Path(data_env) / "auth_token")
        candidates.append(Path(__file__).resolve().parents[1] / "data" / "data" / "auth_token")
        for p in candidates:
            try:
                if p.is_file():
                    t = p.read_text(encoding="utf-8").strip()
                    if t:
                        tok = t
                        break
            except Exception:  # noqa: BLE001
                continue
    return {"Authorization": f"Bearer {tok}"} if tok else {}


@pytest.fixture
async def ws_session():
    """建立 WebSocket 连接，测试结束后自动关闭。

    M2.1 R4 兼容：必须声明 client_version（与桌面 backend-relay 同源常量
    '1.2.0'），否则按 legacy_client 处理——R4 破坏性方法直接回 -32010，
    rpc_approved 的「pending → approve → 重放」审批链路永远走不到。
    """
    url = SIYI_WS_URL
    sep = "&" if "?" in url else "?"
    async with aiohttp.ClientSession() as session:
        async with session.ws_connect(f"{url}{sep}client_version=1.2.0", timeout=10, headers=_auth_headers()) as ws:
            yield ws


@pytest.fixture
def rpc(ws_session):
    """便捷 RPC 调用封装。返回 async callable: rpc(method, params) -> dict。"""
    _counter = [0]

    async def _call(method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        _counter[0] += 1
        req = {"jsonrpc": "2.0", "id": _counter[0], "method": method, "params": params or {}}
        await ws_session.send_json(req)
        while True:
            msg = await asyncio.wait_for(ws_session.receive(), timeout=SIYI_RPC_TIMEOUT)
            if msg.type != aiohttp.WSMsgType.TEXT:
                continue
            data = json.loads(msg.data)
            if data.get("type") == "event" or "event_type" in data:
                continue
            if data.get("id") == _counter[0]:
                result = data.get("result")
                if isinstance(result, dict) and result.get("type") == "chunk":
                    chunks = []
                    while True:
                        msg2 = await asyncio.wait_for(ws_session.receive(), timeout=SIYI_RPC_TIMEOUT)
                        if msg2.type != aiohttp.WSMsgType.TEXT:
                            continue
                        d2 = json.loads(msg2.data)
                        if d2.get("id") != _counter[0]:
                            continue
                        r2 = d2.get("result", {})
                        if r2.get("type") == "chunk":
                            chunks.append(r2.get("data", ""))
                        elif r2.get("type") == "done":
                            return {"jsonrpc": "2.0", "id": _counter[0], "result": "".join(chunks)}
                        else:
                            return d2
                return data

    return _call


@pytest.fixture
def rpc_approved(rpc):
    """V2.1 批次2：执行 RPC；若返回 pending（R4 破坏性方法需审批），
    先调 sandbox.approve 批准，再返回最终执行结果。

    模拟真实前端"审批后执行"链路：dispatch → pending → approve → 重放执行。
    """

    async def _call(method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        resp = await rpc(method, params)
        result = resp.get("result")
        if isinstance(result, dict) and result.get("pending"):
            pid = result.get("pending_id")
            assert pid, f"{method} pending 响应缺少 pending_id: {result}"
            ack = await rpc("sandbox.approve", {"pending_id": pid})
            return ack
        return resp

    return _call


def _backend_reachable() -> bool:
    """探测 SIYI_WS_URL 对应端口是否可连。"""
    import socket
    from urllib.parse import urlparse

    parsed = urlparse(SIYI_WS_URL)
    host = parsed.hostname or "127.0.0.1"
    port = parsed.port or 18765
    try:
        with socket.create_connection((host, port), timeout=2):
            return True
    except OSError:
        return False


# ── P2-3：集成测试自包含（SIYI_INTEGRATION_AUTOSTART=1 时自启临时后端） ──

_AUTOSTART_STATE: dict = {}


def _shutdown_autostart() -> None:
    proc = _AUTOSTART_STATE.get("proc")
    if proc is None:
        return
    try:
        proc.terminate()
        proc.wait(timeout=10)
    except Exception:  # noqa: BLE001
        try:
            proc.kill()
        except Exception:  # noqa: BLE001
            pass


def _maybe_autostart_backend() -> bool:
    """SIYI_INTEGRATION_AUTOSTART=1 时，自启会话级临时后端。

    隔离语义：只要 AUTOSTART 打开就**始终**使用随机端口 + 临时数据目录的
    临时后端，即使本地真实后端（18765 观察期实例）在线也**不连它**——
    避免写入类/破坏性集成测试污染真实数据、以及被真实实例拖慢。
    会话结束 atexit 清理。返回 True 表示临时后端已就绪（或已在运行）。
    """
    global SIYI_WS_URL
    if os.environ.get("SIYI_INTEGRATION_AUTOSTART") != "1":
        return False
    if _AUTOSTART_STATE.get("proc") is not None:
        return True

    import atexit
    import socket
    import subprocess
    import sys
    import tempfile
    import time
    from pathlib import Path

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    data_dir = tempfile.mkdtemp(prefix="siyi-it-")
    # E2E 桥隔离（2026-09-24，E2 取证）：AUTOSTART 若不设 SIYI_DESKNOTES_DATA_DIR，
    # 写类测试会经 DeskNotesNoteAdapter 落进 %LOCALAPPDATA%\desk-notes\siyi-notes 真实用户库。
    # 给独立 temp 目录并预建 notes/（满足 P0-2 权威判据，缺子目录会 raise 而非静默回退生产）。
    desknotes_dir = tempfile.mkdtemp(prefix="siyi-it-desknotes-")
    Path(desknotes_dir, "notes").mkdir(exist_ok=True)
    env = dict(os.environ)
    env["SIYI_WS_HOST"] = "127.0.0.1"
    env["SIYI_WS_PORT"] = str(port)
    env["SIYI_DATA_DIR"] = data_dir
    env["SIYI_DESKNOTES_DATA_DIR"] = desknotes_dir
    env["SIYI_LOCK_TAKEOVER"] = "0"  # 测试环境禁止接管他人锁
    # PD-ISO-b（2026-09-18）：离线声明必须覆盖**全部**出网通道，而不只是
    # headless_main 的 RSS/autonomy。既往 ai_client / kernel_ai 无视该开关，
    # 导致 AUTOSTART 集成测试真实联网调 DeepSeek 烧配额。键名引用
    # core.isolation 基元常量（单一真源，杜绝此处写一个、那边读另一个）。
    env[OFFLINE_ENV_KEY] = "1"  # 测试后端离线：跳过 RSS/autonomy/LLM 出网
    app_dir = str(Path(__file__).resolve().parents[1])
    try:
        proc = subprocess.Popen(
            [sys.executable, "-m", "backend.headless_main"],
            cwd=app_dir,
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except Exception:  # noqa: BLE001
        return False

    deadline = time.time() + 90
    while time.time() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=1):
                break
        except OSError:
            if proc.poll() is not None:
                return False
            time.sleep(1)
    else:
        proc.kill()
        return False

    SIYI_WS_URL = f"ws://127.0.0.1:{port}/ws"
    os.environ["SIYI_WS_URL"] = SIYI_WS_URL

    # V2.1 批次1#4：临时后端已强制认证 → 从临时数据目录读取 auth_token，
    # 注入 SIYI_DIAG_TOKEN，供 _auth_headers() / 集成测试握手使用。
    token_relay_timeout = time.time() + 30
    token_read = None
    token_path = os.path.join(data_dir, "auth_token")
    while time.time() < token_relay_timeout:
        try:
            if Path(token_path).is_file():
                token_read = Path(token_path).read_text(encoding="utf-8").strip()
                if token_read:
                    break
        except Exception:  # noqa: BLE001
            pass
        if proc.poll() is not None:
            break
        time.sleep(0.3)
    if token_read:
        os.environ["SIYI_DIAG_TOKEN"] = token_read
    else:
        # 导引发红（fail-not-skip），避免集成测试拿到空 token 静默 401 全挂
        raise RuntimeError(f"AUTOSTART 后端未能读取到认证 token 文件 {token_path}（后端强制认证→依赖它）")

    _AUTOSTART_STATE.update({"proc": proc, "port": port, "data_dir": data_dir})
    atexit.register(_shutdown_autostart)
    return True


def pytest_collection_modifyitems(config, items):
    """后端未运行时自动跳过依赖 WS 连接的集成测试。

    CI 环境无后端运行，避免 test_integration_* 因连接失败报 ERROR；
    本地后端运行中时行为不变。设 SIYI_INTEGRATION_REQUIRED=1 可强制
    要求后端在场（不可达时直接失败而非跳过）；设
    SIYI_INTEGRATION_AUTOSTART=1 则自动拉起临时后端使集成测试自包含（P2-3）。
    """
    ws_items = [item for item in items if {"ws_session", "rpc"} & set(getattr(item, "fixturenames", []))]
    if not ws_items:
        return
    # 隔离优先：AUTOSTART=1 时先自启临时后端（即使真实后端在线也不连）。
    autostart_requested = os.environ.get("SIYI_INTEGRATION_AUTOSTART") == "1"
    if _maybe_autostart_backend():
        return  # 隔离临时后端已就绪
    if autostart_requested:
        # T3.2 修复（fail-not-skip）：临时后端起不来 = 后端代码/环境损坏，
        # 必须让 CI 失败而非静默跳过——原实现跳过会掩盖后端被改坏的回归
        # （P0 类故障第一受害者是自己，CI 漏报等于没测）。
        # 注：当前 CI 单进程跑 AUTOSTART（python-tests.yml:99 无 -n，无 xdist 并发），
        # 故不存在"并发拉起超时假失败"，直接 fail-not-skip 即可；若未来引入
        # xdist，需文件锁会话级单例保证后端只拉起一次，再 fail-not-skip。
        pytest.exit(
            "SIYI_INTEGRATION_AUTOSTART=1 但隔离临时后端未能启动（疑似后端代码/环境损坏），集成测试 fail-not-skip：不再静默跳过以免 CI 漏报后端损坏",
            returncode=1,
        )
    if _backend_reachable():
        return
    if os.environ.get("SIYI_INTEGRATION_REQUIRED") == "1":
        pytest.exit(f"SIYI_INTEGRATION_REQUIRED=1 但后端不可达: {SIYI_WS_URL}", returncode=1)
    skip_marker = pytest.mark.skip(reason=f"后端未运行（{SIYI_WS_URL} 不可达），自动跳过 WS 集成测试")
    for item in ws_items:
        item.add_marker(skip_marker)


# ── 原有配置 ──


def _cleanup_legacy_tempdir():
    """清理历史上落在 C: 盘默认位置的 pytest 临时目录。

    只针对 **系统 temp 下** 的 `pytest-of-<user>` 旧产物，绝不触碰仓库工作树。
    进程/系统防护仍持有锁时删除静默失败——这是可接受的（尽力而为）。
    """
    legacy = os.path.join(
        os.environ.get("LOCALAPPDATA", "C:\\Users\\Administrator\\AppData\\Local"),
        "Temp",
        "pytest-of-Administrator",
    )
    if os.path.isdir(legacy):
        try:
            shutil.rmtree(legacy, ignore_errors=True)
        except Exception:
            pass  # best-effort only


def _system_tempdir() -> str:
    """返回真正的系统临时目录（不受本 conftest 自身重定向影响）。"""
    saved, tempfile.tempdir = tempfile.tempdir, None
    try:
        return tempfile.gettempdir()
    finally:
        tempfile.tempdir = saved


def pytest_sessionstart(session):
    """把 pytest/标准库临时目录指向 **系统 temp 下的本会话专属子目录**。

    原实现把 TMPDIR/TMP/TEMP + `tempfile.tempdir` 重定向到仓库内的
    `.pytest-tmp-root`，代价是：

    1. pytest 的 basetemp 保留策略（keep-last-3）每次 session start 都要在
       该目录上递归 stat/删除——实测已累积 11k+ 条目 / 360MB，拖慢每个测试
       会话，且历史上出现过符号链接 ACL 损坏导致的 `PermissionError`
       与 pytest-cov INTERNALERROR（见 deliverables/思忆集全栈评审报告-2026-08-23.md）；
    2. jieba 缓存、构建日志、探针脚本等旁路产物一并落进仓库工作树（污染 +
       误删生产数据的危险）。

    现在落在 `<系统 temp>/siyi-pytest-tmp/session-*`：由 OS/磁盘清理负责回收，
    会话结束只删 **本会话自己** 那一个目录，不再对任何历史/共享大目录做递归
    删除。仅顺手回收同根下超过 `_SESSION_TMP_MAX_AGE_SECONDS` 的孤儿会话目录
    （崩溃残留，扫描深度为顶层一项）。设 `SIYI_PYTEST_TMP_ROOT` 可覆盖根位置。
    """
    global _SESSION_TMP_DIR
    root = os.environ.get("SIYI_PYTEST_TMP_ROOT", "").strip() or os.path.join(_system_tempdir(), "siyi-pytest-tmp")
    os.makedirs(root, exist_ok=True)
    _prune_orphan_session_dirs(root)
    _SESSION_TMP_DIR = tempfile.mkdtemp(prefix=f"session-pid{os.getpid()}-", dir=root)
    # 重定向 TMP/TEMP 让子进程（自启后端、subprocess 类测试）也落在会话目录内
    os.environ["TMPDIR"] = _SESSION_TMP_DIR
    os.environ["TMP"] = _SESSION_TMP_DIR
    os.environ["TEMP"] = _SESSION_TMP_DIR
    tempfile.tempdir = _SESSION_TMP_DIR
    # Best-effort cleanup of legacy temp dir（系统 temp，不涉仓库）
    _cleanup_legacy_tempdir()
    # 生产数据泄漏守卫：会话起点采基线（覆盖模块/会话级 fixture 的 setup 期写穿）
    global _SESSION_PROD_BASELINE
    if _leak_guard_enabled():
        _SESSION_PROD_BASELINE = _guard_snapshot()


def _prune_orphan_session_dirs(root: str) -> None:
    """删除同根下明显已无人使用的孤儿会话目录（崩溃残留），深度仅顶层一项。"""
    cutoff = time.time() - _SESSION_TMP_MAX_AGE_SECONDS
    try:
        entries = list(os.scandir(root))
    except OSError:
        return
    for entry in entries:
        try:
            if not entry.name.startswith("session-") or not entry.is_dir():
                continue
            if entry.stat().st_mtime < cutoff:
                shutil.rmtree(entry.path, ignore_errors=True)
        except OSError:
            continue


def pytest_sessionfinish(session, exitstatus):
    """只回收本会话创建的临时目录；锁未释放时留给系统 temp 回收。"""
    global _SESSION_TMP_DIR
    # 生产数据泄漏守卫：会话级总对比（兜住 fixture teardown / 未被归属的写入）
    if _SESSION_PROD_BASELINE is not None:
        after = _guard_snapshot()
        residual = {k: (_SESSION_PROD_BASELINE.get(k), sig) for k, sig in after.items() if sig != _SESSION_PROD_BASELINE.get(k)}
        exempted = {k: v for k, v in residual.items() if k in _GUARD_EXEMPT}
        residual = {k: v for k, v in residual.items() if k not in _GUARD_EXEMPT}
        if exempted:
            print("\n[生产数据泄漏守卫] 以下路径被 @pytest.mark.allow_production_write 放行，不再计入会话总账：")
            for key, (before, sig) in exempted.items():
                print(f"  · {key}\n      before: {_fmt_sig(before)}\n      after : {_fmt_sig(sig)}")
        if residual:
            content_changes = {k: v for k, v in residual.items() if _is_content_change(*v)}
            mtime_only = {k: v for k, v in residual.items() if k not in content_changes}
            if mtime_only:
                print("\n[生产数据泄漏守卫] 告警：以下路径 mtime 变化但**内容指纹一致**（疑似写入后字节回滚 / 同内容重写），不计入失败：")
                for key, (before, sig) in mtime_only.items():
                    print(f"  · {key}\n      before: {_fmt_sig(before)}\n      after : {_fmt_sig(sig)}")
            if content_changes:
                print(_render_leak_report(content_changes, "会话级总账（本次 pytest 会话使生产数据内容发生变化）"))
                print(
                    "注：若本次会话是**对着真实生产后端**跑的集成测试，外部后端进程自身的"
                    f"写入同样会落到这里；此时可用 {_LEAK_GUARD_ENV}=0 关闭守卫。\n"
                )
                if session.exitstatus == 0:
                    session.exitstatus = 1
    if _SESSION_TMP_DIR and os.path.isdir(_SESSION_TMP_DIR):
        shutil.rmtree(_SESSION_TMP_DIR, ignore_errors=True)
    _SESSION_TMP_DIR = None
