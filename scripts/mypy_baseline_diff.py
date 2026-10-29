#!/usr/bin/env python3
"""分析 mypy 基线 vs 当前错误（M5 基线卫生）：找出陈旧条目与未覆盖错误。"""
import io
import re
import sys
from collections import Counter

LINE_RE = re.compile(
    r"^(?P<path>.+?):(?P<line>\d+):(?P<col>\d+):\s*error:.*?(?:\[(?P<code>[a-z-]+)\])?\s*$"
)


def fps(lines):
    s = set()
    for ln in lines:
        m = LINE_RE.match(ln.strip())
        if m:
            code = m.group("code") or "unknown"
            s.add(f"{m.group('path')}:{m.group('line')}:{m.group('col')}:[{code}]")
    return s


def main():
    base = fps(io.open("scripts/mypy-baseline.txt", encoding="utf-8").read().splitlines())
    cur = fps(io.open("verify_tmp/mypy_current.txt", encoding="utf-8").read().splitlines())
    stale = base - cur
    un = cur - base
    print(f"baseline={len(base)} current={len(cur)} stale(应删)={len(stale)} uncovered(未覆盖)={len(un)}")
    print("--- uncovered top files ---")
    c = Counter(fp.split(":")[0] for fp in un)
    for f, n in c.most_common(20):
        print(f"{n:3}  {f}")


if __name__ == "__main__":
    sys.exit(main())
