"""测试基建契约：守护「会话产物」类辅助模块不再次丢失（H2 结构防护）。

背景（2026-08-29 审查 H2）：``tests/_isolated_backend.py`` 曾以会话本地产物形式
存在、**从未入 git**，会话结束后丢失 → 4 个集成测试文件 ImportError →
pytest 收集阶段即整体中断，本地门禁全红却无人察觉数日。

本文件把"该模块必须存在且 API 齐备"固化为断言；同时守护"任何 tests/ 下的
模块不得处于被 git 忽略状态"（被忽略 = 下次会话仍会丢失）。
"""
from __future__ import annotations

import ast
import subprocess
from pathlib import Path

APP = Path(__file__).resolve().parents[1]  # 思忆集app
TESTS = APP / "tests"

REQUIRED_API = {"spawn_backend", "teardown_backend", "rpc"}


def test_isolated_backend_module_exists_with_api() -> None:
    mod = TESTS / "_isolated_backend.py"
    assert mod.is_file(), (
        "tests/_isolated_backend.py 缺失——4 个集成测试会因此 ImportError "
        "导致 pytest 收集整体中断（H2 复发）。见该文件头注释的契约说明。"
    )
    tree = ast.parse(mod.read_text(encoding="utf-8"))
    exported = {
        n.name
        for n in tree.body
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    missing = sorted(REQUIRED_API - exported)
    assert not missing, f"_isolated_backend 缺少必需 API：{missing}"


def test_no_test_module_is_gitignored() -> None:
    """tests/ 下被跟踪目录里的 .py 若被 gitignore，等同于埋下一颗"下次丢失"的雷。"""
    repo = APP.parent
    try:
        proc = subprocess.run(
            ["git", "check-ignore", "-z", "--stdin"],
            cwd=str(repo),
            input="\0".join(str(p.relative_to(repo)) for p in TESTS.rglob("*.py")),
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
    except FileNotFoundError:  # pragma: no cover - 无 git 环境（如 sdist 安装）
        return
    ignored = [p for p in proc.stdout.split("\0") if p]
    assert not ignored, (
        f"tests/ 下存在被 git 忽略的 .py，会话结束即丢失（H2 同类风险）：{ignored[:10]}"
    )
