#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""B-14 登记册引用检查（防「登记了未提交」证据链断裂）。

扫描 deliverables/risk-register.md 每一行的「复测方式」列（反引号包裹的引用），
提取其中的测试文件路径（tests/xxx.py 或 test_xxx.py），并验证其**存在于 git 树**
（git ls-files --error-unmatch）。任一被引用的测试文件不在 git 树中 → 退出码 1（CI 红）。

设计意图（audit-report-2026-08-27.md 原则 P-2）：把「登记册引用的测试必须提交」
变成机器可断言的门禁，堵死「登记了复测方式却从未提交测试文件」的通道。

用法：
    python scripts/check_register_refs.py
退出码：
    0 = 所有被引用的测试文件均已入库
    1 = 存在被引用但未入库的测试文件
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
REGISTER = REPO_ROOT / "deliverables" / "risk-register.md"

# 反引号包裹的引用
_BACKTICK = re.compile(r"`([^`]+)`")
# 测试文件引用：tests/xxx.py 或 test_xxx.py
_TEST_REF = re.compile(r"(?:tests/)?(test_[A-Za-z0-9_]+\.py)")


def _git_tracked(path: Path) -> bool:
    """文件是否在 git 树中（已跟踪）。"""
    try:
        subprocess.run(
            ["git", "-C", str(REPO_ROOT), "ls-files", "--error-unmatch", str(path)],
            capture_output=True,
            text=True,
            check=True,
        )
        return True
    except Exception:
        return False


def _resolve(token: str):
    """将引用 token 解析为磁盘上存在的已跟踪测试路径；非测试引用返回 None；
    是测试引用但不入库返回 False。"""
    m = _TEST_REF.search(token)
    if not m:
        return None
    name = m.group(1)
    candidates = [
        REPO_ROOT / "思忆集app" / "tests" / name,
        REPO_ROOT / "tests" / name,
    ]
    if token.startswith("tests/"):
        candidates.append(REPO_ROOT / token)
    for c in candidates:
        if c.exists() and _git_tracked(c):
            return c
    # 文件可能已被跟踪但尚未出现在工作树（如全新 clone 未 checkout）——放宽到仅跟踪
    for c in candidates:
        if _git_tracked(c):
            return c
    return False


def main() -> int:
    if not REGISTER.exists():
        print(f"ERROR: register not found: {REGISTER}", file=sys.stderr)
        return 1

    lines = REGISTER.read_text(encoding="utf-8").splitlines()
    missing: list[str] = []
    seen: set[str] = set()
    referenced = 0

    for line in lines:
        if "|" not in line:
            continue
        for tok in _BACKTICK.findall(line):
            if tok in seen:
                continue
            seen.add(tok)
            res = _resolve(tok)
            if res is None:
                continue  # 非测试文件引用（如 ws_server.py:433-448），跳过
            referenced += 1
            if res is False:
                missing.append(tok)

    if missing:
        print("ERROR: risk-register references test files NOT in git tree:")
        for m in missing:
            print(f"  - {m}")
        print(
            "请先将这些测试文件提交入库（原则 P-2：测试与修复同 commit），"
            "或修正登记册中的引用。",
            file=sys.stderr,
        )
        return 1

    print(f"OK: all referenced test files ({referenced} unique) exist in git tree")
    return 0


if __name__ == "__main__":
    sys.exit(main())
