#!/usr/bin/env python3
"""
bandit high 棘轮门禁（T1.2 补全）。

与 ruff / mypy 棘轮一致——基线 scripts/bandit-baseline.json 记录当前全量
high 问题指纹；后续修改若引入"基线外"的新 high 问题，则阻塞 CI。
已知误报（B413 PyCryptodome / B411 WordPress XML-RPC）保留在基线中，
专项清理时从基线移除对应行即可重新激活拦截。

用法：
    python scripts/bandit_ratchet.py [--base main]
        --check-changed  仅检查相对 base 分支新增/修改的 .py 文件中出现的新 high
        （无此 flag 则全仓比对，用于本地验证基线是否过期）

指纹：取 bandit json 结果的 (filename, line_number, test_id) 作为稳定指纹。
"""

import argparse
import json
import os
import subprocess
import sys


# [stdio-utf8-guard] 中文 Windows 控制台(cp1252)下 print 中文会 UnicodeEncodeError 并退出 1，
# 导致 CI quality-gates 中断、后续步骤全部 skipped。详见 scripts/_stdio.py。
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
except Exception:
    pass

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _gate_tooling import resolve_tool  # noqa: E402  （scripts/ 内公共模块）

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP_DIR = os.path.join(REPO_ROOT, "思忆集app")
BASELINE = os.path.join(REPO_ROOT, "scripts", "bandit-baseline.json")


def fingerprint(r: dict) -> str:
    fn = r["filename"].replace("\\", "/")
    return f"{fn}:{r['line_number']}:{r['test_id']}"


def load_baseline(path: str) -> set[str]:
    if not os.path.exists(path):
        return set()
    d = json.load(open(path, encoding="utf-8"))
    return {fingerprint(r) for r in d.get("results", []) if r.get("issue_severity") == "HIGH"}


def run_bandit() -> list[dict]:
    proc = subprocess.run(
        [*resolve_tool("bandit"), "-r", APP_DIR, "-ii", "-x", f"{APP_DIR}/tests,{APP_DIR}/build", "-f", "json", "-q"],
        capture_output=True,
        text=True,
    )
    try:
        d = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return []
    return [r for r in d.get("results", []) if r.get("issue_severity") == "HIGH"]


def changed_python_files(base: str) -> set[str]:
    try:
        res = subprocess.run(
            ["git", "diff", "--name-only", "--diff-filter=AM", f"origin/{base}...HEAD"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
        )
        files = set()
        for ln in res.stdout.splitlines():
            ln = ln.strip().replace("/", os.sep)
            if ln.endswith(".py") and os.path.isfile(os.path.join(REPO_ROOT, ln)):
                rel = os.path.relpath(os.path.join(REPO_ROOT, ln), APP_DIR)
                files.add(rel.replace(os.sep, "/"))
        return files
    except Exception:
        return set()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="main")
    ap.add_argument("--baseline", default=BASELINE)
    ap.add_argument("--check-changed", action="store_true")
    args = ap.parse_args()

    baseline = load_baseline(args.baseline)
    results = run_bandit()
    current = {fingerprint(r): r for r in results}

    if args.check_changed:
        changed = changed_python_files(args.base)
        if not changed:
            print(f"[bandit ratchet] no changed python files vs origin/{args.base}; skipping.")
            return 0
        new_highs = [r for fp, r in current.items() if fp not in baseline and _in_changed(fp, changed)]
    else:
        # 修：current 是 {指纹: 结果} 字典，原写法 `for fp in current` 迭代的是键
        # 却引用未定义的 r → 全仓模式必抛 NameError（F821，2026-09-16 ruff 扫出）。
        # done_check 恒用 --check-changed，故该分支长期未被走到、bug 潜伏至今。
        new_highs = [r for fp, r in current.items() if fp not in baseline]

    if new_highs:
        print(f"[bandit ratchet] BLOCKED: {len(new_highs)} new HIGH issue(s) outside baseline:")
        for r in new_highs[:50]:
            fn = r["filename"].replace("\\", "/").split("/")[-1]
            print(f"  {r['test_id']} {fn}:{r['line_number']} {r['issue_text'][:50]}")
        if len(new_highs) > 50:
            print(f"  ... and {len(new_highs) - 50} more")
        return 1

    print(f"[bandit ratchet] OK: {len(current)} HIGH issues all covered by baseline ({len(baseline)} entries).")
    return 0


def _in_changed(fp: str, changed: set[str]) -> bool:
    # fp 形如 "D:/.../app/backend/x.py:12:B307"，盘符也含冒号 → 必须从右侧切
    path = fp.rsplit(":", 2)[0]
    norm = os.path.abspath(path.replace("/", os.sep))
    try:
        if os.path.commonpath([norm, APP_DIR]) == APP_DIR:
            norm = os.path.relpath(norm, APP_DIR)
    except ValueError:
        pass
    return norm.replace(os.sep, "/") in changed


if __name__ == "__main__":
    sys.exit(main())
