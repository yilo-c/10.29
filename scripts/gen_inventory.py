"""从代码生成 RPC 方法与环境变量权威清单，输出 docs/inventory.md（自动生成，勿手改）。

debt-v2 TASK-V1-03：
- RPC 方法名复用 test_jsonrpc_contract.py 的 RE_BACKEND_REGISTER 正则
  （registry.register / registry.register_with_schema），并叠加动态注册前缀清单；
- SIYI_* 环境变量全仓扫描（跳过 _trash / .venv 等）。

用法：python scripts/gen_inventory.py [--check]
  --check：生成后若 docs/inventory.md 与生成结果不一致则退出码 1（CI 防漂移）。
"""
from __future__ import annotations

import argparse
import re
import sys

# [stdio-utf8-guard] 中文 Windows 控制台(cp1252)下 print 中文会 UnicodeEncodeError 并退出 1，
# 导致 CI quality-gates 中断、后续步骤全部 skipped。详见 scripts/_stdio.py。
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
except Exception:
    pass

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# 与 tests/test_jsonrpc_contract.py:21-23 一致的后端注册正则
RE_BACKEND_REGISTER = re.compile(
    r'registry\.register(?:_with_schema)?\s*\(\s*["\']([^"\']+)["\']'
)
# 动态注册模块前缀（与 test_jsonrpc_contract.py:71-79 一致）
DYNAMICALLY_REGISTERED_PREFIXES = (
    "cron.", "job_hunter.", "rss.", "process.", "timeline.",
    "task_queue.", "info_sources.",
)
RE_ENV = re.compile(r"SIYI_[A-Z0-9_]+")

_SKIP_PARTS = ("_trash", ".venv", "node_modules", ".git", "web_dist", "__pycache__")


def _collect_methods() -> set[str]:
    methods: set[str] = set()
    handlers_dir = ROOT / "思忆集app" / "backend" / "handlers"
    if handlers_dir.is_dir():
        for py in handlers_dir.rglob("*.py"):
            src = py.read_text(encoding="utf-8", errors="ignore")
            methods.update(RE_BACKEND_REGISTER.findall(src))
    # 动态注册前缀：登记一组"前缀匹配"占位，与契约测试口径对齐
    methods.update(f"{p}*" for p in DYNAMICALLY_REGISTERED_PREFIXES)
    return methods


def _collect_envs() -> set[str]:
    import os

    envs: set[str] = set()
    skip_dirs = {
        "node_modules", ".git", ".venv", ".venv3.11", "_trash", "__pycache__",
        "dist", "build", "web_dist", ".claude", "worktrees", ".pytest_cache",
        ".mypy_cache", ".ruff_cache", "backups", "archive",
    }
    # 只扫描源码相关顶层目录，避免 node_modules 坏符号链接 / 大目录
    scan_roots = [
        ROOT / "思忆集app",
        ROOT / "automation_hub",
        ROOT / "cloudflare",
        ROOT / "scripts",
        ROOT / "docs",
    ]
    for root in scan_roots:
        if not root.is_dir():
            continue
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d not in skip_dirs]
            for fname in filenames:
                if not fname.endswith(".py"):
                    continue
                fp = os.path.join(dirpath, fname)
                try:
                    with open(fp, encoding="utf-8", errors="ignore") as fh:
                        src = fh.read()
                except OSError:
                    continue
                envs.update(RE_ENV.findall(src))
    return envs


def _render(methods: set[str], envs: set[str]) -> str:
    lines = [
        "# 自动生成清单（勿手改）",
        "# 由 scripts/gen_inventory.py 生成；改动后端后必须重跑（debt-v2 TASK-V1-03）",
        "",
        f"- RPC 方法名（静态注册 + 动态前缀，去重）: {len(methods)}",
        f"- SIYI_* 环境变量: {len(envs)}",
        "",
        "## RPC 方法",
    ]
    lines += [f"- {m}" for m in sorted(methods)]
    lines += ["", "## SIYI_* 变量"]
    lines += [f"- {e}" for e in sorted(envs)]
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true", help="CI 防漂移：不一致时退出码 1")
    args = parser.parse_args()

    methods = _collect_methods()
    envs = _collect_envs()
    content = _render(methods, envs)

    out = ROOT / "docs" / "inventory.md"
    if args.check:
        if out.exists() and out.read_text(encoding="utf-8") == content:
            print(f"OK: inventory in sync ({len(methods)} methods, {len(envs)} envs)")
            return 0
        print(f"DRIFT: docs/inventory.md out of sync ({len(methods)} methods, {len(envs)} envs)")
        return 1
    out.write_text(content, encoding="utf-8")
    print(f"OK: {len(methods)} methods, {len(envs)} env vars -> docs/inventory.md")
    return 0


if __name__ == "__main__":
    sys.exit(main())
