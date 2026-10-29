"""N4 / N5 认证链回归测试（B-14 补回，原 test_auth_chain.py 从未入库）。

N4（/auth 无轮换 / 吊销）：auth_token_store.rotate 生成新 token 并持久化；
    重复 rotate 产出不同 token（轮换生效）。
N5（Windows 零 ACL）：token 文件持久化后 ACL 应被收紧；acl_is_hardened 是
    真实可用的自检函数（非空实现）。Windows 上需 icacls 在场才做正向断言，
    否则 skip（避免沙箱 / CI 环境差异造成假红）。
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest

from backend.auth_token_store import (
    acl_is_hardened,
    get_token_path,
    load_or_create,
    rotate,
)


class TestN4TokenRotation:
    def test_rotate_generates_new_token_and_persists(self, tmp_path: Path):
        token = rotate(str(tmp_path))
        # secrets.token_urlsafe(32) → 43 字符
        assert isinstance(token, str) and len(token) == 43
        path = get_token_path(tmp_path)
        assert path.exists()
        # 重新加载应得到同一 token（持久化生效）
        reloaded, created = load_or_create(str(tmp_path))
        assert reloaded == token
        assert created is False

    def test_rotate_changes_token_each_time(self, tmp_path: Path):
        t1 = rotate(str(tmp_path))
        t2 = rotate(str(tmp_path))
        assert t1 != t2


class TestN5AclHardening:
    def test_acl_is_hardened_returns_bool(self, tmp_path: Path):
        # 不存在的路径：win32→False，posix→True；至少返回 bool（非空实现）
        assert isinstance(acl_is_hardened(get_token_path(tmp_path)), bool)

    def test_token_file_hardened_after_persist(self, tmp_path: Path):
        load_or_create(str(tmp_path))
        path = get_token_path(tmp_path)
        if os.name == "nt":
            if shutil.which("icacls"):
                assert acl_is_hardened(path) is True
            else:
                pytest.skip("icacls unavailable in this environment")
        else:
            # POSIX 侧 chmod 0600 已由 persist 执行，acl_is_hardened 恒 True
            assert acl_is_hardened(path) is True


class TestAclIsHardenedParsing:
    """★ 2026-09-18 修复（先红后绿）：路径本体**不得**参与 ACE 匹配。

    历史缺陷：`acl_is_hardened` 对 icacls 的**整段输出**做子串匹配，而 token 文件
    必然位于 ``%LOCALAPPDATA%``（即 ``C:\\Users\\<name>\\...``）——路径里的
    ``\\Users\\`` 会命中粗粒度 marker ``r"\\users"`` →
    **任何 Windows 机器上该函数恒返回 False**，ACL 自检形同虚设（自己骗自己）。

    修法：只扫每行中**文件路径之后**的 ACE 部分。下面用桩输出固化判据，
    不依赖本机是否真的装了 icacls / 是否中文 locale。
    """

    @staticmethod
    def _stub_icacls(monkeypatch, stdout: str, returncode: int = 0) -> None:
        import subprocess

        class _Proc:
            def __init__(self) -> None:
                self.returncode = returncode
                self.stdout = stdout
                self.stderr = ""

        # auth_token_store 里是 `import subprocess; subprocess.run(...)`，
        # 与此处拿到的是同一个模块对象 → 替换其 run 属性即可。
        monkeypatch.setattr(subprocess, "run", lambda *a, **k: _Proc())

    def test_path_containing_users_is_not_mistaken_for_ace(self, tmp_path: Path, monkeypatch) -> None:
        """路径含 `\\Users\\` 但 ACE 只有管理员 → 必须判为**已收紧**。"""
        if os.name != "nt":
            pytest.skip("本判据针对 Windows icacls 输出")
        path = tmp_path / "auth_token"
        path.write_text("x", encoding="utf-8")
        self._stub_icacls(
            monkeypatch,
            f"{path} PC-20230405JYXU\\Administrator:(F)\n\n已成功处理 1 个文件\n",
        )
        assert acl_is_hardened(path) is True

    def test_builtin_users_ace_is_detected(self, tmp_path: Path, monkeypatch) -> None:
        """ACE 里真出现 BUILTIN\\Users → 必须判为**未收紧**（真阳性不能丢）。"""
        if os.name != "nt":
            pytest.skip("本判据针对 Windows icacls 输出")
        path = tmp_path / "auth_token"
        path.write_text("x", encoding="utf-8")
        self._stub_icacls(
            monkeypatch,
            f"{path} BUILTIN\\Users:(R)\n",
        )
        assert acl_is_hardened(path) is False

    def test_continuation_line_ace_is_detected(self, tmp_path: Path, monkeypatch) -> None:
        """★ 续行 ACE 不得被漏判（首版修复的假阴性）。

        icacls 的真实输出把同一文件的**其余 ACE** 缩进排到**后续行**（实测：
        首行只挂第一条，SYSTEM / Administrators / OWNER RIGHTS 分列其后），
        这些行**不含文件路径**。首版修复对"不含路径的行"一律 ``continue``，
        于是粗粒度 ACE 只要排在续行就会被整个跳过 → 明明没收紧却报"已收紧"。
        本用例用真实形态的桩输出钉死这条判据。
        """
        if os.name != "nt":
            pytest.skip("本判据针对 Windows icacls 输出")
        path = tmp_path / "auth_token"
        path.write_text("x", encoding="utf-8")
        self._stub_icacls(
            monkeypatch,
            f"{path} NT AUTHORITY\\SYSTEM:(I)(F)\n                    BUILTIN\\Users:(I)(RX)\n\n已成功处理 1 个文件; 处理 0 个文件时失败\n",
        )
        assert acl_is_hardened(path) is False

    def test_summary_line_alone_is_not_an_ace(self, tmp_path: Path, monkeypatch) -> None:
        """只有汇总行（无 ACE）→ 保守判 False，不能被当成"已收紧"。"""
        if os.name != "nt":
            pytest.skip("本判据针对 Windows icacls 输出")
        path = tmp_path / "auth_token"
        path.write_text("x", encoding="utf-8")
        self._stub_icacls(
            monkeypatch,
            f"{path}\n\n已成功处理 1 个文件; 处理 0 个文件时失败\n",
        )
        assert acl_is_hardened(path) is False

    def test_empty_output_is_not_treated_as_hardened(self, tmp_path: Path, monkeypatch) -> None:
        """空输出 / 只有汇总行 → 保守判 False（看不到 ACE 不能算通过）。"""
        if os.name != "nt":
            pytest.skip("本判据针对 Windows icacls 输出")
        path = tmp_path / "auth_token"
        path.write_text("x", encoding="utf-8")
        self._stub_icacls(monkeypatch, "")
        assert acl_is_hardened(path) is False
