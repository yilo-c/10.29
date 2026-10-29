#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""F2 干净会话复核归档门禁（审计报告章节 4 原则 P-3 / V2.1 §8 #5）。

背景：原方案 F2（无上下文新会话独立复核输出三态表）写进文档但零执行——
因为它是「流程要求」而非机器断言。本脚本把它变成硬门禁：

对 risk-register 中出现的每个批次标记 R<N>（如 R0/R1/...），要求
`deliverables/reviews/<批次>-f2.md` 存在，且文件内含三态表关键词
（属实 / 不属实 / 行号漂移）之一。任一批次缺报告 → 退出码 1（CI/done_check 红）。

用法：
    python scripts/check_f2_archives.py
退出码：
    0 = 所有已登记批次的 F2 报告齐全
    1 = 存在缺报告 / 报告缺三态表的批次
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

REPO_ROOT = Path(__file__).resolve().parents[1]
REGISTER = REPO_ROOT / "deliverables" / "risk-register.md"
REVIEWS_DIR = REPO_ROOT / "deliverables" / "reviews"

# 三态表关键词：任一出现即视为「含三态表」
_TRI_STATE_MARKERS = ("属实", "不属实", "行号漂移")
# 批次标记：R0 / R1 / ... （形如 R<数字>）
_BATCH_RE = re.compile(r"\bR(\d{1,2})\b")


def collect_batches() -> set[str]:
    """从 risk-register 全文提取批次标记（如 R0、R1）。"""
    if not REGISTER.exists():
        return set()
    text = REGISTER.read_text(encoding="utf-8", errors="ignore")
    return {f"R{n}" for n in _BATCH_RE.findall(text)}


def check() -> tuple[bool, str]:
    batches = collect_batches()
    if not batches:
        return True, "登记册无批次标记（R<N>），无需 F2 报告"
    missing: list[str] = []
    for b in sorted(batches, key=lambda x: int(x[1:])):
        f = REVIEWS_DIR / f"{b}-f2.md"
        if not f.exists():
            missing.append(f"{b}-f2.md(文件不存在)")
            continue
        text = f.read_text(encoding="utf-8", errors="ignore")
        if not any(k in text for k in _TRI_STATE_MARKERS):
            missing.append(f"{b}-f2.md(缺三态表关键词)")
    if missing:
        return False, f"缺 F2 复核报告: {', '.join(missing)}"
    return True, f"{len(batches)} 个批次 F2 报告齐全（三态表在案）"


if __name__ == "__main__":
    ok, detail = check()
    print(f"[check-f2-archives] {'PASS' if ok else 'FAIL'} -- {detail}")
    sys.exit(0 if ok else 1)
