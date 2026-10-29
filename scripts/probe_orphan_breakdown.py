"""只读：从回收站 $I 元数据还原「批2 生产孤儿 tmp」的**形状分解**。

背景（2026-09-18 独立复核发现，deliverables/backlog-review-2026-09-18.md §11.4 尾注）：
F-3 称"43/45 是点开头的 `.ai_cache_*.tmp`"，F-5 称"31 个 `.ai_cache_*.tmp`
+ 2 个 `siyi-*.json.tmp` + 其余 14 个"——两处互相验算不上。

当时 45 个孤儿位于 `思忆集app/data/`，由 `verify_tmp/clean_orphan_tmp.py`
（走 `os.remove`）与启动清扫共同删除。本仓运行在 WorkBuddy CLI 沙箱内，
`sitecustomize.py` 会把 `os.remove` 改道回收站 → **$I 元数据里应留有原始路径**。

本脚本只读回收站，不改任何东西；用"原路径含 思忆集app\\data" + 文件名匹配
`_ORPHAN_TMP_RE` 三重形状来还原分解。

用法：python verify_tmp/probe_orphan_breakdown.py
"""
from __future__ import annotations

import os
import re
import struct
import time
from collections import Counter
from datetime import datetime

RB = r"C:/$Recycle.Bin/S-1-5-21-2242857364-2689431739-2786187747-500"
# 批2 清扫窗口：2026-09-18 01:00 ~ 04:00
CUT_FROM = datetime(2026, 9, 18, 1, 0).timestamp()
CUT_TO = datetime(2026, 9, 18, 4, 0).timestamp()

# 与 data/_atomic_json.py::_ORPHAN_TMP_RE 保持一致
SHAPES = {
    "atomic_json_tmp（{base}.{pid}.{uuid32hex}.tmp）": re.compile(
        r"^.+\.\d+\.[0-9a-f]{32}\.tmp$"
    ),
    "ai_cache（.ai_cache_*.tmp，点开头）": re.compile(r"^\.ai_cache_[A-Za-z0-9_]+\.tmp$"),
    "siyi_json（siyi-*.json.tmp）": re.compile(r"^siyi-[A-Za-z0-9_]+\.json\.tmp$"),
}


def classify(name: str) -> str | None:
    for label, rx in SHAPES.items():
        if rx.match(name):
            return label
    return None


def main() -> int:
    if not os.path.isdir(RB):
        print(f"[SKIP] 回收站目录不可达：{RB}")
        return 1

    hits: list[tuple[float, int, str, str]] = []
    scanned = 0
    for entry in os.scandir(RB):
        if not entry.name.startswith("$I"):
            continue
        try:
            st = entry.stat()
        except OSError:
            continue
        scanned += 1
        if not (CUT_FROM <= st.st_mtime <= CUT_TO):
            continue
        try:
            with open(entry.path, "rb") as fh:
                data = fh.read()
        except OSError:
            continue
        if len(data) < 24:
            continue
        version = struct.unpack_from("<Q", data, 0)[0]
        size = struct.unpack_from("<Q", data, 8)[0]
        try:
            if version == 2:
                path_len = struct.unpack_from("<I", data, 24)[0]
                raw = data[28 : 28 + path_len * 2]
            else:
                raw = data[24:]
            orig = raw.decode("utf-16-le", errors="replace").rstrip("\x00")
        except Exception as exc:  # noqa: BLE001
            orig = f"<decode-fail {exc}>"
        hits.append((st.st_mtime, size, orig, entry.name))

    print(f"扫描 $I 文件: {scanned} 个；窗口内(01:00-04:00)删除的: {len(hits)} 个")

    # 只保留原路径指向 思忆集app\data 的
    cand = [h for h in hits if "思忆集app" in h[2] and os.sep + "data" in h[2]]
    print(f"其中原路径含 '思忆集app\\data' 的: {len(cand)} 个")

    counter: Counter[str] = Counter()
    other: list[tuple[float, int, str, str]] = []
    dot_prefixed = 0
    total_bytes = 0
    for mt, size, orig, name in cand:
        base = orig.replace("\\", "/").rsplit("/", 1)[-1]
        label = classify(base)
        total_bytes += size
        if base.startswith("."):
            dot_prefixed += 1
        if label:
            counter[label] += 1
        else:
            other.append((mt, size, orig, name))

    print("-" * 70)
    print("形状分解（按 _ORPHAN_TMP_RE 三类）：")
    for label, n in counter.most_common():
        print(f"  {n:>4} 个  {label}")
    if other:
        print(f"  {len(other):>4} 个  **不匹配任何已知形状**（明细见下）")
    print(f"  合计 {sum(counter.values()) + len(other)} 个，"
          f"{total_bytes / 1024 / 1024:.2f} MB；其中点开头 {dot_prefixed} 个")
    print("-" * 70)
    if other:
        print("“其它”明细（最多 20 条）：")
        for mt, size, orig, name in sorted(other)[:20]:
            print(f"  {time.strftime('%H:%M:%S', time.localtime(mt))}"
                  f"  {size:>10} B  ->  {orig}")
    print("-" * 70)
    print("样本（窗口内最早 10 条，含全部形状）：")
    for mt, size, orig, name in sorted(cand)[:10]:
        print(f"  {time.strftime('%H:%M:%S', time.localtime(mt))}"
              f"  {size:>10} B  ->  {orig}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
