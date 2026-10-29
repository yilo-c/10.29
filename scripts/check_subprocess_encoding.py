#!/usr/bin/env python3
"""子进程编码纪律棘轮：文本模式 subprocess.run 不得裸 text=True（P1-A 治本门禁）。

背景（2026-09-18 实测取证 deliverables/shell-capability-test-2026-09-18.md §3 P1-A）：
`subprocess.run(..., text=True)` 不带 `encoding=` 时，解码编码取自
`locale.getpreferredencoding()`。门禁/启动环境设 `PYTHONUTF8=1` 会让它变 utf-8，
而 Windows 系统 CLI（powershell/tasklist/ipconfig/wmic）输出是 cp936 →
reader 线程解码抛错被 subprocess 内部吞掉、`CompletedProcess.stdout` **静默变 None**
（主进程无异常）→ 下游 `.strip()` 炸 `'NoneType' object has no attribute 'strip'`。
实测 27 处中招，`assistant/execution/environment.py` 一家 16 处。

合规写法：
  * 首选 `core.subprocess_text.run_text(...)` —— 抓字节 + utf-8/gbk/cp936 回退解码，
    `.stdout/.stderr` 恒为 str，与 PYTHONUTF8 无关。
  * 次选 `subprocess.run(..., text=True, encoding="utf-8", errors="replace")`
    —— 显式 encoding（本门禁放行）。

与 check_clock_discipline / mypy_ratchet 同款棘轮：AST 精确判定 Call 节点，
**存量只允许下降，新增即失败**；消减后 `--update` 收紧基线。

用法：
  python scripts/check_subprocess_encoding.py            # 违规>基线 → 退出 1
  python scripts/check_subprocess_encoding.py --report   # 打印当前计数
  python scripts/check_subprocess_encoding.py --update   # 收紧基线
"""

from __future__ import annotations

import ast
import json
import os
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP_SUBROOT = "思忆集app"

# 扫描范围：产品代码目录。tests/ 与一次性 verify_tmp 不入计（探针可用裸 text=True）。
_SCAN_DIR = os.path.join(REPO_ROOT, APP_SUBROOT)
_SKIP_DIR_NAMES = {".venv", "__pycache__", "node_modules", "verify_tmp", "backups", "build"}
_SKIP_PATH_HINTS = (
    os.path.join(APP_SUBROOT, "tests") + os.sep,
    os.path.join(APP_SUBROOT, "core", "subprocess_text.py"),  # 基元本身用 bytes，不含裸 text=True
)

BASELINE_PATH = os.path.join(REPO_ROOT, "scripts", "subprocess_encoding_baseline.json")


def _iter_py():
    for root, dirs, files in os.walk(_SCAN_DIR):
        dirs[:] = [d for d in dirs if d not in _SKIP_DIR_NAMES]
        for fn in files:
            if not fn.endswith(".py"):
                continue
            full = os.path.join(root, fn)
            rel = os.path.relpath(full, REPO_ROOT)
            if any(h in rel for h in _SKIP_PATH_HINTS):
                continue
            yield rel, full


def _count_violations(rel: str, path: str) -> list[int]:
    """返回该文件里裸 text=True（无 encoding=）的 subprocess.run/check_* 行号列表。"""
    try:
        with open(path, encoding="utf-8") as f:
            tree = ast.parse(f.read(), filename=rel)
    except (OSError, SyntaxError, UnicodeDecodeError):
        return []
    lines: list[int] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        if not isinstance(fn, ast.Attribute) or fn.attr not in ("run", "check_output", "check_call"):
            continue
        owner = fn.value
        owner_name = getattr(owner, "id", None) or (getattr(owner, "attr", None) if isinstance(owner, ast.Attribute) else None)
        if owner_name != "subprocess":
            continue
        has_text = any(
            kw.arg in ("text", "universal_newlines") and isinstance(kw.value, ast.Constant) and kw.value.value is True for kw in node.keywords
        )
        has_enc = any(kw.arg == "encoding" for kw in node.keywords)
        if has_text and not has_enc:
            lines.append(node.lineno)
    return lines


def _collect() -> dict[str, int]:
    out: dict[str, int] = {}
    for rel, full in _iter_py():
        n = len(_count_violations(rel, full))
        if n:
            out[rel] = n
    return out


def _load_baseline() -> dict[str, int]:
    if not os.path.isfile(BASELINE_PATH):
        return {}
    with open(BASELINE_PATH, encoding="utf-8") as f:
        return json.load(f)


def _save_baseline(data: dict[str, int]) -> None:
    with open(BASELINE_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2, sort_keys=True)
        f.write("\n")


def main() -> int:
    argv = sys.argv[1:]
    current = _collect()

    if "--report" in argv:
        total = sum(current.values())
        print(f"[子进程编码纪律] 当前裸 text=True 违规合计 {total} 处，涉及 {len(current)} 文件：")
        for rel, n in sorted(current.items(), key=lambda kv: -kv[1]):
            print(f"  {n:>3}  {rel}")
        return 0

    if "--update" in argv:
        _save_baseline(current)
        print(f"[子进程编码纪律] 基线已收紧：合计 {sum(current.values())} 处 / {len(current)} 文件")
        return 0

    baseline = _load_baseline()
    if not baseline:
        print("[子进程编码纪律] 基线缺失——先跑 `python scripts/check_subprocess_encoding.py --update`")
        print(f"  期望路径：{BASELINE_PATH}")
        return 1

    regressions: list[str] = []
    tightened: list[str] = []
    for rel in set(current) | set(baseline):
        now = current.get(rel, 0)
        was = baseline.get(rel, 0)
        if now > was:
            regressions.append(f"  [新增裸 text=True] {rel}: {was} → {now}")
        elif now < was:
            tightened.append(f"  [可收紧] {rel}: {was} → {now}")

    if regressions:
        print("[子进程编码纪律] 失败：出现新的裸 text=True（无 encoding=）子进程调用：")
        for r in regressions:
            print(r)
        print()
        print("  合规写法：core.subprocess_text.run_text(...)  —— 抓字节 + utf-8/gbk 回退，")
        print("            或 subprocess.run(..., text=True, encoding='utf-8', errors='replace')")
        print("  理由：P1-A —— PYTHONUTF8=1 下 text=True 无 encoding 会静默把 stdout 置 None，")
        print("        下游 .strip() 炸 'NoneType'。详见 deliverables/shell-capability-test-2026-09-18.md。")
        if tightened:
            print("\n  另有存量已消减（跑 --update 收紧基线）：")
            for t in tightened:
                print(t)
        return 1

    total = sum(current.values())
    print(f"[子进程编码纪律] 通过：裸 text=True 违规均在基线内（合计 {total} 处存量，{len(current)} 文件）")
    if tightened:
        print("  以下文件存量已消减，建议 `--update` 收紧基线：")
        for t in tightened:
            print(t)
    return 0


if __name__ == "__main__":
    sys.exit(main())
