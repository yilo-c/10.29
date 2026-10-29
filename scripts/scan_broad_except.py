#!/usr/bin/env python3
"""广义异常（broad except）扫描与变更门禁（T2.1 异常治理核心机制）。

问题背景：
  思忆集app/ 下存量约 1980 处 ``except Exception``。绝大多数已经用
  ``logger.exception / warning / debug(exc_info=True)`` 记录了上下文，这种站点
  属于"可观测的兜底"，暂可接受。真正危险的是**静默吞掉**的广义异常
  （裸 ``except Exception:`` 后既无日志也无 re-raise），它会让 P0-2 类故障
  无法从日志定位。

  ruff 的 BLE001（broad-exception-caught）当前**未**纳入 select，若直接全局
  启用会一次性报出 1980 处并阻断一切，不可行。因此本脚本采用与
  gen_quality_baseline 一致的"基线 + 变更门禁"策略，且**只针对静默站点**。

两种模式：
  1. 生成基线（默认 / --generate）：
     扫描全仓，记录每文件"静默广义异常"计数 + 全量站点计数，写入
     scripts/broad-except-baseline.json。同时打印分类统计，供人工排优先级。
  2. 变更检查（--check-changed）：
     对本次 PR/提交中**改动或新增**的 .py 文件（默认仅白名单路径：
     backend/handlers、services/cron_actions.py、app_runtime、plugins），
     检查是否**新增了静默广义异常站点**。新增则阻塞（返回 1）。
     存量文件允许维持原状，但任何改动/新增文件都不得引入新的静默兜底。

用法：
  python scripts/scan_broad_except.py                  # 生成/更新基线 + 打印统计
  python scripts/scan_broad_except.py --check-changed  # CI 门禁模式
  python scripts/scan_broad_except.py --check-changed --base main
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
APP_DIR = REPO_ROOT / "思忆集app"
BASELINE_PATH = REPO_ROOT / "scripts" / "broad-except-baseline.json"

# 变更门禁仅覆盖这些"白名单路径"（计划 T2.1 优先级：handler → cron → AppContext → 插件）
WHITELIST_PREFIXES = (
    "backend/handlers/",
    "services/cron_actions.py",
    "app_runtime/",
    "plugins/",
)
# 排除测试与构建产物
EXCLUDE_DIRS = {"tests", "build", "__pycache__", "archive"}

_BROAD_RE = re.compile(r"except\s+Exception\b")
# 静默判定：站点后 3 行内出现 logger.{exception,error,warning,info,debug}
# 或 exc_info= 或 raise / reraise / 显式 return None 之外的"可见副作用"。
_LOGGING_RE = re.compile(
    r"logger\.(exception|error|warning|critical|info|debug)|exc_info\s*=|"
    r"\b(traceback|logging\.)\b|"
    # 2026-09-16（S2 棘轮治理）：degrade() 是 handler 层统一降级助手
    # （backend/handlers/_wrap.py:86，内部 log_degradation 做 ERR 级落日志、
    # 含 traceback），语义等同 logger.exception，属可观测兜底而非静默吞异常。
    # 原正则只认 logger 直呼名，把 _notes.py 等 7 处 degrade 站点误判为静默。
    r"\bdegrade\s*\("
)
_RAISE_RE = re.compile(r"\braise\b")


def _classify(content: str) -> tuple[int, int, list[dict]]:
    """返回 (总站点数, 静默站点数, 静默站点明细)。"""
    lines = content.splitlines()
    total = 0
    silent = 0
    silent_sites: list[dict] = []
    for i, line in enumerate(lines):
        if not _BROAD_RE.search(line):
            continue
        total += 1
        window = "\n".join(lines[i : i + 4])  # 本行 + 后续 3 行
        has_log = bool(_LOGGING_RE.search(window))
        has_raise = bool(_RAISE_RE.search(window))
        # 有日志或有 re-raise → 视为可观测/可传播，不算静默
        if has_log or has_raise:
            continue
        silent += 1
        silent_sites.append({"line": i + 1, "text": line.strip()})
    return total, silent, silent_sites


def _collect_py_files() -> list[Path]:
    files: list[Path] = []
    for root, dirs, names in os.walk(APP_DIR):
        dirs[:] = [d for d in dirs if d not in EXCLUDE_DIRS]
        for n in names:
            if n.endswith(".py"):
                files.append(Path(root) / n)
    return files


def generate_baseline() -> int:
    py_files = _collect_py_files()
    baseline: dict[str, dict] = {}
    total_all = 0
    silent_all = 0
    for f in py_files:
        rel = f.relative_to(REPO_ROOT).as_posix()
        try:
            content = f.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        total, silent, _ = _classify(content)
        baseline[rel] = {"total": total, "silent": silent}
        total_all += total
        silent_all += silent
    BASELINE_PATH.write_text(json.dumps(baseline, ensure_ascii=False, indent=2), encoding="utf-8")
    # 统计静默站点 Top 目录
    by_dir: dict[str, int] = {}
    for rel, v in baseline.items():
        if v["silent"]:
            top = "/".join(rel.split("/")[:2])
            by_dir[top] = by_dir.get(top, 0) + v["silent"]
    print(f"基线已写入 {BASELINE_PATH}")
    print(f"广义异常站点总数: {total_all}")
    print(f"静默站点总数:     {silent_all}")
    print("静默站点按顶层目录分布 (Top 12):")
    for d, c in sorted(by_dir.items(), key=lambda x: -x[1])[:12]:
        print(f"  {d}: {c}")
    return 0


def _changed_files(base: str | None) -> list[Path]:
    """返回本次改动/新增的 .py 文件（相对 REPO_ROOT 的路径）。"""
    if os.environ.get("CI") and os.environ.get("GITHUB_BASE_REF"):
        # CI（GitHub Actions）：checkout 后仅有 origin/<base> 远程跟踪分支，
        # 用 {base} 直接 diff 会解析失败 → 误放行，必须与其余门禁一致用 origin/ 前缀
        ref = f"origin/{base or 'main'}...HEAD"
    elif base:
        ref = f"{base}...HEAD"
    else:
        ref = "HEAD"  # 本地：仅未提交改动
    try:
        out = subprocess.run(
            ["git", "diff", "--diff-filter=AM", "--name-only", "-z", ref],
            capture_output=True,
            text=True,
            cwd=str(REPO_ROOT),
        ).stdout
    except FileNotFoundError:
        out = ""
    files = [p for p in out.split("\0") if p and p.endswith(".py")]
    return [REPO_ROOT / p for p in files]


def check_changed(base: str | None) -> int:
    if not BASELINE_PATH.exists():
        print("基线不存在，先运行 --generate", file=sys.stderr)
        return 2
    baseline = json.loads(BASELINE_PATH.read_text(encoding="utf-8"))
    changed = _changed_files(base)
    violations: list[str] = []
    for f in changed:
        try:
            rel_app = f.relative_to(APP_DIR).as_posix()
        except ValueError:
            continue  # 不在 APP_DIR 下（如 scripts/、deliverables/），白名单外
        if not any(rel_app.startswith(p) or rel_app == p for p in WHITELIST_PREFIXES):
            continue
        if not f.exists():
            continue
        rel = f.relative_to(REPO_ROOT).as_posix()  # 基线 key 语义：REPO_ROOT 相对
        content = f.read_text(encoding="utf-8", errors="replace")
        _, silent_now, sites = _classify(content)
        silent_base = baseline.get(rel, {}).get("silent", 0)
        new_silent = silent_now - silent_base
        if new_silent > 0:
            for s in sites:
                violations.append(f"{rel}:{s['line']} 新增静默广义异常: {s['text']}")
    if violations:
        print("❌ 白名单路径新增了静默广义异常站点（阻断）:")
        for v in violations:
            print(f"  {v}")
        print("\n修复建议：在 except Exception 块内至少加一行 logger.exception(...) 或显式 raise，使其可观测/可传播。")
        return 1
    print("✅ 白名单路径未新增静默广义异常站点。")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check-changed", action="store_true", help="CI 变更门禁模式")
    ap.add_argument("--base", default=None, help="diff 基准分支 (默认 origin/main / HEAD)")
    args = ap.parse_args()
    if args.check_changed:
        return check_changed(args.base)
    return generate_baseline()


if __name__ == "__main__":
    raise SystemExit(main())
