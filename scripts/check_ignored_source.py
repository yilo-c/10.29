#!/usr/bin/env python3
"""仓库完整性守卫（2026-08-24）：生产 .py 源码不得被 gitignore 静默吞掉。

背景：`_*.py` / `data/` / `build/` 三条未锚定 gitignore 规则曾吞掉
app_runtime/_context_*.py（四个 DI mixin）、data/ 存储层（local_storage.py /
sqlite_store.py）、ai/client/_*.py、repositories/_*.py、memory/search/_*.py、
build/ 工具包等 78 个生产模块 —— HEAD 快照残缺：本地工作树能跑、fresh clone
必坏（app_context 无法导入），而 done_check 的 `git status` 检查看不到被忽略
文件，全程静默。

本守卫钉死不变量：除「思忆集app 顶层 `_*.py` 诊断脚本」外，任何被 gitignore
忽略的 .py 一律 FAIL（说明有源码被忽略规则吞掉，须取消忽略并恢复跟踪）。
本地 done_check 跑在含未跟踪文件的工作树上，正好能抓到 CI（fresh clone 无
被忽略文件）抓不到的这一类问题。

用法：
  python scripts/check_ignored_source.py
  返回 0 = 通过；1 = 存在被忽略的生产 .py
"""

from __future__ import annotations

import os
import subprocess
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP_DIR_NAME = "思忆集app"

# 依赖/虚拟环境目录：其内 .py 全是第三方 site-packages，不是"被 gitignore 吞掉的
# 生产源码"（守卫意图见 docstring）。此前本守卫误把 `思忆集app/.venv/`（uv 建的
# 本地虚拟环境，1.2G、从未被 git 跟踪）当成 16332 个"被忽略生产 .py" → 全量误报。
# 按路径任一目录段命中即跳过（用 os.sep 边界，避免误伤 xxx_venv 之类源码目录）。
_VENDORED_SEGMENTS = frozenset(
    {
        ".venv",
        "venv",
        ".env",
        "env",
        "site-packages",
        "node_modules",
        "__pypackages__",
        ".conda",
        ".virtualenvs",
    }
)


def _is_vendored(path: str) -> bool:
    parts = path.split(os.sep)
    return any(seg in _VENDORED_SEGMENTS for seg in parts)


def main() -> int:
    proc = subprocess.run(
        ["git", "ls-files", "--others", "--ignored", "--exclude-standard", "--", APP_DIR_NAME],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    bad: list[str] = []
    for ln in proc.stdout.splitlines():
        ln = ln.strip().replace("/", os.sep)
        if not ln.endswith(".py"):
            continue
        # 跳过依赖/虚拟环境目录内的第三方 .py（非生产源码）
        if _is_vendored(ln):
            continue
        # 合法忽略白名单：思忆集app 顶层 _*.py（一次性诊断/抓数脚本）
        prefix = APP_DIR_NAME + os.sep
        if ln.startswith(prefix):
            rest = ln[len(prefix) :]
            if os.sep not in rest and rest.startswith("_"):
                continue
        bad.append(ln)

    if not bad:
        print("[ignored-source] OK: 无生产 .py 被 gitignore 吞掉")
        return 0
    print(f"[ignored-source] FAIL: gitignore 吞掉 {len(bad)} 个生产 .py:")
    for p in bad[:10]:
        print(f"  {p}")
    print("  处理：取消忽略并恢复跟踪（见 .gitignore 2026-08-24 修正注释）；")
    print("  确为一次性脚本的，移入顶层 _*.py 白名单或显式忽略，勿留静默源文件。")
    return 1


if __name__ == "__main__":
    sys.exit(main())
