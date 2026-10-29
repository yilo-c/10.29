#!/usr/bin/env python3
"""门禁工具解析 —— 让门禁与"用哪个 python 跑"解耦。

## 背景（2026-09-16 实测，详见 .workbuddy/memory/MEMORY.md「门禁解释器坑」）

各门禁脚本原先硬绑 `[sys.executable, "-m", "<tool>"]`，而 ruff 与 mypy 2.3.1
分居两个解释器环境：

| 解释器 | ruff | mypy |
|---|---|---|
| `.venv3.11` | 0.15.12 | 1.20.2（与基线不符） |
| `~/.workbuddy/binaries/python/envs/default` | 修复前无 | **2.3.1（基线版）** |
| hermes venv | 0.15.10 | 无 |

结果：**任何单一解释器都必然红一道**，且红的是哪道随解释器切换 →
S2 批报"红 pip-audit"、补测批报"红 pip-audit"、主线程复核报"红 mypy"、
换 2.3.1 后报"红 ruff"——全是假红，却被当成"环境性欠债"放过。

`run_pip_audit.py:47-48` 早已用 `shutil.which("pip-audit")` 兜底（故它始终
与解释器无关），本模块把该范式抽成公共实现，供 ruff / bandit 门禁复用。

## 用法

    from _gate_tooling import resolve_tool
    cmd = [*resolve_tool("ruff"), "check", "--output-format", "concise", str(f)]

mypy 例外：其**版本决定基线是否匹配**，不能只按 PATH 解析，故 `mypy_ratchet.py`
保留 `sys.executable -m mypy` 并额外做版本一致性守卫（见该脚本
`_mypy_version_guard`）。
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

__all__ = [
    "FAIL_CONTEXT_LINES",
    "MYPY_VERSION_HEADER_PREFIX",
    "indent_detail",
    "project_python",
    "read_baseline_mypy_version",
    "resolve_tool",
    "running_mypy_version",
    "tail_detail",
]

# FAIL 时保留的上下文行数。见 tail_detail() 的动机说明。
FAIL_CONTEXT_LINES = 20

# mypy 基线文件头部的版本标记。load_baseline() 用 LINE_RE 解析，非
# "path:line:col: error:" 形状的行会被自然忽略，故加头不破坏既有基线。
MYPY_VERSION_HEADER_PREFIX = "# mypy-version: "

_VERSION_RE = re.compile(r"(\d+\.\d+(?:\.\d+)?)")


def tail_detail(out: str, *, ok: bool, context_lines: int = FAIL_CONTEXT_LINES) -> str:
    """从子门禁输出中提取"结论行"。

    PASS → 只取末行（保持汇总输出整齐、可一眼扫过）
    FAIL → 保留末 ``context_lines`` 行

    为什么 FAIL 要保留上下文（2026-09-17 实测，代价约 2.5 分钟/次）：
    子门禁脚本的**末行往往是"提示：…"这类无信息尾注**，真因在倒数几行。
    典型：`gate_all` → `done_check` 红，`done_check` 末行是
    「提示：任一 FAIL 即不满足 M1『完成』定义」，真正的原因行
    「[FAIL] inventory 防漂移 -- DRIFT: …」被吞掉，只能再单独跑一次
    `done_check.py` 才定位。

    历史上这个"诊断税"三度把真红误判成「瞬时假红」（见
    .workbuddy/memory/MEMORY.md「单次红先复跑再定性」），故收敛为公共实现，
    避免每个门禁脚本各写一份 `tail[-1]`。
    """
    lines = [ln.rstrip() for ln in (out or "").splitlines() if ln.strip()]
    if not lines:
        return ""
    if ok:
        return lines[-1]
    return "\n".join(lines[-context_lines:])


def indent_detail(detail: str, spaces: int = 8) -> str:
    """把多行 detail 的续行缩进，使其在 `  [FAIL] 名称 -- <detail>` 后仍可读。"""
    if "\n" not in detail:
        return detail
    pad = " " * spaces
    head, *rest = detail.split("\n")
    return "\n".join([head, *(pad + ln for ln in rest)])


def resolve_tool(name: str, module: str | None = None) -> list[str]:
    """返回调用 `<name>` 的命令前缀。

    1. PATH 上存在同名可执行文件 → 直接用它（与解释器解耦，首选）
    2. 否则回退 `[sys.executable, "-m", module or name]`

    PATH 与模块都不可用时，调用方会收到 "No module named ..." 的 stderr；
    该情形由 `scripts/check_gate_toolchain.py` 预检拦下并给出明确指引，
    避免"工具缺失"被静默计成"零违规"或"每文件 +1 假违规"。
    """
    exe = shutil.which(name)
    if exe:
        return [exe]
    return [sys.executable, "-m", module or name]


def project_python() -> str:
    """项目虚拟环境解释器（跑 pytest 用）；不存在则回退当前解释器。

    为什么需要它（2026-09-16 实测）：`gate_all.py` 的 kwarg/facade 门禁是
    **pytest 项**，依赖项目依赖（aiohttp 等）；而 mypy 棘轮需要的是 mypy 2.3.1
    那个解释器。两者不是同一个环境 —— 用 `sys.executable` 跑 pytest 会在
    mypy 环境下报 `No module named pytest`（同一「解释器错配」病灶的第二处）。

    故：**静态门禁跟着"你启动 gate_all 用的解释器"，pytest 门禁固定用项目 venv。**
    推荐启动方式（两个条件同时满足）：

        "~/.workbuddy/binaries/python/envs/default/Scripts/python.exe" scripts/gate_all.py
    """
    for rel in (os.path.join(".venv3.11", "Scripts", "python.exe"), os.path.join(".venv3.11", "bin", "python")):
        cand = os.path.join(REPO_ROOT, rel)
        if os.path.isfile(cand):
            return cand
    return sys.executable


def running_mypy_version() -> str | None:
    """当前解释器下 mypy 的版本号（如 "2.3.1"）；不可用时返回 None。

    刻意走 `sys.executable -m mypy` 而非 PATH —— mypy 的**版本**决定基线是否
    匹配，必须与 `mypy_ratchet.run_mypy()` 使用同一个 mypy。
    """
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "mypy", "--version"],
            capture_output=True,
            text=True,
            timeout=60,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    out = (proc.stdout or "") + (proc.stderr or "")
    m = _VERSION_RE.search(out)
    return m.group(1) if m else None


def read_baseline_mypy_version(path: str) -> str | None:
    """读取 mypy 基线头部记录的生成版本；无头部（旧基线）返回 None。"""
    try:
        with open(path, encoding="utf-8") as f:
            for ln in f:
                if ln.startswith(MYPY_VERSION_HEADER_PREFIX):
                    return ln[len(MYPY_VERSION_HEADER_PREFIX) :].strip()
                if ln.strip() and not ln.lstrip().startswith("#"):
                    break  # 已进入错误行，说明无头部
    except OSError:
        return None
    return None
