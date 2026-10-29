"""P-H 双端传送 + 阅读/词书写路径旅程探针（2026-09-17，第二轮系统×环境测试）。

方法论：模拟客户使用 + 对抗边界，全部写后读断言；写只进隔离实例临时 data。
运行：SIYI_INTEGRATION_AUTOSTART=1 python -m pytest tests/test_ph_journey_20260917.py -v
（WS 旅程依赖 conftest 自启的隔离临时后端；边界攻击为进程内直调 handler，
 避免 WS 入站 16MB 帧上限把连接打死，同时可精确断言拒绝形态。）

生产数据泄漏守卫放行（2026-09-17）：AUTOSTART 存在路径分裂 —— local_data_store
走临时目录，但 transfer_files/transfer_history.json 会因 context 回落到**生产
data/**。本模块的 _leak_guard（module autouse）以 TAG 唯一标记探针写入、测后
按特征清理，属于"确有必要写生产路径"的场景，故显式声明 allow_production_write。
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import sys
import tempfile
import types
import uuid

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.allow_production_write]

TAG = uuid.uuid4().hex[:8]


# ─────────────────────────────────────────────────────────
# 进程内直调基础设施（不依赖后端进程）
# ─────────────────────────────────────────────────────────
class _FakeDS:
    def __init__(self, base_dir: str):
        self.base_dir = base_dir


class _FakeSettings:
    def __init__(self, fail: bool = False):
        self.store: dict[str, str] = {}
        self.fail = fail

    def get_setting(self, key, default=""):
        return self.store.get(key, default)

    def set_setting(self, key, value):
        if self.fail:
            raise OSError("simulated settings write failure")
        self.store[key] = value


class _Ctx:
    def __init__(self, base_dir: str, settings: _FakeSettings | None = None):
        self.local_data_store = _FakeDS(base_dir)
        self.settings_repository = settings if settings is not None else _FakeSettings()


class _Reg:
    def __init__(self):
        self.handlers: dict[str, object] = {}

    def register(self, name, handler, **kw):
        self.handlers[name] = handler


@pytest.fixture()
def transfer_mod():
    """导入 transfer handler 模块并隔离 env。

    PH-A1（2026-09-17）：模块级 _TRANSFER_TOKEN 已随 settings.transfer_token
    配对凭证子系统一并删除，夹具只需隔离 SIYI_TRANSFER_KEY。
    """
    from backend.handlers import _transfer

    old_env = os.environ.pop("SIYI_TRANSFER_KEY", None)
    yield _transfer
    if old_env is not None:
        os.environ["SIYI_TRANSFER_KEY"] = old_env
    else:
        os.environ.pop("SIYI_TRANSFER_KEY", None)


def _register(transfer_mod, ctx):
    reg = _Reg()
    transfer_mod.register_transfer(reg, ctx)
    return reg


@pytest.fixture()
def tmp_base():
    d = tempfile.mkdtemp(prefix="siyi-ph-")
    yield d
    import shutil

    shutil.rmtree(d, ignore_errors=True)


def _res(resp: dict):
    """WS 信封 → result；直调时原样返回。"""
    if isinstance(resp, dict) and "result" in resp:
        return resp["result"]
    return resp


# ─────────────────────────────────────────────────────────
# 隔离泄漏防护（2026-09-17 公告后加固）
# AUTOSTART 存在路径分裂：local_data_store 走临时目录，但 transfer_files/
# transfer_history.json/ai_cache.json 会因 context 回落到**生产 data/**。
# 本模块 WS 写面（TestDualDeviceJourney）每次以 TAG 唯一标记，测后按特征清理，
# 避免污染生产数据（教训：初版探针曾泄漏 4 条历史+5 文件+2 摘要，已手工恢复）。
# ─────────────────────────────────────────────────────────
@pytest.fixture(scope="module", autouse=True)
def _leak_guard():
    """生产 data 备份基线条目数 + 测后按本模块特征清理。

    仅在检测到生产 data 存在本模块 TAG 痕迹时清理；真实用户数据（无探针标记）不动。
    若基线即含探针痕迹（前次泄漏未清）同样按特征扫掉。
    """
    import json as _json
    import shutil as _shutil

    app_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    dd = os.path.join(app_dir, "data", "data")
    th_path = os.path.join(dd, "transfer_history.json")
    tfroot = os.path.join(dd, "transfer_files")
    ac_path = os.path.join(app_dir, "data", "ai_cache.json")
    markers = (f"{TAG}-", "PH 探针", "example.com/ph", "ph-nested-")
    # 仅匹配本探针专属前缀/含 TAG 的条目，绝不含 win.ini/x.bin 等通用名（防误删真实用户传输文件）
    file_prefix = ("ph-journey-", "ph-ovwr-", "ph-file-")

    def _is_probe(entry) -> bool:
        s = _json.dumps(entry, ensure_ascii=False)
        return any(m in s for m in markers) or f"{TAG}-" in s

    yield
    # —— 测后清理（无论成败）——
    if os.path.isfile(th_path):
        try:
            hist = _json.load(open(th_path, "r", encoding="utf-8"))
            kept = [h for h in hist if not _is_probe(h)]
            if len(kept) != len(hist):
                _shutil.copy2(th_path, th_path + ".pre-ph.bak")
                json.dump(kept, open(th_path + ".tmp", "w", encoding="utf-8"), ensure_ascii=False, indent=2)
                os.replace(th_path + ".tmp", th_path)
        except Exception:
            pass
    if os.path.isdir(tfroot):
        for name in os.listdir(tfroot):
            p = os.path.join(tfroot, name)
            if name.startswith(file_prefix) or name in file_prefix or TAG in name or name == "%2e%2e":
                _shutil.rmtree(p) if os.path.isdir(p) else os.remove(p)
        notes = os.path.join(tfroot, "notes")
        if os.path.isdir(notes):
            for d in os.listdir(notes):
                if TAG in d:
                    _shutil.rmtree(os.path.join(notes, d))
            if not os.listdir(notes):
                os.rmdir(notes)
    if os.path.isfile(ac_path):
        try:
            data = _json.load(open(ac_path, "r", encoding="utf-8"))
            if isinstance(data, dict):
                kept = {k: v for k, v in data.items() if not _is_probe((k, v)) and "PH 探针" not in str(v)}
            else:
                kept = [e for e in data if not _is_probe(e)]
            if len(kept) != len(data):
                _shutil.copy2(ac_path, ac_path + ".pre-ph.bak")
                json.dump(kept, open(ac_path + ".tmp", "w", encoding="utf-8"), ensure_ascii=False, indent=2)
                os.replace(ac_path + ".tmp", ac_path)
        except Exception:
            pass


# ─────────────────────────────────────────────────────────
# ① token 旅程
# ─────────────────────────────────────────────────────────
class TestTokenJourney:
    async def test_token_info_lan_mode(self, rpc):
        r = await rpc("transfer.token_info", {})
        res = _res(r)
        assert isinstance(res, dict)
        assert res.get("mode") in ("lan", "secured")
        # 隔离实例未注入 SIYI_TRANSFER_KEY → 必须是 lan，且不泄露任何 token 片段
        assert res.get("token_required") is False
        assert "token_prefix" not in res  # 仅允许 token_required 布尔位，不泄露 token 内容

    async def test_no_paired_credential_persisted(self):
        """PH-A1（2026-09-17）：配对凭证子系统已删 —— 注册不再写 settings/回退文件。

        原 P1-3 契约（注册时生成随机 token 并持久化到 settings，失败回退
        data/transfer_token.txt）已整段移除：该 token 全仓零消费且无人校验。
        """
        from backend.handlers import _transfer

        d = tempfile.mkdtemp(prefix="siyi-ph-tok-")
        try:
            ctx = _Ctx(d, settings=_FakeSettings(fail=True))  # 即便 settings 写失败也不应回退建文件
            _register(_transfer, ctx)
            assert "transfer_token" not in ctx.settings_repository.store, "静默重建了 settings 配对 token"
            assert not os.path.isfile(os.path.join(d, "transfer_token.txt")), "回退凭证文件仍被生成"
            assert not hasattr(_transfer, "_get_or_create_token"), "token 生成死代码未删除"
        finally:
            import shutil

            shutil.rmtree(d, ignore_errors=True)

    async def test_check_auth_contract(self, tmp_base, transfer_mod, monkeypatch):
        """_check_auth 只信 env SIYI_TRANSFER_KEY —— 单一凭证模型（PH-A1）。"""
        d = tempfile.mkdtemp(prefix="siyi-ph-auth-")
        ctx = _Ctx(d)
        # 1) env 未设 → LAN 全放行；且注册期不再产生任何配对 token
        reg = _register(transfer_mod, ctx)
        r = await reg.handlers["transfer.send"](text=f"{TAG}-lan", target="desktop")
        assert r.get("success") is True, f"LAN 模式 send 被拒: {r}"
        assert "transfer_token" not in ctx.settings_repository.store, "PH-A1 后不应再有 settings 配对 token"
        # 2) env 设成别的值 → 任何非 env 值（含旧配对 token）都被拒
        monkeypatch.setenv("SIYI_TRANSFER_KEY", "env-secret-xyz")
        r = await reg.handlers["transfer.send"](text=f"{TAG}-paired", target="desktop", _token="stale-paired-token")
        assert "auth failed" in str(r.get("error", "")), f"非 env 值被 env 模式接受（异常）: {r}"
        # 3) 缺 token → 拒；错 token → 拒；对 env token → 过
        r = await reg.handlers["transfer.send"](text="x")
        assert "auth required" in str(r.get("error", ""))
        r = await reg.handlers["transfer.upload"](name="a.txt", data="AAA", _token="wrong")
        assert "auth failed" in str(r.get("error", ""))
        r = await reg.handlers["transfer.send"](text=f"{TAG}-envok", target="desktop", _token="env-secret-xyz")
        assert r.get("success") is True
        import shutil

        shutil.rmtree(d, ignore_errors=True)


# ─────────────────────────────────────────────────────────
# ② 边界对抗（send 内嵌附件 / upload 上限 / history 轮转）
# ─────────────────────────────────────────────────────────
class TestBoundary:
    async def test_send_embedded_1mb_plus_1_rejected_not_stored(self, tmp_base, transfer_mod):
        """1MB+1 字节 base64 → 明确拒绝（error 含原因），且不截断入库。"""
        ctx = _Ctx(tmp_base)
        reg = _register(transfer_mod, ctx)
        payload = "A" * (transfer_mod.MAX_EMBEDDED_FILE + 1)
        r = await reg.handlers["transfer.send"](text=f"{TAG}-big", file=payload, fileName="big.bin")
        assert isinstance(r, dict) and r.get("error"), f"超限附件未被拒绝: {keys(r)}"
        assert "too large" in r["error"], f"拒绝原因不明确: {r['error']}"
        assert "transfer.upload" in r["error"], "未引导走大文件通道"
        assert "success" not in r
        # 读 history 验证：没有任何截断条目混入
        hist = await reg.handlers["transfer.history"](limit=50)
        assert all("big.bin" != h.get("fileName") for h in hist), "超限附件被截断入库"
        assert all(h.get("text") != f"{TAG}-big" for h in hist), "拒绝的 send 仍落了历史"

    async def test_send_embedded_exactly_1mb_accepted(self, tmp_base, transfer_mod):
        ctx = _Ctx(tmp_base)
        reg = _register(transfer_mod, ctx)
        payload = "A" * transfer_mod.MAX_EMBEDDED_FILE  # 边界值：等于上限 → 放行
        r = await reg.handlers["transfer.send"](text="", file=payload, fileName="edge.bin")
        assert r.get("success") is True, f"=1MB 边界被误拒: {r}"

    async def test_upload_50mb_plus_rejected(self, tmp_base, transfer_mod):
        """base64 长度 > 50MB → 拒（进程内直调绕开 WS 16MB 入站帧限）。"""
        ctx = _Ctx(tmp_base)
        reg = _register(transfer_mod, ctx)
        huge = "B" * (transfer_mod.MAX_FILE_SIZE + 1)
        r = await reg.handlers["transfer.upload"](name="huge.bin", data=huge)
        assert r.get("success") is False and "50MB" in str(r.get("error", "")), f"50MB+ 未被拒: {r}"
        assert not os.path.exists(os.path.join(tmp_base, "transfer_files", "huge.bin")), "超限文件仍落盘"

    async def test_history_51_rotates_oldest(self, tmp_base, transfer_mod):
        ctx = _Ctx(tmp_base)
        reg = _register(transfer_mod, ctx)
        for i in range(51):
            r = await reg.handlers["transfer.send"](text=f"{TAG}-h{i:02d}", target="desktop")
            assert r.get("success") is True, f"第{i}条 send 失败: {r}"
        hist = await reg.handlers["transfer.history"](limit=100)
        assert len(hist) == 50, f"历史未被裁剪到 MAX_HISTORY=50: {len(hist)}"
        texts = [h["text"] for h in hist]
        assert f"{TAG}-h00" not in texts, "第 1 条未被轮转掉"
        assert texts[0] == f"{TAG}-h50", f"最新条目不在首位: {texts[:3]}"
        # history(limit) 参数生效
        assert len(await reg.handlers["transfer.history"](limit=5)) == 5

    async def test_send_empty_rejected(self, tmp_base, transfer_mod):
        ctx = _Ctx(tmp_base)
        reg = _register(transfer_mod, ctx)
        r = await reg.handlers["transfer.send"](text="   ")
        assert r.get("error"), f"空白文本被接受: {r}"


def keys(d):
    return list(d.keys()) if isinstance(d, dict) else d


# ─────────────────────────────────────────────────────────
# ③ 路径穿越攻击集
# ─────────────────────────────────────────────────────────
TRAVERSAL_CASES = [
    "../escape.txt",
    "..%2fevil.txt",
    "%2e%2e/evil.txt",
    "C:/Windows/win.ini",
    "c:\\Windows\\win.ini",
    "/etc/passwd",
    "a/../../b/evil.txt",
    "notes/../../secret.txt",
    "..\\..\\x.txt",
    "",  # 空 path → 参数错误
]


class TestTraversalWS:
    """经真实 RPC 通道攻击：必须全部拒绝且不在 transfer_files 外读写真实文件。"""

    async def test_upload_traversal_rejected(self, rpc, rpc_approved):
        """穿越/绝对路径全 fail-closed（PH-F2 修复后盘符也显式拒）。"""
        blob = base64.b64encode(b"ESCAPED").decode()
        for p in TRAVERSAL_CASES:
            r = _res(await rpc("transfer.upload", {"name": p, "data": blob, "folder": ""}))
            # 字面 ".." / unix 绝对 "/" / 盘符 "C:\\" 全部必须拒（PH-F2：盘符修复前会被 basename 静默收敛）
            literal_traversal = (".." in p) or p.startswith("/") or p.startswith("\\")
            is_abs_drive = (len(p) >= 2 and p[1] == ":") or (":" in p[:6])
            if not literal_traversal and not is_abs_drive:
                # 仅剩纯编码态 "%2e%2e"（不 decode 时不触发字面 ".." 检查）：
                # 允许 basename 收敛，但绝不能写出含目录分隔的逃逸 path
                assert r.get("success") is not True or "/" not in str(r.get("path", "")), f"疑似逃逸: {p!r} → {r}"
                if r.get("success"):
                    await rpc_approved("transfer.delete", {"path": r["path"]})
                continue
            assert r.get("success") is False, f"穿越/绝对 upload 竟成功: {p!r} → {r}"
            err = str(r.get("error", ""))
            assert "invalid path" in err or "required" in err, f"{p!r} 拒绝形态异常: {r}"
            if is_abs_drive:
                assert "absolute" in err, f"PH-F2 盘符未走显式绝对路径拒绝分支: {p!r} → {r}"
        # 攻击面副作用检查：不得出现逃逸到 base 外的文件
        listing = _res(await rpc("transfer.files", {}))
        names = [f["name"] for f in listing]
        assert not any("escape" in n for n in names), f"transfer_files 混入攻击文件: {names}"

    async def test_upload_folder_traversal(self, rpc, rpc_approved):
        blob = base64.b64encode(b"X").decode()
        for f in ["../out", "..", "a/../../b", "%2e%2e"]:
            r = _res(await rpc("transfer.upload", {"name": "x.bin", "data": blob, "folder": f}))
            # "%"2e%2e 不 decode → 作为字面目录名落在 base 内是**安全**的；.. 系列必须拒
            if "%" not in f:
                assert r.get("success") is False, f"folder 穿越未拒: {f!r} → {r}"
            elif r.get("success"):
                # %2e%2e 合法字面子目录，测后清理避免残留生产 transfer_files
                await rpc_approved("transfer.delete", {"path": r["path"]})

    async def test_download_traversal_never_reads_real_file(self, rpc):
        for p in ["../auth_token", "..\\auth_token", "C:/Windows/win.ini", "/etc/passwd", "sub/../../auth_token"]:
            r = _res(await rpc("transfer.download", {"path": p}))
            assert r.get("success") is not True or "data" not in r or len(r.get("data", "")) < 200, f"疑似读到真实文件: {p!r}"
            if ".." in p or p.startswith("/") or ":" in p:
                # 明确拒绝 或 basename 收敛后 not found —— 但绝不能回真文件内容
                if r.get("success") is True:
                    blob = base64.b64decode(r["data"])
                    assert b"Bearer" not in blob and len(blob) < 1024, f"download 泄露敏感文件: {p!r}"

    async def test_delete_traversal_rejected(self, rpc):
        for p in ["../auth_token", "C:/Windows/win.ini", "/etc/passwd"]:
            r = _res(await rpc("transfer.delete", {"path": p}))
            assert r.get("success") is not True, f"穿越 delete 竟成功: {p!r} → {r}"

    async def test_files_folder_traversal(self, rpc):
        for f in ["..", "../..", "/etc"]:
            r = _res(await rpc("transfer.files", {"folder": f}))
            assert r == [], f"files 列出了穿越目录: {f!r} → {r}"

    def test_resolve_path_function_units(self, transfer_mod):
        base = tempfile.mkdtemp(prefix="siyi-ph-rp-")
        for evil in ["../x", "a/../../x", "..", "\\..\\x", "\x00../x"]:
            out = transfer_mod._resolve_file_store_path(base, evil)
            assert out is None or out.startswith(os.path.abspath(base)), f"_resolve 放行穿越: {evil!r} → {out}"
        ok = transfer_mod._resolve_file_store_path(base, "notes/1/att.bin")
        assert ok == os.path.join(os.path.abspath(base), "notes", "1", "att.bin")
        import shutil

        shutil.rmtree(base, ignore_errors=True)

    async def test_roundtrip_nested_upload_download(self, rpc, rpc_approved):
        """PH-D1/D2（已修复·回归锁定）：folder 归档文件须能按 upload 返回的 path
        download 回来并 delete 掉（读写对称）。修复前 download/delete 用 basename
        拍平到 transfer_files 根 → 子目录附件传了读不到、删不掉。"""
        content = f"ph-nested-{TAG}"
        blob = base64.b64encode(content.encode()).decode()
        r = _res(await rpc("transfer.upload", {"name": "att.bin", "data": blob, "folder": f"notes/{TAG}"}))
        assert r.get("success") is True, f"合法 folder 上传失败: {r}"
        rel = r["path"]
        assert rel == f"notes/{TAG}/att.bin", f"upload 返回 path 不含 folder: {rel}"
        listed = _res(await rpc("transfer.files", {"folder": f"notes/{TAG}"}))
        assert any(f["name"] == "att.bin" for f in listed), f"files 列不到归档文件: {listed}"
        # PH-D1：download 用含 folder 的相对路径必须取回且字节一致
        d = _res(await rpc("transfer.download", {"path": rel}))
        assert d.get("success") is True, f"PH-D1 回归：download('{rel}') 失败: {d}"
        assert base64.b64decode(d["data"]).decode() == content, "嵌套下载字节不一致"
        # PH-D2：delete（R4 审批链）必须真删子目录文件
        ack = await rpc_approved("transfer.delete", {"path": rel})
        final = (ack.get("result") or {}).get("result") or ack
        assert final.get("success") is True, f"PH-D2 回归：delete('{rel}') 审批重放后失败: {final}"
        listed2 = _res(await rpc("transfer.files", {"folder": f"notes/{TAG}"}))
        assert all(f["name"] != "att.bin" for f in listed2), "删除后 files 仍列出归档文件"
        d2 = _res(await rpc("transfer.download", {"path": rel}))
        assert d2.get("success") is not True, "删除后仍可下载（磁盘未真删）"


# ─────────────────────────────────────────────────────────
# ④ 双端 + delete 完整旅程（WS 真实通道）
# ─────────────────────────────────────────────────────────
def _ws_url():
    return os.environ.get("SIYI_WS_URL", "ws://127.0.0.1:18765/ws")


def _auth():
    tok = os.environ.get("SIYI_DIAG_TOKEN", "").strip()
    return {"Authorization": f"Bearer {tok}"} if tok else {}


class TestDualDeviceJourney:
    async def test_a_send_b_receives(self, rpc):
        import aiohttp

        url = _ws_url()
        marker = f"{TAG}-hello-B"
        async with aiohttp.ClientSession() as ses:
            async with ses.ws_connect(f"{url}?client_version=1.2.0", timeout=10, headers=_auth()) as wsB:
                # 桌面端 A → 发给 mobile（target=mobile 时 from_device 判定为 desktop）
                r = await rpc("transfer.send", {"text": marker, "target": "mobile"})
                assert _res(r).get("success") is True, f"A 端 send 失败: {r}"
                got = None
                deadline = asyncio.get_event_loop().time() + 20
                while asyncio.get_event_loop().time() < deadline:
                    try:
                        msg = await asyncio.wait_for(wsB.receive(), timeout=5)
                    except asyncio.TimeoutError:
                        break
                    if msg.type != aiohttp.WSMsgType.TEXT:
                        break
                    data = json.loads(msg.data)
                    if data.get("event_type") == "transfer.received" and data.get("payload", {}).get("text") == marker:
                        got = data["payload"]
                        break
                assert got is not None, "B 端未收到 transfer.received 事件"
                assert got.get("from_device") == "desktop"
            # B 端（新连接）读历史亦可见
        async with aiohttp.ClientSession() as ses2:
            async with ses2.ws_connect(f"{url}?client_version=1.2.0", timeout=10, headers=_auth()) as wsC:
                await wsC.send_json({"jsonrpc": "2.0", "id": 1, "method": "transfer.history", "params": {"limit": 10}})
                while True:
                    m = json.loads((await asyncio.wait_for(wsC.receive(), timeout=30)).data)
                    if m.get("id") == 1:
                        break
                assert any(h.get("text") == marker for h in m.get("result", [])), "跨连接历史不可见"

    async def test_file_journey_upload_list_download_delete(self, rpc, rpc_approved):
        content = f"ph-file-{TAG}".encode() * 100
        b64 = base64.b64encode(content).decode()
        name = f"ph-journey-{TAG}.bin"
        up = _res(await rpc("transfer.upload", {"name": name, "data": b64}))
        assert up.get("success") is True and up.get("size") == len(content), f"upload 失败: {up}"
        listed = _res(await rpc("transfer.files", {}))
        assert name in [f["name"] for f in listed], f"files 未列出: {listed}"
        dn = _res(await rpc("transfer.download", {"path": name}))
        assert dn.get("success") is True and base64.b64decode(dn["data"]) == content, "字节级不一致"
        # delete = R4：pending → approve → 重放
        d0 = _res(await rpc("transfer.delete", {"path": name}))
        if isinstance(d0, dict) and d0.get("pending"):
            ack = _res(await rpc("sandbox.approve", {"pending_id": d0["pending_id"]}))
            assert ack.get("success") is True and (ack.get("result") or {}).get("success") is True, f"审批重放失败: {ack}"
        elif not (isinstance(d0, dict) and d0.get("success")):
            pytest.fail(f"delete 形态异常: {d0}")
        listed2 = _res(await rpc("transfer.files", {}))
        assert name not in [f["name"] for f in listed2], "delete 后 files 仍列出（列表来自真实 listdir）"
        dn2 = _res(await rpc("transfer.download", {"path": name}))
        assert dn2.get("success") is not True, "delete 后仍可下载"

    async def test_upload_same_name_overwrites(self, rpc, rpc_approved):
        """PH-D3（已修复）：同名覆盖不阻断，但返回体必须如实带 overwritten=true。"""
        n = f"ph-ovwr-{TAG}.txt"
        r1 = _res(await rpc("transfer.upload", {"name": n, "data": base64.b64encode(b"FIRST").decode()}))
        r2 = _res(await rpc("transfer.upload", {"name": n, "data": base64.b64encode(b"SECOND").decode()}))
        assert r1.get("success") and r2.get("success")
        assert r1.get("overwritten") is False, f"首次上传误报 overwritten: {r1}"
        assert r2.get("overwritten") is True, f"PH-D3 回归：同名覆盖未在返回体如实告知: {r2}"
        d = _res(await rpc("transfer.download", {"path": n}))
        assert base64.b64decode(d["data"]) == b"SECOND", "覆盖后字节非新值"
        await rpc_approved("transfer.delete", {"path": n})

    async def test_send_file_path_ref_warning(self, rpc):
        r = _res(await rpc("transfer.send", {"text": f"{TAG}-ref", "file_path": "/phone/tmp/a.bin"}))
        assert r.get("success") is True and "warning" in r, f"file_path 引用未回警告: {r}"


# ─────────────────────────────────────────────────────────
# ⑤ 阅读 / 词书 写路径闭环
# ─────────────────────────────────────────────────────────
class TestReadingWordbook:
    async def test_article_roundtrip(self, rpc, rpc_approved):
        sv = _res(await rpc("reading.save_article", {"title": f"PH 探针文章 {TAG}", "url": "https://example.com/ph"}))
        assert sv.get("success") and sv.get("id"), f"save_article 失败: {sv}"
        aid = sv["id"]
        lst = _res(await rpc("reading.list_articles", {}))
        assert any(a["id"] == aid for a in lst.get("articles", [])), "文章列表读不到新存条目"
        dl = await rpc_approved("reading.delete_article", {"article_id": aid})
        assert (dl.get("result") or {}).get("success") is True or dl.get("success") is not False, f"delete_article: {dl}"
        lst2 = _res(await rpc("reading.list_articles", {}))
        assert all(a["id"] != aid for a in lst2.get("articles", [])), "delete 后文章仍在列表"
        # 读回不存在的删除 → 审批后执行，诚实返回 not found（R4 闸门先行入队是设计语义）
        dl2 = await rpc_approved("reading.delete_article", {"article_id": "nonexistent"})
        inner = dl2.get("result", dl2)
        inner = inner.get("result", inner)  # 审批 ack → 重放的真实 handler 结果
        assert inner.get("success") is False and inner.get("error"), f"幽灵删除形态: {dl2}"

    async def test_wordbook_fsrs_journey(self, rpc, rpc_approved):
        wb = _res(await rpc("wordbook.create", {"name": f"PH词本{TAG}"}))
        assert wb.get("success"), f"建本失败: {wb}"
        wb_id = wb["id"]
        wa = _res(await rpc("word.add", {"wordbook_id": wb_id, "word": f"phword{TAG}", "meaning": "探针词"}))
        assert wa.get("success") and wa.get("id"), f"word.add 失败: {wa}"
        wid = wa["id"]
        # 新词无 next_review → 立即到期
        c1 = _res(await rpc("word.count_due", {"wordbook_id": wb_id}))
        assert c1.get("count", 0) >= 1, f"新词未计到期: {c1}"
        due = _res(await rpc("word.list_due", {"wordbook_id": wb_id}))
        assert any(w.get("id") == wid for w in due), "list_due 缺新词"
        # 复习一次（R3 直通）
        rv = _res(await rpc("word.review", {"word_id": wid, "quality": 4}))
        assert rv.get("success") and rv.get("next_review"), f"word.review 失败: {rv}"
        assert rv.get("repetitions", 0) >= 1 and rv.get("interval", 0) >= 1, f"FSRS/SM-2 状态异常: {rv}"
        c2 = _res(await rpc("word.count_due", {"wordbook_id": wb_id}))
        assert c2.get("count", 99) == 0, f"复习后仍计到期: {c2}"
        # 词详情读回
        words = _res(await rpc("word.list", {"wordbook_id": wb_id}))
        mine = next((w for w in words if w.get("id") == wid), None)
        assert mine and mine.get("next_review"), f"复习字段未落库: {mine}"
        # 删词（R4 审批链）
        dd = await rpc_approved("word.delete", {"word_id": wid})
        res = dd.get("result", dd)
        assert res.get("success") is True, f"word.delete 审批链失败: {dd}"
        words2 = _res(await rpc("word.list", {"wordbook_id": wb_id}))
        assert all(w.get("id") != wid for w in words2), "删词后仍可列出"
        # 删本（R4）
        wd = await rpc_approved("wordbook.delete", {"wordbook_id": wb_id})
        res2 = wd.get("result", wd)
        assert res2.get("success") is True, f"wordbook.delete: {wd}"
        wbs = _res(await rpc("wordbook.list", {}))
        assert all(w.get("id") != wb_id for w in wbs), "删本后仍列出"

    async def test_word_review_validation(self, rpc, rpc_approved):
        wb = _res(await rpc("wordbook.create", {"name": f"PH词本V{TAG}"}))
        wid = _res(await rpc("word.add", {"wordbook_id": wb["id"], "word": f"phv{TAG}", "meaning": "m"}))["id"]
        r = _res(await rpc("word.review", {"word_id": wid, "quality": 99}))
        assert r.get("success") is False and "quality" in str(r.get("error", "")), f"quality 越界未拒: {r}"
        r = _res(await rpc("word.review", {"word_id": 99999999, "quality": 3}))
        assert r.get("success") is False, f"幽灵词复习未拒: {r}"
        await rpc_approved("word.delete", {"word_id": wid})
        await rpc_approved("wordbook.delete", {"wordbook_id": wb["id"]})
