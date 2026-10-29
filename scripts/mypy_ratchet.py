#!/usr/bin/env python3
"""
mypy 棘轮门禁（T1.2 补全）。

策略：与 ruff / bandit 棘轮一致——存量类型错误不阻塞，仅拦截"新增"错误。
基线文件 scripts/mypy-baseline.txt 记录当前全量 mypy 错误指纹；
后续修改若引入基线外的新错误，则阻塞 CI。修复存量后应从基线中移除对应行。

用法：
    python scripts/mypy_ratchet.py [--base main] [--baseline scripts/mypy-baseline.txt]
        --check-changed  仅检查相对 base 分支新增/修改的 .py 文件中出现的新错误
        （无此 flag 则检查全仓，用于本地验证基线是否过期）

指纹：取 mypy 输出的 "path:line:col: error: ... [code]" 中的
      "path:line:col: [code]" 部分作为稳定指纹（忽略具体消息文本，避免重构误报）。
"""

import argparse
import os
import re
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _gate_tooling import (  # noqa: E402  （scripts/ 内公共模块）
    MYPY_VERSION_HEADER_PREFIX,
    read_baseline_mypy_version,
    running_mypy_version,
)

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP_DIR = os.path.join(REPO_ROOT, "思忆集app")
BASELINE = os.path.join(REPO_ROOT, "scripts", "mypy-baseline.txt")

# mypy 单行: <relpath>:<line>:<col>: error: <msg>  [code]
LINE_RE = re.compile(r"^(?P<path>.+?):(?P<line>\d+):(?P<col>\d+):\s*error:.*?(?:\[(?P<code>[a-z-]+)\])?\s*$")


def fingerprint(line: str) -> str | None:
    m = LINE_RE.match(line.strip())
    if not m:
        return None
    code = m.group("code") or "unknown"
    return f"{m.group('path')}:{m.group('line')}:{m.group('col')}:[{code}]"


def file_code_signature(fp: str) -> str:
    """把指纹降维成 `path:[code]`（去掉行/列）。

    用途：基线匹配的行号容错——编辑导致既有错误行号漂移时，只要该文件
    基线里已存在同 code 错误（同一病灶），就视为存量而非"新增"。
    """
    path = fp.split(":")[0]
    code = fp.rsplit("[", 1)[-1].rstrip("]")
    return f"{path}:[{code}]"


def load_baseline(path: str) -> set[str]:
    fps = set()
    if not os.path.exists(path):
        return fps
    with open(path, encoding="utf-8") as f:
        for ln in f:
            fp = fingerprint(ln)
            if fp:
                fps.add(fp)
    return fps


def run_mypy() -> list[str]:
    proc = subprocess.run(
        [sys.executable, "-m", "mypy", ".", "--ignore-missing-imports", "--show-column-numbers"],
        cwd=APP_DIR,
        capture_output=True,
        text=True,
        # 子进程编码纪律：本仓路径含中文，Windows 默认 GBK 解码会直接
        # UnicodeDecodeError（实测 `--check-changed` 在 git 输出上崩过）。
        encoding="utf-8",
        errors="replace",
    )
    return proc.stdout.splitlines()


def changed_python_files(base: str) -> set[str]:
    """返回相对 APP_DIR 的新增/修改 .py 文件集合。

    CI 用 origin/<base>...HEAD；本地优先 base...HEAD（base 分支在场时含已提交
    改动），ref 不可解析时退回未提交工作区改动（git diff HEAD）。
    """
    specs = [f"origin/{base}...HEAD", f"{base}...HEAD", "HEAD"]
    for spec in specs:
        try:
            res = subprocess.run(
                ["git", "diff", "--name-only", "--diff-filter=AM", spec],
                cwd=REPO_ROOT,
                capture_output=True,
                text=True,
                # 同上：git 输出的中文路径在 GBK 解码下直接报错（本仓必现）。
                encoding="utf-8",
                errors="replace",
            )
        except Exception:
            continue
        if res.returncode != 0:
            continue  # ref 不存在（如无 origin/main）→ 试下一个候选
        files = set()
        for ln in res.stdout.splitlines():
            ln = ln.strip().replace("/", os.sep)
            if ln.endswith(".py") and os.path.isfile(os.path.join(REPO_ROOT, ln)):
                rel = os.path.relpath(os.path.join(REPO_ROOT, ln), APP_DIR)
                files.add(rel.replace(os.sep, "/"))
        return files
    return set()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="main")
    ap.add_argument("--baseline", default=BASELINE)
    ap.add_argument("--check-changed", action="store_true")
    ap.add_argument(
        "--gen-baseline",
        action="store_true",
        help="用当前全量 mypy 输出重建基线文件（M3 事实生成不复制：基线=当前全部已知错误；随后按病灶修复并移除条目）",
    )
    args = ap.parse_args()

    if args.gen_baseline:
        lines = run_mypy()
        errors = [ln.strip() for ln in lines if " error:" in ln]
        ver = running_mypy_version()
        if ver is None:
            print("[mypy ratchet] 拒绝生成基线：当前解释器没有 mypy。")
            print(f"  当前解释器: {sys.executable}")
            print("  基线必须记录生成它的 mypy 版本，否则后续比对全是假『新增错误』。")
            return 1
        with open(args.baseline, "w", encoding="utf-8") as f:
            f.write(f"{MYPY_VERSION_HEADER_PREFIX}{ver}\n")
            f.write("\n".join(errors) + "\n")
        print(f"[mypy ratchet] 基线已重建: mypy {ver} / {len(errors)} 条当前错误 -> {args.baseline}")
        return 0

    baseline = load_baseline(args.baseline)

    # 版本守卫（2026-09-16）：mypy 的**版本**决定错误集合，版本不符时比对
    # 必然产出大量假"新增错误"（实测 1.20.2 vs 2.3.1：同一基线多出 13 条
    # 假新增）。历史上多个会话把这类假红当"环境性欠债"放过 —— 改为响亮报错。
    running_ver = running_mypy_version()
    if running_ver is None:
        print("[mypy ratchet] BLOCKED: 当前解释器没有 mypy，门禁无法给出真值。")
        print(f"  当前解释器: {sys.executable}")
        print("  修复：改用带 mypy 的解释器，例如")
        print(
            '    "C:/Users/Administrator/.workbuddy/binaries/python/envs/default/Scripts/python.exe"'
            " scripts/mypy_ratchet.py --check-changed --base main"
        )
        return 1
    baseline_ver = read_baseline_mypy_version(args.baseline)
    if baseline_ver and running_ver != baseline_ver:
        print("[mypy ratchet] BLOCKED: mypy 版本与基线不符 —— 继续比对只会产出假『新增错误』。")
        print(f"  基线生成版本: {baseline_ver}    当前版本: {running_ver}")
        print(f"  当前解释器:   {sys.executable}")
        print("  处理（二选一）：")
        print("    1) 换回基线版本的解释器重跑（推荐；见 .workbuddy/memory/MEMORY.md「门禁解释器坑」）")
        print("    2) 确认版本升级是有意为之 → python scripts/mypy_ratchet.py --gen-baseline")
        return 1

    lines = run_mypy()

    # 收集当前错误指纹
    current = {}
    for ln in lines:
        fp = fingerprint(ln)
        if fp:
            current.setdefault(fp, ln.strip())

    if args.check_changed:
        changed = changed_python_files(args.base)
        if not changed:
            print(f"[mypy ratchet] no changed python files vs {args.base}; skipping.")
            return 0
        # 行号容错：既精确指纹不在基线、且该文件+code 病灶也不在基线时，才算"新增"
        baseline_codes = {file_code_signature(fp) for fp in baseline}
        new_errors = [
            ln for fp, ln in current.items() if fp not in baseline and file_code_signature(fp) not in baseline_codes and _in_changed(fp, changed)
        ]
    else:
        new_errors = [ln for fp, ln in current.items() if fp not in baseline]

    if new_errors:
        print(f"[mypy ratchet] BLOCKED: {len(new_errors)} new type error(s) outside baseline:")
        for ln in new_errors[:50]:
            print("  " + ln)
        if len(new_errors) > 50:
            print(f"  ... and {len(new_errors) - 50} more")
        return 1

    print(f"[mypy ratchet] OK: {len(current)} errors all covered by baseline ({len(baseline)} entries).")
    return 0


def _in_changed(fp: str, changed: set[str]) -> bool:
    path = fp.split(":")[0].replace("\\", "/")
    return path in changed


if __name__ == "__main__":
    sys.exit(main())
