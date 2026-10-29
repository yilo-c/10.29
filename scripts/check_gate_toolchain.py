#!/usr/bin/env python3
"""门禁工具链预检 —— 在跑 12 道门禁之前，先确认"尺子本身是准的"。

## 动机（2026-09-16 实测事故）

门禁连续多轮产出**假红**：`ruff` 与 `mypy 2.3.1` 分居两个解释器环境，
而各门禁脚本硬绑 `sys.executable -m <tool>` → 任何单一解释器都必然红一道，
且红的是哪道随解释器切换：

| 跑门禁用的解释器 | 观察到的"红项" | 真相 |
|---|---|---|
| `.venv3.11` | mypy 棘轮 | mypy 1.20.2 ≠ 基线版 2.3.1 |
| 2.3.1 环境（修复前） | ruff 棘轮 | 该环境无 ruff 模块 |
| hermes venv | mypy 棘轮 | 该环境无 mypy |

三个会话各自看到不同的红、都判为"环境性欠债"放过，真实质量信号被淹没。
本预检把"用哪个 python 跑门禁"从口口相传变成**机器断言**：
工具链不就绪 → 直接 BLOCKED 并给出修复指引，而不是静默产出假红。

## 检查项

1. ruff 可解析（PATH 独立二进制，或当前解释器模块）
2. mypy 可解析，且**版本与 scripts/mypy-baseline.txt 头部一致**
3. pip-audit 可解析（PATH 或模块）

退出码：0=就绪；1=存在阻塞项。
"""

from __future__ import annotations

import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _gate_tooling import (  # noqa: E402
    read_baseline_mypy_version,
    resolve_tool,
    running_mypy_version,
)

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MYPY_BASELINE = os.path.join(REPO_ROOT, "scripts", "mypy-baseline.txt")


def _probe(cmd: list[str]) -> tuple[bool, str]:
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        return False, repr(exc)
    out = ((proc.stdout or "") + (proc.stderr or "")).strip().splitlines()
    return proc.returncode == 0, (out[0] if out else "")


def check_ruff() -> tuple[bool, str]:
    cmd = [*resolve_tool("ruff"), "--version"]
    ok, first = _probe(cmd)
    if ok:
        return True, f"{first}（{'PATH' if cmd[0] != sys.executable else '当前解释器模块'}）"
    return False, (
        "ruff 不可用 —— 门禁会静默计成『零违规』或每文件 +1 假违规。\n"
        "     修复：pip install ruff，或把某个已装 ruff 环境的 ruff/ 与 Scripts/ruff.exe 拷入当前环境"
    )


def check_mypy() -> tuple[bool, str]:
    ver = running_mypy_version()
    if ver is None:
        return False, (
            "mypy 不可用 —— mypy 棘轮无法给出真值。\n"
            "     修复：改用带 mypy 的解释器，例如\n"
            '       "C:/Users/Administrator/.workbuddy/binaries/python/envs/default/Scripts/python.exe"'
        )
    expected = read_baseline_mypy_version(MYPY_BASELINE)
    if expected and expected != ver:
        return False, (
            f"mypy 版本与基线不符：基线={expected} 当前={ver} —— 比对必产假『新增错误』。\n"
            f"     处理：换回 {expected} 的解释器重跑；或确认升级有意为之后 "
            "--gen-baseline 重建基线"
        )
    suffix = f"（与基线 {expected} 一致）" if expected else "（基线无版本头，建议 --gen-baseline 补记）"
    return True, f"mypy {ver}{suffix}"


def check_pip_audit() -> tuple[bool, str]:
    cmd = [*resolve_tool("pip-audit", "pip_audit"), "--version"]
    ok, first = _probe(cmd)
    if ok:
        return True, first
    return False, ("pip-audit 不可用（无网/未安装）—— 该门禁为环境依赖项，非代码回归。\n     修复：在可联网环境重跑，或 pip install pip-audit")


def main() -> int:
    print("gate toolchain preflight:")
    results: list[tuple[str, bool, str]] = []
    for name, fn in (("ruff", check_ruff), ("mypy", check_mypy), ("pip-audit", check_pip_audit)):
        try:
            ok, detail = fn()
        except Exception as exc:  # noqa: BLE001
            ok, detail = False, f"预检自身异常: {exc!r}"
        results.append((name, ok, detail))
        print(f"  [{'PASS' if ok else 'FAIL'}] {name} -- {detail}")

    failed = [n for n, ok, _ in results if not ok]
    print("-" * 60)
    if failed:
        print(f"[gate-toolchain] BLOCKED: {len(failed)} 项不就绪（{', '.join(failed)}）")
        print("  说明：这**不是代码回归**，是『跑门禁的尺子不对』。")
        print("  参考 .workbuddy/memory/MEMORY.md「门禁解释器坑」——曾连续多轮把假红当欠债上报。")
        return 1
    print("[gate-toolchain] OK: 工具链与基线一致，后续 12 道门禁结果可信。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
