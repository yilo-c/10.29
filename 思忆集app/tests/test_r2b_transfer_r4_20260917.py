# -*- coding: utf-8 -*-
"""R2-B 批（2026-09-17）回归：双端传送读写对称 + file.write R4 审批面。

覆盖 5 条已实测定案缺陷（每条一个测试类）：
- PH-D1/D2：transfer.upload 带 folder 归档的文件，download/delete 用**同一相对
  路径**往返成功（此前只认根目录 basename → 带 folder 的附件必然读不到/删不掉）；
- PH-F2：盘符 / 绝对路径显式拒绝（此前被 os.path.basename 收敛成裸文件名静默收下）；
- PH-D3：同名 upload 返回体带 ``overwritten: true``（此前 os.replace 静默覆盖）；
- PH-A1：transfer token 死孤岛删除 —— ``_check_auth`` 只认 env ``SIYI_TRANSFER_KEY``，
  不再生成/读取 settings.transfer_token；
- D6：file.write 纳入 R4（39→40）+ 路径守卫先于高危标签 + 审批重放才执行。

证明层级（如实标注）：
- transfer.* / token：**组件级真跑**（真 MethodRegistry + tmp_path，无活后端、无 WS）；
- file.write：**进程内端到端**：register_all → dispatch 入队 pending → 真 SandboxManager
  审批 → skip_approval 重放执行（假 capability_router，不含 WS 传输层与前端）。
"""

from __future__ import annotations

import base64
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

import backend.handlers._transfer as transfer_mod
from backend.handlers import _R4_METHODS, _sandbox, register_all
from backend.handlers._transfer import _check_auth, register_transfer
from backend.method_registry import RISK_R4, MethodRegistry, is_r4_approved_replay

TRANSFER_SRC = (APP_ROOT / "backend" / "handlers" / "_transfer.py").read_text(encoding="utf-8")


# ═══════════════════════════════════════════════════════════════
# 夹具：LAN 模式 transfer registry（tmp_path 隔离，不触真实 data/）
# ═══════════════════════════════════════════════════════════════


@pytest.fixture
def transfer_env(monkeypatch, tmp_path: Path):
    monkeypatch.delenv("SIYI_TRANSFER_KEY", raising=False)
    ctx = SimpleNamespace(local_data_store=SimpleNamespace(base_dir=str(tmp_path)))
    registry = MethodRegistry()
    register_transfer(registry, ctx)
    return registry, tmp_path


# ═══════════════════════════════════════════════════════════════
# PH-D1 / D2：folder 归档文件往返（写 → 读 → 删 → 不存在）
# ═══════════════════════════════════════════════════════════════


class TestFolderArchiveRoundTrip:
    async def test_upload_download_delete_with_folder(self, transfer_env):
        registry, tmp_path = transfer_env
        blob = b"attachment-bytes-roundtrip"
        up = await registry.dispatch(
            "transfer.upload",
            {"name": "a.png", "data": base64.b64encode(blob).decode(), "folder": "notes/n1"},
        )
        assert up.get("success") is True, up
        assert up["path"] == "notes/n1/a.png"
        landed = tmp_path / "transfer_files" / "notes" / "n1" / "a.png"
        assert landed.exists(), f"未按 folder 归档：{landed}"

        # 写后读：download 按 upload 返回的同一相对路径定位
        down = await registry.dispatch("transfer.download", {"path": up["path"]})
        assert down.get("success") is True, down
        assert base64.b64decode(down["data"]) == blob
        assert down["size"] == len(blob)

        # 删：同一相对路径（transfer.delete 属 R4，dispatch 层会入审批队列 ——
        # 本测试关注路径读写对称，故直调 handler，与 test_handlers_transfer 同法）
        dele = await registry._handlers["transfer.delete"](path=up["path"])
        assert dele.get("success") is True, dele

        # 删后不存在：文件与 download 双双确认
        assert not landed.exists(), "删除后文件仍在磁盘"
        gone = await registry.dispatch("transfer.download", {"path": up["path"]})
        assert gone.get("success") is False
        assert gone.get("error") == "file not found"

    async def test_root_file_still_roundtrips(self, transfer_env):
        """回归守卫：不带 folder 的根文件行为不变（basename 路径仍可用）。"""
        registry, tmp_path = transfer_env
        blob = b"plain"
        up = await registry.dispatch("transfer.upload", {"name": "b.bin", "data": base64.b64encode(blob).decode()})
        assert up["path"] == "b.bin"
        down = await registry.dispatch("transfer.download", {"path": "b.bin"})
        assert down.get("success") is True
        assert base64.b64decode(down["data"]) == blob
        dele = await registry._handlers["transfer.delete"](path="b.bin")
        assert dele.get("success") is True
        assert not (tmp_path / "transfer_files" / "b.bin").exists()


# ═══════════════════════════════════════════════════════════════
# PH-F2：盘符 / 绝对路径显式拒绝（不再 basename 收敛）
# ═══════════════════════════════════════════════════════════════


class TestAbsolutePathRejected:
    async def test_upload_drive_path_rejected(self, transfer_env):
        registry, tmp_path = transfer_env
        for evil in ("C:/Windows/win.ini", r"C:\Windows\win.ini", "/etc/passwd"):
            res = await registry.dispatch(
                "transfer.upload",
                {"name": evil, "data": base64.b64encode(b"x").decode()},
            )
            assert res.get("success") is False, f"{evil} 竟被收下：{res}"
            assert "absolute path not allowed" in res.get("error", ""), res
        # 不得留下任何被收敛出来的裸文件
        root = tmp_path / "transfer_files"
        leftover = sorted(p.name for p in root.rglob("*") if p.is_file()) if root.exists() else []
        assert leftover == [], f"盘符路径被静默落盘：{leftover}"

    async def test_download_delete_drive_path_rejected(self, transfer_env):
        registry, tmp_path = transfer_env
        # 先放一个真实文件，确保拒绝不是因为"文件不存在"
        (tmp_path / "transfer_files").mkdir(parents=True, exist_ok=True)
        (tmp_path / "transfer_files" / "win.ini").write_bytes(b"real")
        for evil in ("C:/Windows/win.ini", r"C:\Windows\win.ini"):
            down = await registry.dispatch("transfer.download", {"path": evil})
            assert down.get("success") is False, down
            assert "absolute path not allowed" in down.get("error", ""), down
            # transfer.delete 属 R4 → 直调 handler 验路径判定（同 test_handlers_transfer）
            dele = await registry._handlers["transfer.delete"](path=evil)
            assert dele.get("success") is False, dele
            assert "absolute path not allowed" in dele.get("error", ""), dele
        assert (tmp_path / "transfer_files" / "win.ini").exists(), "拒绝路径不应误删同名根文件"

    def test_resolver_rejects_drive_form(self, tmp_path: Path):
        """组件级：_resolve_file_store_path 对盘符形态返回 None（平台无关）。"""
        assert transfer_mod._resolve_file_store_path(str(tmp_path), r"C:\Windows\system.ini") is None
        assert transfer_mod._resolve_file_store_path(str(tmp_path), "D:/x/y.txt") is None
        # 回归：POSIX 前导斜杠仍按 base 内归一（既有契约，test_transfer_auth 已锁定）
        assert transfer_mod._resolve_file_store_path(str(tmp_path), "/etc/passwd") is not None


# ═══════════════════════════════════════════════════════════════
# PH-D3：同名覆盖诚实告知
# ═══════════════════════════════════════════════════════════════


class TestOverwriteNotice:
    async def test_second_upload_marks_overwritten(self, transfer_env):
        registry, tmp_path = transfer_env
        first = await registry.dispatch(
            "transfer.upload",
            {"name": "same.txt", "data": base64.b64encode(b"v1").decode()},
        )
        assert first.get("success") is True
        assert first.get("overwritten") is False

        second = await registry.dispatch(
            "transfer.upload",
            {"name": "same.txt", "data": base64.b64encode(b"v2-longer").decode()},
        )
        assert second.get("success") is True, "覆盖不阻断上传"
        assert second.get("overwritten") is True, f"同名覆盖未标记 overwritten：{second}"
        assert (tmp_path / "transfer_files" / "same.txt").read_bytes() == b"v2-longer"

    async def test_folder_overwrite_marks_overwritten(self, transfer_env):
        registry, _ = transfer_env
        payload = {"name": "x.bin", "data": base64.b64encode(b"a").decode(), "folder": "notes/n9"}
        first = await registry.dispatch("transfer.upload", payload)
        assert first.get("overwritten") is False
        second = await registry.dispatch("transfer.upload", payload)
        assert second.get("overwritten") is True


# ═══════════════════════════════════════════════════════════════
# PH-A1：token 死孤岛删除（单一凭证模型）
# ═══════════════════════════════════════════════════════════════


class _NoSettingsAccess:
    """任何 settings 读取/写入都会炸 —— 用于证明注册期不再碰 settings token。"""

    def get_setting(self, *a, **kw):  # noqa: ANN002, ANN003
        raise AssertionError("transfer 子系统不得再读取 settings（PH-A1 已删除配对凭证）")

    def set_setting(self, *a, **kw):  # noqa: ANN002, ANN003
        raise AssertionError("transfer 子系统不得再写入 settings（PH-A1 已删除配对凭证）")


class TestTokenIslandRemoved:
    def test_dead_helpers_deleted(self):
        for name in ("_persist_token_with_retry", "_get_or_create_token", "_TRANSFER_TOKEN", "_SETTINGS_KEY"):
            assert not hasattr(transfer_mod, name), f"死代码未删除：{name}"

    def test_source_has_no_settings_token_path(self):
        # 剥离注释与 docstring 说明性文字后，源码不得再有 settings token 通路
        assert "set_setting" not in TRANSFER_SRC
        assert "get_setting" not in TRANSFER_SRC
        assert "secrets." not in TRANSFER_SRC
        assert "transfer_token.txt" not in TRANSFER_SRC

    def test_registration_does_not_touch_settings(self, tmp_path: Path):
        """真实回归：注册 handler 不读/写 settings（旧实现会 get/set transfer_token）。"""
        ctx = SimpleNamespace(
            local_data_store=SimpleNamespace(base_dir=str(tmp_path)),
            settings_repository=_NoSettingsAccess(),
        )
        register_transfer(MethodRegistry(), ctx)  # 不抛即通过

    def test_check_auth_only_env_credential(self, monkeypatch):
        monkeypatch.delenv("SIYI_TRANSFER_KEY", raising=False)
        assert _check_auth() is None
        monkeypatch.setenv("SIYI_TRANSFER_KEY", "env-only-key")
        assert _check_auth() == "auth required (missing _token param)"
        assert _check_auth(_token="wrong") == "auth failed (invalid _token)"
        assert _check_auth(_token="env-only-key") is None

    async def test_token_info_states_single_credential_model(self, transfer_env):
        registry, _ = transfer_env
        info = await registry.dispatch("transfer.token_info", {})
        assert info["auth_model"] == "ws_auth_token"
        assert info["mode"] == "lan" and info["token_required"] is False
        assert "配对凭证" in info.get("note", "")


# ═══════════════════════════════════════════════════════════════
# D6：file.write ∈ R4 + 路径守卫先行 + 审批重放才执行
# ═══════════════════════════════════════════════════════════════


def _make_ctx(tmp_path: Path, router=None) -> SimpleNamespace:
    """register_all 所需的最小 context（隔离 tmp_path；settings 用 MagicMock）。"""
    ctx = SimpleNamespace(
        local_data_store=SimpleNamespace(base_dir=str(tmp_path)),
        settings_repository=MagicMock(),
    )
    if router is not None:
        ctx.capability_router = router
    return ctx


def _fake_capability_router(tmp_path: Path):
    """假 capability_router：真 FileCapabilityDomain（同一份写路径白名单）+ 调用记录。"""
    from assistant.capability._file_ops import FileCapabilityDomain

    ws_root = tmp_path / "ws"
    ws_root.mkdir(parents=True, exist_ok=True)
    data_root = tmp_path / "data"
    data_root.mkdir(parents=True, exist_ok=True)

    calls: list[dict] = []

    async def _execute_direct(action_id: str, args=None, approved: bool = False):
        calls.append({"action_id": action_id, "args": dict(args or {}), "approved": approved})
        return {"success": True, "path": (args or {}).get("path"), "approved": approved}

    router = SimpleNamespace(
        shell_execution=SimpleNamespace(workspace_root=str(ws_root)),
        data_store=SimpleNamespace(base_dir=str(data_root)),
        write_guard=None,
        _DEFAULT_DOWNLOAD_DIRNAME="assistant_downloads",
    )
    router.file_domain = FileCapabilityDomain(router)
    router.execute_direct = _execute_direct
    return router, calls


class TestFileWriteR4:
    def test_method_in_r4_list(self):
        from _r4_baseline import assert_r4_not_shrunk

        assert "file.write" in _R4_METHODS, "file.write 未纳入 R4 集中清单（无审批通路）"
        # 批4（2026-09-18）：原为硬编码等号 `== 40`，新增合法 R4 方法即假红
        # （本批 knowledge.entity.delete → 41）。改用单点下界基线。
        assert_r4_not_shrunk(_R4_METHODS)

    async def test_unapproved_pending_then_approved_replays(self, tmp_path: Path):
        """进程内端到端：未审批 → pending（不执行）；sandbox.approve → 重放执行。"""
        router, calls = _fake_capability_router(tmp_path)
        ctx = _make_ctx(tmp_path, router)
        reg = MethodRegistry()
        await register_all(reg, ctx, ws_port=18765)

        # ① 未审批：入队 pending，handler 未执行（execute_direct 零调用）
        res = await reg.dispatch("file.write", {"path": "ok.txt", "content": "hello"})
        assert res.get("pending") is True, f"file.write 未审批应入审批队列：{res}"
        assert res.get("requires_approval") is True
        assert calls == [], "未审批就执行了写操作（R4 闸门失效）"

        # ② 真审批（reject 路径的反面）：sandbox.approve → skip_approval 重放
        pid = res["pending_id"]
        approved = await _sandbox._handle_sandbox_approve(pending_id=pid, registry=reg)
        assert approved.get("success") is True, approved
        assert approved.get("action_id") == "file.write"
        assert len(calls) == 1, f"审批后执行次数异常：{calls}"
        assert calls[0]["approved"] is True, "重放未携带人工授权（会被 llm_source_high_risk 二次拦截）"
        assert calls[0]["args"]["path"] == "ok.txt"
        assert approved["result"]["success"] is True

    async def test_rejected_pending_never_executes(self, tmp_path: Path):
        router, calls = _fake_capability_router(tmp_path)
        ctx = _make_ctx(tmp_path, router)
        reg = MethodRegistry()
        await register_all(reg, ctx, ws_port=18765)

        res = await reg.dispatch("file.write", {"path": "ok.txt", "content": "hello"})
        await _sandbox._handle_sandbox_reject(pending_id=res["pending_id"])
        assert calls == [], "拒绝后仍执行了写操作"

    async def test_path_guard_runs_before_broker_high_risk(self, tmp_path: Path):
        """D6①：越界路径给出明确原因，且**不**进入执行（不被高危标签掩盖）。"""
        router, calls = _fake_capability_router(tmp_path)
        ctx = _make_ctx(tmp_path, router)
        reg = MethodRegistry()
        await register_all(reg, ctx, ws_port=18765)

        out = await reg.dispatch(
            "file.write",
            {"path": "C:/Windows/__r2b_probe__.txt", "content": "x"},
            skip_approval=True,  # 即便走审批重放，越界路径也必须先被路径守卫拒绝
        )
        assert isinstance(out, dict) and out.get("success") is False, out
        assert out.get("reason") == "path_outside_allowed_root", out
        assert "path rejected" in out.get("error", ""), out
        assert calls == [], "越界路径不得进入执行层"

    async def test_replay_marker_semantics(self, tmp_path: Path):
        """标记只在 skip_approval 重放时置位（不是无条件免审）。"""
        ctx = _make_ctx(tmp_path)
        reg = MethodRegistry()
        await register_all(reg, ctx, ws_port=18765)

        seen: list[bool] = []

        async def probe(**_kw):
            seen.append(is_r4_approved_replay())
            return {"ok": True}

        reg.register("test.r2b_replay_probe", probe, risk=RISK_R4)

        pending = await reg.dispatch("test.r2b_replay_probe", {})
        assert pending.get("pending") is True
        assert seen == [], "未审批的 R4 调用不应执行 handler"

        await reg.dispatch("test.r2b_replay_probe", {}, skip_approval=True)
        assert seen == [True], "审批重放未置位标记（file.write 会被二次高危拦截）"

        # 普通方法（非重放）拿不到标记
        other: list[bool] = []

        async def plain(**_kw):
            other.append(is_r4_approved_replay())
            return {}

        reg.register("test.r2b_plain_probe", plain)
        await reg.dispatch("test.r2b_plain_probe", {})
        assert other == [False], "非审批重放的调用不得拿到审批标记"
