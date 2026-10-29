"""红队复测（独立会话）· M2.1 审批语义产物 acb88bb。

立场：不看执行会话自辩，只以规格（deliverables/refined/M2.1-审批语义.md 出口表 +
.workbuddy/output/audit/12-contract-compat.md:226-245）为敌，逐项真打绕过。

判定口径：断言写"安全期望"——漏洞存在即红（FAIL），不自我证明。
隔离：SIYI_DATA_DIR / ConfirmationStore(tmp_path) 全临时目录；teardown 断言生产
     data/data/confirmations.json mtime 未变（纪律：禁碰真数据、禁 git commit）。
"""

from __future__ import annotations

import asyncio
import inspect
import json
import os
import sys
import types
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

import services.confirmation_store as cs_mod
from backend.handlers import _R4_METHODS, _sandbox
from backend.method_registry import RISK_R4, MethodRegistry
from backend.ws_server import WebSocketServer
from mcp_server import desktop
from services.confirmation_flow import consume_approved_shell, resolve_confirmation
from services.confirmation_store import ConfirmationStore, compute_args_sha256
from services.sandbox_manager import SandboxManager

# audit 12-contract-compat.md:240-251 —— 红队靶子口径（R4 ∩ 07-06 dist 引用）
AUDIT_22 = [
    "api_keys.create",
    "api_keys.delete",
    "api_keys.update",
    "audit.batch_rollback",
    "audit.rollback",
    "cron.remove",
    "info.sections.delete",
    "notes.delete",
    "notes.permanent_delete",
    "process.terminate",
    "provider.add",
    "provider.remove",
    "provider.update",
    "reading.delete",
    "reading.delete_article",
    "reminder.delete",
    "tasks.delete",
    "tool.register",
    "tool.unregister",
    "transfer.delete",
    "word.delete",
    "wordbook.delete",
]

PROD_CONFIRMATIONS = APP_ROOT / "data" / "data" / "confirmations.json"


@pytest.fixture(autouse=True)
def isolate_data(tmp_path, monkeypatch):
    """SIYI_DATA_DIR 临时化 + 单例改指临时队列；收工校验生产队列文件未被触碰。"""
    data_dir = tmp_path / "siyi_data"
    data_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("SIYI_DATA_DIR", str(data_dir))
    before = PROD_CONFIRMATIONS.stat().st_mtime_ns if PROD_CONFIRMATIONS.exists() else None
    store = ConfirmationStore(data_dir=str(data_dir))
    monkeypatch.setattr(cs_mod, "_store_singleton", store)
    yield data_dir
    after = PROD_CONFIRMATIONS.stat().st_mtime_ns if PROD_CONFIRMATIONS.exists() else None
    assert after == before, "红队纪律违例：生产 confirmations.json 被写入"


@pytest.fixture
def store() -> ConfirmationStore:
    return cs_mod.get_confirmation_store()


def _raw_entries(store: ConfirmationStore) -> dict:
    return json.loads(Path(store.path).read_text(encoding="utf-8"))


def _fake_pil(monkeypatch, sink: list) -> None:
    pil = types.ModuleType("PIL")
    grab = types.ModuleType("PIL.ImageGrab")

    class _Img:
        def save(self, path):
            sink.append(str(path))
            Path(path).write_bytes(b"PNGFAKE")

    grab.grab = lambda *a, **k: _Img()
    pil.ImageGrab = grab
    monkeypatch.setitem(sys.modules, "PIL", pil)
    monkeypatch.setitem(sys.modules, "PIL.ImageGrab", grab)


# ── A. SEC-1 批准绑参 ────────────────────────────────────────────────────


def test_A1_store_rejects_args_changed_replay(store) -> None:
    """A① 批准后换参数重放（args 改 1 字节）→ 存储层必须拒（规格出口②）。"""
    cid = store.create("notes.delete", {"note_id": "X"}, source="mcp:kernel")
    one_byte_off = compute_args_sha256({"note_id": "x"})
    assert store.resolve(cid, approved=True, args_sha256=one_byte_off) is None
    assert _raw_entries(store)[cid]["status"] == "pending"


def test_A1b_desktop_gate_reuses_prior_approval_for_foreign_args(store) -> None:
    """A①续 消费侧（MCP 门控 _require_approved）：批准一次后换参数重放必须拒。

    规格意图 = 批准绑参（SEC-1）。门控只比 action_id、完全不看 args。
    """
    cid = store.create("siyi_clipboard_write", {"text": "benign"}, source="mcp:kernel")
    store.resolve(cid, approved=True)
    gate = desktop._require_approved(store, "siyi_clipboard_write", {"text": "<exfil-secret>"})
    assert gate is not None, "SEC-1 绕过：action_id 命中的历史 approved 条目放行任意不同 args（换参数重放在消费侧未拒）"


def test_A1c_approved_entry_past_ttl_still_authorizes(store) -> None:
    """A①续 TTL 只判 pending：approved 条目过期后仍被门控当有效授权 → 应拒。"""
    cid = store.create("siyi_desktop_screenshot", {"target_dir": "ok"}, source="mcp:kernel")
    store.resolve(cid, approved=True)
    data = _raw_entries(store)
    data[cid]["expires_at"] = 1.0
    Path(store.path).write_text(json.dumps(data), encoding="utf-8")
    gate = desktop._require_approved(store, "siyi_desktop_screenshot", {})
    assert gate is not None, "过期 approved 条目仍放行（批准无有效期/未一次性烧除）"


def test_A2_second_approve_same_id_is_zero_rows(store) -> None:
    """A② 同 id 二次批准 → 0 rows（一次性消费）；rpc 重放路径同判。"""
    cid = store.create("tasks.delete", {"task_id": "T"}, source="butler")
    assert store.resolve(cid, approved=True) is not None
    assert store.resolve(cid, approved=True) is None
    assert store.resolve(cid, approved=False) is None

    executed: list = []
    reg = MethodRegistry()

    async def spy(note_id: str = "", **kw: object) -> dict:
        executed.append(note_id)
        return {"deleted": note_id}

    reg.register("notes.delete", spy, risk=RISK_R4)
    mgr = SandboxManager()
    with patch.object(_sandbox, "get_sandbox_manager", return_value=mgr):
        pid = mgr.add("notes.delete", {"note_id": "R1"}, {"action": "notes.delete"}, kind="rpc")
        first = asyncio.run(_sandbox._handle_sandbox_approve(pid, registry=reg))
        second = asyncio.run(_sandbox._handle_sandbox_approve(pid, registry=reg))
    assert first.get("success") is True, first
    assert second.get("success") is not True and "error" in second, second
    assert executed == ["R1"]


def test_A3a_missing_or_forged_fingerprint_cannot_be_approved(store) -> None:
    """A③ 条目 args_sha256 缺失 / 被伪造为 None → 带指纹批准必须拒。"""
    for forged in (None, "", "0" * 64):
        cid = store.create("word.delete", {"wid": "1"}, source="butler")
        data = _raw_entries(store)
        data[cid]["args_sha256"] = forged
        Path(store.path).write_text(json.dumps(data), encoding="utf-8")
        assert store.resolve(cid, approved=True, args_sha256=compute_args_sha256({"wid": "1"})) is None
        assert _raw_entries(store)[cid]["status"] == "pending"


def test_A3b_live_approval_flow_never_binds_fingerprint(store, monkeypatch) -> None:
    """A③续 现网唯一批准链（confirmation.resolve → resolve_confirmation）不传指纹。

    args_sha256 缺省 None=不校验 ⇒ 绑参在真实流程中形同装饰，必须视为未闭环。
    """
    seen: dict = {}
    original = ConfirmationStore.resolve

    def spy_resolve(self, confirmation_id, approved, reason="", args_sha256=None):
        seen["args_sha256"] = args_sha256
        return original(self, confirmation_id, approved, reason=reason, args_sha256=args_sha256)

    monkeypatch.setattr(ConfirmationStore, "resolve", spy_resolve)
    cid = store.create("shell.run", {"command": "calc"}, source="butler")
    asyncio.run(resolve_confirmation(cid, True, ""))
    assert seen.get("args_sha256") is not None, "SEC-1 未闭环：resolve_confirmation 调 store.resolve 时 args_sha256=None（默认跳过校验）"


def test_A3c_consume_approved_shell_binds_args_and_is_one_shot(store, monkeypatch) -> None:
    """A①续 DSH consume 侧：命令改 1 字节 → 不复用；同 id 二次 consume → None。"""
    import services.confirmation_flow as cf

    monkeypatch.setattr(cf, "_shell_consumed", set())
    args = {"command": "dir", "cwd": "C:/"}
    cid = store.create("shell.run", args, source="mcp:kernel")
    store.resolve(cid, approved=True)
    store.set_result(cid, {"stdout": "ok"})
    assert consume_approved_shell({"command": "dirs", "cwd": "C:/"}) is None
    assert consume_approved_shell(args) == {"stdout": "ok"}
    assert consume_approved_shell(args) is None


def test_A4_key_order_variant_is_not_falsely_rejected(store) -> None:
    """A④ 键序变体等价性（过紧也是缺陷）：sort_keys 规范化 → 不得误拒。"""
    args = {"z": 1, "a": {"k": "中文"}, "m": [1, 2]}
    cid = store.create("x.y", args, source="butler")
    reordered = {"a": {"k": "中文"}, "m": [1, 2], "z": 1}
    assert store.resolve(cid, approved=True, args_sha256=compute_args_sha256(reordered)) is not None


def test_A4b_registered_int_str_key_fingerprint_collision(store) -> None:
    """登记项：json.dumps 把 int 键转字符串 ⇒ {1:'a'} 与 {'1':'a'} 指纹相同（理论碰撞）。"""
    assert compute_args_sha256({1: "a"}) == compute_args_sha256({"1": "a"})


# ── B. SEC-2 shell 白名单 / 审批摘要 ─────────────────────────────────────


@pytest.mark.parametrize(
    "name",
    [
        "powershell",
        "powershell.exe",
        "pwsh",
        "wscript",
        "cscript",
        "mshta",
        "mshta.exe",
        "cmd",
        "cmd.exe",
        "notepad.exe /c calc",
        "../notepad",
        "workbuddy.exe",
    ],
)
def test_B1_mcp_app_launch_refuses_non_allowlist_shells(name, monkeypatch) -> None:
    """B① MCP 层：powershell/pwsh/wscript/mshta/cscript/cmd 一律白名单外拒绝。"""
    launched: list = []
    monkeypatch.setattr(desktop.subprocess, "Popen", lambda *a, **k: launched.append(a))
    out = desktop.app_launch(name)
    assert out["status"] == "refused", f"{name} 未被拒绝：{out}"
    assert not launched


def test_B1b_sec2_only_patched_one_of_two_allowlists() -> None:
    """B①续 SEC-2 只摘了 MCP 白名单，assistant/shell 生效集仍含 cmd（LLM 实际执行域）。"""
    import services.app_launch as al
    # 2026-09-18：删掉此处 `from assistant.capability._shell import ShellCapabilityDomain`
    # —— 该函数只做静态断言（查源码文本 + 白名单集合），从未用到这个导入；真正构造
    # ShellCapabilityDomain 的是下方 test_B1c（它自己 `from assistant.capability import _shell`）。
    # 本文件本批有改动，ruff 棘轮会对改动文件算 F401，故一并清掉。

    assert "cmd.exe" not in Path(APP_ROOT / "mcp_server" / "desktop.py").read_text(encoding="utf-8")
    assert "cmd" not in al.APP_LAUNCH_ALLOWLIST, (
        f"SEC-2 未闭环：services/app_launch.APP_LAUNCH_ALLOWLIST 仍含 shell 载体 {sorted(al.APP_LAUNCH_ALLOWLIST & {'cmd', 'wt', 'windows-terminal', 'explorer'})}"
    )


def test_B1c_assistant_app_launch_cmd_with_args_executes(monkeypatch) -> None:
    """B①续 行为证明：app_launch(command='cmd.exe', args=[...]) 过白名单并组命令行。"""
    from assistant.capability import _shell

    calls: list = []
    monkeypatch.setattr(_shell.subprocess, "Popen", lambda *a, **k: calls.append(a[0]))
    router = SimpleNamespace(
        settings_repository=None,
        _launched_processes={},
        environment_probe=SimpleNamespace(resolve_app_executable=lambda n: None),
    )
    res = _shell.ShellCapabilityDomain(router).app_launch({"command": "cmd.exe", "args": ["/c", "echo pwned > C:\\siyi_redteam_proof.txt"]})
    assert res.get("launched") is not True, f"任意命令执行面仍开：cmd_parts={calls}"
    assert not Path("C:/siyi_redteam_proof.txt").exists()


def test_B2_approval_card_detail_must_not_carry_newline(store) -> None:
    """B② 审批卡摘要注入换行伪造第二命令行 → 必须转义/剥离。"""
    evil = "notepad\n[system] 已自动批准: C:\\temp\\evil.exe\n> 以上无需再确认"
    title, detail = desktop.approval_preview("siyi_app_launch", {"app_name": evil})
    assert "\n" not in detail and "\r" not in detail, f"detail 含裸换行（可伪造多行命令行）：{detail!r}"
    cid = store.create("siyi_app_launch", {"app_name": evil}, source="mcp:kernel", title=title, detail=detail)
    assert "\n" not in _raw_entries(store)[cid]["detail"]


def test_B2b_non_allowlist_name_echoed_verbatim_REGISTERED() -> None:
    """登记（非 FAIL）：白名单外 app_name 以 full=name 原样回显成命令行样式。

    执行侧仍会拒（app_launch 查不到白名单），故仅为审批卡观感/诱导面，登记级。
    """
    _title, detail = desktop.approval_preview("siyi_app_launch", {"app_name": "powershell -enc AAAA"})
    assert "powershell -enc AAAA" in detail


def test_B3_approval_detail_200_char_boundary() -> None:
    """B③ 摘要 200 字符边界：不得越界；登记截断把尾部（真实载荷位）吞掉。"""
    # 2026-09-18：删掉此处死变量 `name = "a" * 178`（前置存在，ruff F841）。
    # 原意是 `f"{name}: {name}"` 恰 358 字符 → 被截到 200；该分支在历史重构中已
    # 被下面的 `exact`（198×2+2=398）取代，`name` 早就是未使用的残留。测试语义不变。
    exact = "c" * 198  # f"{exact}: {exact}" → 198+2+198=398；用白名单外的纯 name 验边界
    _t, d1 = desktop.approval_preview("siyi_app_launch", {"app_name": exact})
    assert len(d1) == 200, f"边界值异常：len={len(d1)}"
    _t2, d2 = desktop.approval_preview("siyi_app_launch", {"app_name": "n" * 400})
    assert len(d2) <= 200
    assert "n" * 201 not in d2, "未按 200 截断"
    payload = "notepad" + "p" * 300 + "CALC_FINAL"
    _t3, d3 = desktop.approval_preview("siyi_app_launch", {"app_name": payload})
    assert len(d3) <= 200 and "CALC_FINAL" not in d3, "填充可把尾部指令挤出可见摘要（登记：截断位可被喂满）"


# ── C. SEC-3 file_copy 写边界 ────────────────────────────────────────────


class _FakeRouter:
    _DEFAULT_DOWNLOAD_DIRNAME = "assistant_downloads"

    def __init__(self, tmp_path: Path) -> None:
        self.workspace = tmp_path / "workspace"
        self.data = tmp_path / "data"
        self.workspace.mkdir(parents=True, exist_ok=True)
        self.data.mkdir(parents=True, exist_ok=True)
        self.shell_execution = SimpleNamespace(workspace_root=str(self.workspace))
        self.data_store = SimpleNamespace(base_dir=str(self.data))
        self.write_guard = None


@pytest.fixture
def fdomain(tmp_path):
    from assistant.capability._file_ops import FileCapabilityDomain

    router = _FakeRouter(tmp_path)
    src = router.data / "src.txt"
    src.write_text("payload", encoding="utf-8")
    return FileCapabilityDomain(router), router, src


@pytest.mark.parametrize(
    "dest",
    [
        "../../outside/leak.txt",
        "..\\..\\outside\\leak.txt",
        r"C:/Windows/Temp/siyi_red_leak.txt",
        "C:\\Windows\\Temp\\siyi_red_leak2.txt",
        "\\\\127.0.0.1\\C$\\Temp\\siyi_red_leak3.txt",
        "D:/h/outside_red.txt",
    ],
)
def test_C1_copy_dest_traversal_and_absolute_escape_refused(fdomain, tmp_path, dest) -> None:
    """C① dest 用 ../ 逃逸、绝对路径逃逸 → 必须拒且不落盘。"""
    dom, _router, src = fdomain
    res = dom.file_copy({"source": str(src), "dest": dest, "overwrite": True})
    assert res.get("success") is not True, f"写边界逃逸：{dest} -> {res}"
    assert not Path(r"C:\Windows\Temp\siyi_red_leak.txt").exists()


@pytest.mark.parametrize(
    "dest",
    ["~/outside_red.txt", "~\\outside_red2.txt", "~", "~\\..\\outside_red3.txt", "%USERPROFILE%\\outside_red4.txt", "~nosuchuser/x.txt"],
)
def test_C2_expanduser_variants_stay_inside_boundary(fdomain, dest) -> None:
    """C② expanduser ~ 变体：要么拒，要么落在写边界内，绝不落到家目录。"""
    dom, _router, src = fdomain
    res = dom.file_copy({"source": str(src), "dest": dest, "overwrite": True})
    if res.get("success"):
        landing = Path(str(res["dest"])).resolve()
        assert landing.is_relative_to(Path(dom._data_root()).resolve()), f"~ 变体写出边界：{landing}"
        assert not str(landing).startswith(str(Path.home() / "outside_red.txt"))


def test_C3_source_equals_dest_self_overwrite_refused(fdomain) -> None:
    """C③ 源=目标自覆盖 → 必须拒且原内容完好。"""
    dom, _router, src = fdomain
    res = dom.file_copy({"source": str(src), "dest": str(src), "overwrite": True})
    assert res.get("success") is not True, f"自覆盖未拒：{res}"
    assert src.read_text(encoding="utf-8") == "payload"


def test_C3b_move_source_equals_dest_no_data_loss(fdomain) -> None:
    """C③续 move 且源=目标：允许 no-op，但绝不得丢数据（登记：返回 success=True 属语义噪声）。"""
    dom, _router, src = fdomain
    res = dom.file_copy({"source": str(src), "dest": str(src), "move": True, "overwrite": True})
    assert src.exists(), f"move 自覆盖致数据丢失：{res}"
    assert src.read_text(encoding="utf-8") == "payload"


def test_C4_read_side_escape_still_refused(fdomain, tmp_path) -> None:
    """C①续 source 侧逃逸（绝对/..）→ 读边界仍生效（防外泄读取后复制）。"""
    dom, router, _src = fdomain
    secret = tmp_path / "outside_secret.txt"
    secret.write_text("topsecret", encoding="utf-8")
    res = dom.file_copy({"source": str(secret), "dest": str(router.data / "x.txt")})
    assert res.get("success") is not True
    res2 = dom.file_copy({"source": "../../outside_secret.txt", "dest": str(router.data / "y.txt")})
    assert res2.get("success") is not True


def test_C5_copy_dest_through_junction_rejected(fdomain) -> None:
    """C①续 Windows junction 逃逸（可建则真打，建不了登记）。"""
    pytest.importorskip("_winapi")
    dom, router, src = fdomain
    outside = router.data.parent / "junction_target"
    outside.mkdir(exist_ok=True)
    link = router.data / "via_link"
    try:
        import _winapi

        _winapi.CreateJunction(str(outside), str(link))
    except Exception as exc:  # pragma: no cover
        pytest.skip(f"登记：本机无法创建 junction（{exc}）")
    res = dom.file_copy({"source": str(src), "dest": str(link / "leak.txt"), "overwrite": True})
    assert res.get("success") is not True, f"junction 写出边界：{res}"
    assert not (outside / "leak.txt").exists()


# ── D. SEC-8 screenshot 路径参数注入 ─────────────────────────────────────


def _whitelist(store: ConfirmationStore) -> str:
    return desktop._screenshots_whitelist_dir()


@pytest.mark.parametrize(
    "target",
    [
        "C:/Windows/Temp",
        r"\\127.0.0.1\C$\Windows\Temp",
        "\\\\?\\C:\\Windows\\Temp",
        "/tmp/siyi_red",
        "D:/h/3.25/思忆集/思忆集app/data",
        "C:/Windows/Temp/../Temp",
    ],
)
def test_D1_screenshot_external_target_rewritten(tmp_path, monkeypatch, store, target) -> None:
    """D screenshot 路径注入：外部 target_dir 一律改写落 <data_dir>/screenshots/。"""
    sink: list = []
    _fake_pil(monkeypatch, sink)
    res = desktop.desktop_screenshot(target)
    if res.get("status") == "unavailable":
        pytest.skip("PIL 不可用")
    assert res.get("status") == "ok", res
    allowed = os.path.normcase(os.path.abspath(_whitelist(store)))
    final = os.path.normcase(os.path.abspath(res["path"]))
    assert final.startswith(allowed), f"落盘越界：{final}"
    assert not Path(r"C:\Windows\Temp\screenshot.png").exists()


def test_D2_screenshot_traversal_inside_prefix_rewritten(monkeypatch, store) -> None:
    """D续 screenshots/../.. 词法逃逸 + 前缀仿冒目录（screenshots_evil）。"""
    sink: list = []
    _fake_pil(monkeypatch, sink)
    allowed = os.path.normcase(os.path.abspath(_whitelist(store)))
    for probe in [
        os.path.join(allowed, "..", "..", "escaped"),
        allowed + "_evil",
        allowed[: len(allowed) - 1] + "ons_evil2",
    ]:
        res = desktop.desktop_screenshot(probe)
        final = os.path.normcase(os.path.abspath(res["path"]))
        assert final.startswith(allowed), f"前缀/词法逃逸未改写：{probe} -> {final}"


def test_D3_screenshot_junction_escape(tmp_path, monkeypatch, store) -> None:
    """D续 白名单目录内置 junction → abspath 不解析链接，真写出边界（应拒）。"""
    pytest.importorskip("_winapi")
    sink: list = []
    _fake_pil(monkeypatch, sink)
    allowed = _whitelist(store)
    os.makedirs(allowed, exist_ok=True)
    outside = tmp_path / "escape_target"
    outside.mkdir(exist_ok=True)
    link = Path(allowed) / "via_junction"
    try:
        import _winapi

        _winapi.CreateJunction(str(outside), str(link))
    except Exception as exc:  # pragma: no cover
        pytest.skip(f"登记：本机无法创建 junction（{exc}）")
    res = desktop.desktop_screenshot(str(link))
    real = os.path.normcase(os.path.realpath(res["path"]))
    assert real.startswith(os.path.normcase(os.path.realpath(allowed))), f"SEC-8 未解析真实路径（abspath 而非 realpath）→ junction 写出边界：{res}"


# ── E. QUA-6 TTL ────────────────────────────────────────────────────────


def test_E1_expired_resolve_rejected_and_flipped(store) -> None:
    cid = store.create("notes.delete", {"note_id": "1"}, source="butler", ttl_s=-1.0)
    assert store.resolve(cid, approved=True, args_sha256=compute_args_sha256({"note_id": "1"})) is None
    assert _raw_entries(store)[cid]["status"] == "expired"


def _freeze_clock(monkeypatch, at: float) -> None:
    """冻结 confirmation_store 的**唯一时间入口**。

    2026-09-18 修：原实现 ``monkeypatch.setattr(cs_mod, "time", SimpleNamespace(time=...))``
    —— 但批1「时间纪律」改造后本模块**不再** ``import time``，时间读数统一收敛到
    ``core.clock.wall_ts()``，于是旧打桩抛 ``AttributeError: module
    'services.confirmation_store' has no attribute 'time'``，E2/E3/E5 共 4 条用例红。
    （这是批1 改造遗漏的**测试侧**适配：只改了生产代码，没跟上打桩点。）

    治本点：冻结 ``cs_mod.clock.wall_ts``（即 confirmation_store 真正调用的那个
    函数对象所在模块），而不是去猜模块里有没有 ``time``。
    """
    monkeypatch.setattr(cs_mod.clock, "wall_ts", lambda: at)


@pytest.mark.parametrize("delta,should_pass", [(1.0, True), (-1.0, False)])
def test_E2_expiry_boundary_1s(store, monkeypatch, delta, should_pass) -> None:
    """E 边界值 now±1s。"""
    cid = store.create("x.y", {}, source="butler")
    data = _raw_entries(store)
    base = data[cid]["created_at"] + 300.0
    data[cid]["expires_at"] = base + delta
    Path(store.path).write_text(json.dumps(data), encoding="utf-8")
    _freeze_clock(monkeypatch, base)
    out = store.resolve(cid, approved=True)
    assert (out is not None) is should_pass, f"delta={delta} 判定异常：{out}"


def test_E3_expires_at_exactly_now_is_still_approvable(store, monkeypatch) -> None:
    """登记：判定用严格 `>` ⇒ expires_at == now 仍可批准（边界含端点，非 off-by-one 风险）。"""
    cid = store.create("x.y", {}, source="butler")
    stamp = _raw_entries(store)[cid]["expires_at"]
    _freeze_clock(monkeypatch, stamp)
    assert store.resolve(cid, approved=True) is not None


def test_E4_legacy_entry_without_expires_at_must_not_crash(store) -> None:
    """E续 条目缺 expires_at（旧队列/伪造形状）→ 期望优雅拒批，不得抛异常。"""
    cid = store.create("x.y", {}, source="butler")
    data = _raw_entries(store)
    data[cid].pop("expires_at")
    Path(store.path).write_text(json.dumps(data), encoding="utf-8")
    try:
        out = store.resolve(cid, approved=True)
    except Exception as exc:  # noqa: BLE001
        pytest.fail(f"resolve 对畸形条目抛 {type(exc).__name__}: {exc}（应返回 None 并保持可用）")
    assert out is None


def test_E5_get_then_resolve_is_consistent(store, monkeypatch) -> None:
    cid = store.create("x.y", {}, source="butler")
    base = _raw_entries(store)[cid]["expires_at"] + 5
    _freeze_clock(monkeypatch, base)
    assert store.get(cid)["status"] == "expired"
    assert store.resolve(cid, approved=True) is None


# ── F. R4 -32010 显式错误码 ──────────────────────────────────────────────


def _r4_registry(executed: list) -> MethodRegistry:
    reg = MethodRegistry()

    async def spy_delete(note_id: str = "", **kw: object) -> dict:
        executed.append(note_id)
        return {"deleted": note_id}

    reg.register("notes.delete", spy_delete, risk=RISK_R4)
    return reg


def _server(reg: MethodRegistry) -> WebSocketServer:
    return WebSocketServer(reg, MagicMock(), host="127.0.0.1", port=0, enable_heartbeat=False)


def _fake_ws() -> MagicMock:
    ws = MagicMock()
    ws.closed = False
    ws.send_str = AsyncMock()
    return ws


def _sent(ws) -> dict:
    return json.loads(ws.send_str.call_args[0][0])


def test_F1_legacy_ws_caller_gets_32010_not_pending() -> None:
    """F 不带 client_version 调 notes.delete → 显式 -32010，且不得静默 pending。"""
    executed: list = []
    fake = MagicMock()
    fake.add.return_value = "pid-1"
    with patch("services.sandbox_manager.get_sandbox_manager", return_value=fake):
        srv = _server(_r4_registry(executed))
        ws = _fake_ws()
        asyncio.run(
            srv._on_message(ws, json.dumps({"jsonrpc": "2.0", "id": 1, "method": "notes.delete", "params": {"note_id": "X"}}), conn_id="conn-legacy")
        )
        out = _sent(ws)
    assert out.get("error", {}).get("code") == -32010, out
    assert "result" not in out
    fake.add.assert_not_called()
    assert executed == []


def test_F2_versioned_ws_caller_still_pending() -> None:
    executed: list = []
    fake = MagicMock()
    fake.add.return_value = "pid-2"
    with patch("services.sandbox_manager.get_sandbox_manager", return_value=fake):
        srv = _server(_r4_registry(executed))
        srv._conn_client_version["conn-modern"] = "3.25.0"
        ws = _fake_ws()
        asyncio.run(
            srv._on_message(ws, json.dumps({"jsonrpc": "2.0", "id": 2, "method": "notes.delete", "params": {"note_id": "Y"}}), conn_id="conn-modern")
        )
        out = _sent(ws)
    assert out.get("result", {}).get("pending") is True
    assert executed == []


def test_F3_http_rpc_transport_also_enforces_32010() -> None:
    """F 未声明版本 ≡ legacy：HTTP /rpc 是同一 22 方法的另一入口，不得仍回 pending。"""
    executed: list = []
    fake = MagicMock()
    fake.add.return_value = "pid-http"
    srv = _server(_r4_registry(executed))
    srv._verify_auth = lambda r: True
    srv._has_explicit_token = lambda r: False
    srv._verify_origin = lambda r: True
    srv._is_loopback = lambda r: False
    req = MagicMock()
    req.headers = {}
    req.remote = "10.0.0.9"
    req.json = AsyncMock(return_value={"jsonrpc": "2.0", "id": 7, "method": "notes.delete", "params": {"note_id": "Z"}})
    with patch("services.sandbox_manager.get_sandbox_manager", return_value=fake):
        resp = asyncio.run(srv._handle_rpc_http(req))
    body = json.loads(resp.body)
    assert body.get("error", {}).get("code") == -32010, f"F FAIL：HTTP /rpc 通道未声明 client_version 的调用仍收静默 pending 信封 → {body}"
    assert "legacy_client" in inspect.getsource(WebSocketServer._handle_rpc_http), (
        "ws_server._handle_rpc_http 未向 dispatch 传 legacy_client（默认 False）"
    )
    assert executed == []


@pytest.mark.parametrize("declared", ["0", "abc", "not-a-version", "1"])
def test_F4_arbitrary_nonempty_version_string_is_exempts(declared) -> None:
    """登记：任意非空 client_version 即视为新客户端（零格式/白名单校验）。"""
    executed: list = []
    fake = MagicMock()
    fake.add.return_value = "pid-v"
    with patch("services.sandbox_manager.get_sandbox_manager", return_value=fake):
        srv = _server(_r4_registry(executed))
        ws = _fake_ws()
        asyncio.run(
            srv._on_message(
                ws,
                json.dumps({"jsonrpc": "2.0", "id": 3, "client_version": declared, "method": "notes.delete", "params": {}}),
                conn_id=f"conn-{declared}",
            )
        )
        out = _sent(ws)
    assert out.get("result", {}).get("pending") is True


def test_F5_stream_channel_legacy_must_surface_explicit_code() -> None:
    """F续 流式帧（stream:true）调 R4：不得 pending，且须给出显式码。"""
    executed: list = []
    fake = MagicMock()
    fake.add.return_value = "pid-s"
    with patch("services.sandbox_manager.get_sandbox_manager", return_value=fake):
        srv = _server(_r4_registry(executed))
        ws = _fake_ws()
        asyncio.run(
            srv._on_message(
                ws,
                json.dumps({"jsonrpc": "2.0", "id": 9, "stream": True, "method": "notes.delete", "params": {"note_id": "S"}}),
                conn_id="conn-stream",
            )
        )
        frames = [json.loads(c[0][0]) for c in ws.send_str.call_args_list]
    assert not any((f.get("result") or {}).get("pending") for f in frames), frames
    joined = json.dumps(frames, ensure_ascii=False)
    assert "-32010" in joined or "请升级客户端" in joined, f"流式通道旧客户端丢码（只见 Internal error）：{frames}"


def test_F6_all_22_audit_methods_are_r4_gated() -> None:
    """F 口径核验：audit:240 的 22 方法须全部 ∈ _R4_METHODS（否则旧客户端直执行）。"""
    from _r4_baseline import assert_r4_not_shrunk

    missing = [m for m in AUDIT_22 if m not in _R4_METHODS]
    assert not missing, f"22 方法中未列 R4：{missing}"
    # 项数沿革：AG-C1③ 新增 sandbox.history.clear → 37→38；
    # 2026-09-17 c8b 补入 memory.delete → 38→39；R2-B6 file.write → 39→40；
    # 2026-09-18 批4 P2-PD-NODEL 补 knowledge.entity.delete → 40→41。
    # 批4 起改用单点下界基线（见 tests/_r4_baseline.py）：合法新增不再假红，
    # 成员被移出才报红——等号写法已在历史上漂移过两次。
    assert_r4_not_shrunk(_R4_METHODS)


# ── G. 组合链：伪造来源 + 匿名 + R4 ─────────────────────────────────────


def _req(remote: str | None, origin: str | None, headers: dict | None = None) -> MagicMock:
    r = MagicMock()
    r.remote = remote
    r.transport.get_extra_info.return_value = (remote, 5555) if remote else None
    h = {"Host": "127.0.0.1:18765"}
    if origin is not None:
        h["Origin"] = origin
    h.update(headers or {})
    r.headers = h
    r.query = {}
    r.cookies = {}
    return r


@pytest.mark.parametrize(
    "remote,origin",
    [
        ("10.0.0.9", "http://localhost:5175"),
        ("203.0.113.7", "http://127.0.0.1:5175"),
        ("10.0.0.9", "file://x"),
        ("10.0.0.9", "http://localhost.attacker.com"),
        ("10.0.0.9", "http://localhost:5175.evil.com"),
        ("10.0.0.9", "http://evil.com#@localhost"),
        ("10.0.0.9", "http://evil.com@localhost"),
    ],
)
def test_G1_loopback_cannot_be_forged_by_headers(remote, origin) -> None:
    """G① Origin / X-Forwarded-For 变体不得拼出 loopback 匿名信任（远端仍须凭证）。"""
    srv = _server(_r4_registry([]))
    req = _req(remote, origin, headers={"X-Forwarded-For": "127.0.0.1"})
    assert srv._is_loopback(req) is False, f"remote={remote} 被判 loopback（头伪造生效）"
    assert srv._verify_auth(req) is False, f"remote={remote} Origin={origin} 无凭证却过鉴权"


def test_G2_cswh_local_page_with_foreign_origin_rejected() -> None:
    """G①续 本机恶意网页（transport loopback + 外站 Origin）仍须拒。"""
    srv = _server(_r4_registry([]))
    assert srv._verify_origin(_req("127.0.0.1", "https://evil.example")) is False


def test_G3_pending_id_self_approval_chain(store) -> None:
    """G② 组合链：连上（可匿名 loopback）→ 声明任意 client_version → R4 pending
    → 同一连接直接 sandbox.approve 自批 → 未获用户批准即执行。"""
    executed: list = []
    reg = _r4_registry(executed)

    async def approve(pending_id: str = "") -> dict:
        return await _sandbox._handle_sandbox_approve(pending_id, registry=reg)

    reg.register("sandbox.approve", approve)
    assert reg.is_r4("sandbox.approve") is False, "登记：sandbox.approve 自身非 R4（审批入口无风险分级、无持有者绑定）"
    mgr = SandboxManager()
    with patch("services.sandbox_manager.get_sandbox_manager", return_value=mgr), patch.object(_sandbox, "get_sandbox_manager", return_value=mgr):
        srv = _server(reg)
        ws = _fake_ws()
        asyncio.run(
            srv._on_message(
                ws,
                json.dumps({"jsonrpc": "2.0", "id": 1, "client_version": "1.0.0", "method": "notes.delete", "params": {"note_id": "VICTIM"}}),
                conn_id="conn-anon",
            )
        )
        env = _sent(ws)["result"]
        pid = env["pending_id"]
        asyncio.run(
            srv._on_message(
                ws, json.dumps({"jsonrpc": "2.0", "id": 2, "method": "sandbox.approve", "params": {"pending_id": pid}}), conn_id="conn-anon"
            )
        )
    assert env.get("pending") is True
    assert executed == [], f"审批绕过链成立：请求方拿到自己的 pending_id={pid} 后可自行 sandbox.approve 即执行"


def test_G4_anon_ws_needs_no_token_when_loopback() -> None:
    """登记：loopback 匿名 WS 短路放行（RQ-1 观测不改语义）——G② 链的前置信任面。"""
    srv = _server(_r4_registry([]))
    req = _req("127.0.0.1", "http://localhost:5175")
    assert srv._verify_origin(req) is True
    assert srv._presents_valid_credential(req) is False
