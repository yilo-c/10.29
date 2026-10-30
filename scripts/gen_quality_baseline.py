#!/usr/bin/env python3
"""质量基线生成与变更门禁（T1.2 CI 硬化核心机制）。

问题背景：
  CI 的 bandit/mypy/ruff 长期 continue-on-error，存量 1952 处 except /
  50+ ruff 违规无人拦截。diff-filter 只对"改动文件"生效，但新增文件
  （diff-filter=A）和存量债仍会裸奔入库。

本脚本提供两种模式：
  1. 生成基线（默认 / --generate）：
     记录当前思忆集app/ 下每个 .py 文件的 ruff 违规计数，写入
     scripts/quality-baseline.json。基线代表"已存在、暂可接受"的违规分布。
  2. 变更检查（--check-changed）：
     对本次 PR/提交中**改动或新增**的 .py 文件，检查其 ruff 违规是否
     **超过基线该文件的计数**（新增文件基线为 0）。超过则阻塞（返回 1）。
     这样：存量文件允许维持原状，但任何改动/新增文件都不得引入新的 ruff 违规。

用法：
  python scripts/gen_quality_baseline.py                 # 生成/更新基线
  python scripts/gen_quality_baseline.py --check-changed # CI 门禁模式
  python scripts/gen_quality_baseline.py --check-changed --base main
"""

from __future__ import annotations

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

from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _gate_tooling import resolve_tool  # noqa: E402  （scripts/ 内公共模块）

REPO_ROOT = Path(__file__).resolve().parents[1]
APP_DIR = REPO_ROOT / "思忆集app"
BASELINE_PATH = REPO_ROOT / "scripts" / "quality-baseline.json"
# 排除测试与构建产物（这些目录的违规不在门禁范围，与既有 plugins/ 阻塞策略一致）
EXCLUDE_DIRS = {"tests", "build", "__pycache__", "archive"}


def _collect_py_files() -> list[Path]:
    files: list[Path] = []
    for root, dirs, names in os.walk(APP_DIR):
        # 原地裁剪，避免走入排除目录
        dirs[:] = [d for d in dirs if d not in EXCLUDE_DIRS]
        for n in names:
            if n.endswith(".py"):
                files.append(Path(root) / n)
    return files


def generate_baseline() -> int:
    py_files = _collect_py_files()
    counts: dict[str, int] = {}
    for f in py_files:
        rel = f.relative_to(REPO_ROOT).as_posix()
        try:
            proc = subprocess.run(
                [*resolve_tool("ruff"), "check", "--output-format", "concise", str(f)],
                capture_output=True,
                text=True,
                cwd=REPO_ROOT,
                timeout=60,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired):
            counts[rel] = 0
            continue
        # ruff 默认退出码 1 表示有违规，stderr 不含计数；按行解析 concise 输出
        lines = [l for l in (proc.stdout + proc.stderr).splitlines() if l.strip()]
        # concise 格式每行一个 "path:line:col: code message"，过滤掉 summary 行
        n = sum(1 for l in lines if ":" in l and not l.startswith("Found"))
        counts[rel] = n
    BASELINE_PATH.write_text(
        json.dumps({"tool": "ruff", "scope": "思忆集app (excl tests/build/archive)", "counts": counts}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    total = sum(counts.values())
    print(f"[quality-baseline] 已生成基线：{len(counts)} 个文件，ruff 违规合计 {total} 条 -> {BASELINE_PATH.name}")
    return 0


def _changed_files(base: str) -> list[str]:
    """返回相对仓库根的、本次改动/新增的 .py 文件路径（posix）。

    环境区分：
      - CI（GITHUB_BASE_REF / CI=true）：用 `origin/<base>...HEAD` 三点语法，
        得到"PR 合并基点到当前 HEAD"的变更，即本次 PR 的改动文件（正确语义）。
      - 本地（无 CI 环境变量）：优先 `origin/<base>...HEAD` → `<base>...HEAD`
        （base 分支在场时含已提交改动），ref 不可解析时退回 `HEAD`
        （仅未提交工作区改动）。修复前本地仅查未提交改动，提交后再跑
        done_check 会让 ruff 棘轮整体跳过（push 被阻塞时 CI 侧永不触发）。
    """
    if os.environ.get("GITHUB_BASE_REF") or os.environ.get("CI"):
        specs = [f"origin/{base}...HEAD"]
    else:
        specs = [f"origin/{base}...HEAD", f"{base}...HEAD", "HEAD"]

    for spec in specs:
        try:
            proc = subprocess.run(
                ["git", "diff", "--name-only", "--diff-filter=AM", spec, "思忆集app/"],
                capture_output=True,
                text=True,
                cwd=REPO_ROOT,
                timeout=60,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired):
            continue
        if proc.returncode == 0:
            files = []
            for line in proc.stdout.splitlines():
                line = line.strip()
                if line.endswith(".py") and not any(seg in line for seg in EXCLUDE_DIRS):
                    files.append(line)
            return files
    return []


def check_changed(base: str) -> int:
    if not BASELINE_PATH.is_file():
        print("[quality-baseline] 基线不存在，请先生成：python scripts/gen_quality_baseline.py")
        return 1
    baseline = json.loads(BASELINE_PATH.read_text(encoding="utf-8")).get("counts", {})
    changed = _changed_files(base)
    if not changed:
        print("[quality-baseline] 无改动/新增的 .py 文件，门禁通过")
        return 0

    offenders: list[str] = []
    for rel in changed:
        base_count = baseline.get(rel, 0)
        f = REPO_ROOT / rel
        if not f.is_file():
            continue
        try:
            proc = subprocess.run(
                [*resolve_tool("ruff"), "check", "--output-format", "concise", str(f)],
                capture_output=True,
                text=True,
                cwd=REPO_ROOT,
                timeout=60,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired):
            continue
        lines = [l for l in (proc.stdout + proc.stderr).splitlines() if l.strip()]
        n = sum(1 for l in lines if ":" in l and not l.startswith("Found"))
        if n > base_count:
            delta = n - base_count
            offenders.append(f"{rel}: 基线 {base_count} -> 当前 {n} (+{delta})")

    if offenders:
        print("[quality-baseline] 改动/新增文件引入了新的 ruff 违规（阻断合并）:")
        for o in offenders:
            print("  ", o)
        print()
        print("处理指南:")
        print("  1. 修复上述文件的 ruff 违规（推荐）")
        print("  2. 若为有意为之的存量整理，先 `python scripts/gen_quality_baseline.py` 更新基线")
        return 1

    print(f"[quality-baseline] 通过，{len(changed)} 个改动/新增文件无新增 ruff 违规")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="质量基线生成与变更门禁")
    parser.add_argument("--generate", action="store_true", help="生成/更新基线（默认）")
    parser.add_argument("--check-changed", action="store_true", help="CI 门禁：检查改动/新增文件是否新增违规")
    parser.add_argument("--base", default="main", help="--check-changed 时的对比基线分支")
    args = parser.parse_args()

    if args.check_changed:
        return check_changed(args.base)
    return generate_baseline()


if __name__ == "__main__":
    sys.exit(main())
