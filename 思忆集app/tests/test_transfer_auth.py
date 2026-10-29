"""L6 安全 —— 跨端传送（transfer）鉴权与路径穿越防护。

两层：
1. 组件级（确定性）：`_check_auth` 在 LAN / 配置 token 三种分支下的行为；
   `_resolve_file_store_path` 对 `..` 穿越、绝对路径、空字节的拒绝。
2. 集成级（只读 / 无副作用）：`transfer.token_info` 契约；`transfer.send`
   空参数返回"text or file required"（不落盘）。

注意：组件级用 monkeypatch 控制 `SIYI_TRANSFER_KEY` 环境变量，
不影响真实后端进程（后者在启动时已读取其 env）。
"""

from __future__ import annotations

import os
from pathlib import Path

from backend.handlers._transfer import _check_auth, _resolve_file_store_path


# ── 组件级：token 鉴权三分支 ────────────────────────────────────────────────


class TestTransferAuth:
    def test_lan_mode_no_key_allows_all(self, monkeypatch) -> None:
        """未配置 SIYI_TRANSFER_KEY → LAN 模式，任何请求放行。"""
        monkeypatch.delenv("SIYI_TRANSFER_KEY", raising=False)
        assert _check_auth() is None
        assert _check_auth(_token="anything") is None

    def test_key_required_missing_token(self, monkeypatch) -> None:
        """配置 token 后，缺 _token → auth required。"""
        monkeypatch.setenv("SIYI_TRANSFER_KEY", "test-secret")
        assert _check_auth() == "auth required (missing _token param)"
        assert _check_auth(_token="") == "auth required (missing _token param)"

    def test_key_invalid_token_rejected(self, monkeypatch) -> None:
        """错误 token → auth failed。"""
        monkeypatch.setenv("SIYI_TRANSFER_KEY", "test-secret")
        assert _check_auth(_token="wrong") == "auth failed (invalid _token)"
        # 前缀正确但整体不匹配也不行
        assert _check_auth(_token="test-secre") == "auth failed (invalid _token)"

    def test_key_valid_token_allowed(self, monkeypatch) -> None:
        """正确 token → 放行。"""
        monkeypatch.setenv("SIYI_TRANSFER_KEY", "test-secret")
        assert _check_auth(_token="test-secret") is None

    def test_key_takes_exact_match(self, monkeypatch) -> None:
        """大小写敏感精确匹配（非模糊）。"""
        monkeypatch.setenv("SIYI_TRANSFER_KEY", "SecretKey")
        assert _check_auth(_token="secretkey") == "auth failed (invalid _token)"


# ── 组件级：路径穿越防护 ────────────────────────────────────────────────────


class TestPathTraversal:
    def test_normal_path_resolves_inside_base(self, tmp_path: Path) -> None:
        base = str(tmp_path)
        target = _resolve_file_store_path(base, "transfer_files/a.txt")
        assert target is not None
        assert str(Path(target)).startswith(str(Path(base)))

    def test_traversal_rejected(self, tmp_path: Path) -> None:
        base = str(tmp_path)
        for evil in ("../evil.txt", "../../etc/passwd", "a/../../evil.txt"):
            assert _resolve_file_store_path(base, evil) is None, f"穿越未拦截: {evil}"

    def test_absolute_path_normalized_or_rejected(self, tmp_path: Path) -> None:
        base = str(tmp_path)
        # POSIX 风格绝对路径经 lstrip("/") 归一为 base 内相对路径（不逃逸，放行）
        target = _resolve_file_store_path(base, "/etc/passwd")
        assert target is not None
        assert str(Path(target)).startswith(str(Path(base)))
        # Windows 盘符绝对路径逃逸 base → 拒绝
        if os.name == "nt":
            assert _resolve_file_store_path(base, r"C:\Windows\system.ini") is None

    def test_nul_byte_sanitized(self, tmp_path: Path) -> None:
        base = str(tmp_path)
        # 空字节被剔除后若仍逃逸 base 则拒绝
        target = _resolve_file_store_path(base, "a\x00b.txt")
        assert target is not None  # 空字节剔除后落在 base 内

    def test_base_itself_allowed(self, tmp_path: Path) -> None:
        base = str(tmp_path)
        assert _resolve_file_store_path(base, "") == os.path.abspath(base)


# ── 集成级：契约观察（只读，无副作用） ─────────────────────────────────────


async def test_transfer_token_info_contract(rpc) -> None:
    """transfer.token_info 返回结构稳定（未鉴权，只报告状态）。"""
    info = (await rpc("transfer.token_info"))["result"]
    assert isinstance(info, dict)
    assert "token_required" in info and isinstance(info["token_required"], bool)
    # 七维-安C7 契约变更（2026-08-29）：token_prefix 前缀泄露已移除，不再返回
    assert "token_prefix" not in info
    assert info["mode"] in ("lan", "secured")


async def test_transfer_send_empty_returns_error(rpc) -> None:
    """空参数 send 优雅返回错误（不落盘，无副作用）。"""
    result = (await rpc("transfer.send", {}))["result"]
    assert isinstance(result, dict)
    assert "error" in result
    # LAN 模式下是"缺内容"；若运维配置了 token，则可能是鉴权拒绝 —— 均合法
    assert result["error"] in (
        "text or file required",
        "auth required (missing _token param)",
    )
