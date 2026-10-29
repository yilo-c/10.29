#!/usr/bin/env python3
"""守卫：禁止仓库内存在无源码的裸 legacy .pyc。

背景：2026-07-18 清理脚本误删导致 82 个模块源码丢失、仅靠裸 pyc 运行，
重建完成后引入本守卫防止再次发生。

规则：思忆集app/ 下（排除 __pycache__/）若存在 *.pyc 且无同名 *.py，即报错。

用法：
  python scripts/check_no_sourceless_pyc.py
  返回值：0 表示干净，1 表示存在无源码 pyc
"""

from __future__ import annotations

import os
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP_DIR = os.path.join(REPO_ROOT, "思忆集app")


def main() -> int:
    offenders: list[str] = []
    for root, _dirs, files in os.walk(APP_DIR):
        in_pycache = "__pycache__" in root
        for f in files:
            if not f.endswith(".pyc"):
                continue
            if in_pycache:
                # 现代布局缺口（2026-09-13 守卫通电普查发现）：__pycache__/X.pyc
                # 的源码 X.py 被删 = 当年"仅靠裸 pyc 运行"事故的现代形态。
                # 正常缓存必然有同名源码，此处无同名 .py 即裸 pyc，零误报。
                stem = f[:-4]
                if ".cpython-" in stem:  # X.cpython-311.pyc → X.py
                    stem = stem.split(".cpython-", 1)[0]
                if not os.path.isfile(os.path.join(os.path.dirname(root), stem + ".py")):
                    offenders.append(os.path.relpath(os.path.join(root, f), REPO_ROOT))
                continue
            if not os.path.isfile(os.path.join(root, f[:-4] + ".py")):
                offenders.append(os.path.relpath(os.path.join(root, f), REPO_ROOT))

    if offenders:
        print("[无源码 pyc 守卫] 发现无源码的裸 .pyc（源码丢失风险）:")
        for o in sorted(offenders):
            print(" ", o)
        print()
        print("处理指南:")
        print("  1. 若为误删源码残留：按 docs/PYC_REBUILD_RUNBOOK.md 重建源码")
        print("  2. 若为无用残留：确认无引用后删除该 .pyc")
        return 1

    print("[无源码 pyc 守卫] 通过，无裸 .pyc")
    return 0


if __name__ == "__main__":
    sys.exit(main())
