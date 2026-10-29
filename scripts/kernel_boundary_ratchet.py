#!/usr/bin/env python3
"""内核契约 I1 边界棘轮（2026-09-02）。

基线锁定 + 只阻塞新增（模式与 bandit_ratchet.py / mypy 棘轮一致）。

契约 I1（docs/kernel-contract.md v1.3）：四区（backend/services/repositories/
app_runtime）禁止 import assistant / agent_gateway.adapters / smolagents。

2026-09-02 基线（实测）：
  - assistant.*: 28 行 / 5 文件（10 _context_builder + 7 service_registry_manager
    + 6 pydantic_agent_path + 4 ai_manager + 1 _tooling_provider）
  - smolagents: 3 行 / 1 文件（backend/siyi_tools.py，工作树已删该文件但未提交，
    按提交态计入基线，防止未提交删除回滚导致基线失真）
  - agent_gateway.adapters: 0

用法：
    python scripts/kernel_boundary_ratchet.py             # 检查（CI 门禁）
    python scripts/kernel_boundary_ratchet.py --update    # 消减存量后收紧基线

退出码：0=未新增违规；1=发现基线外新增违规（阻塞）。

设计要点：
  - 直接扫工作树（与 scripts/check-layer-boundaries.py 的 RULES 完全同源），
    保证「门禁报什么、棘轮锁什么」口径一致。
  - 指纹 = (规则类别, 文件, 行号) 前缀匹配宽松化：文件级消减（整文件迁移/删除）
    自动视为改善而非新增；行号漂移不算新增（同文件同类别计数不增即可）。
  - 基线按「每文件每类别的违规行数」计数，只比较计数不比较具体行号，
    避免无意义行号漂移阻塞。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP_DIR = os.path.join(REPO_ROOT, "思忆集app")
BASELINE_PATH = os.path.join(REPO_ROOT, "scripts", "kernel-boundary-baseline.json")

FOUR_ZONE_DIRS = ("backend", "services", "repositories", "app_runtime")

# 与 check-layer-boundaries.py 的 I1 规则同源
_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("assistant", re.compile(r"^\s*(?:from|import)\s+assistant\b")),
    ("agent_gateway_adapters", re.compile(r"^\s*(?:from|import)\s+agent_gateway\.adapters\b")),
    ("smolagents", re.compile(r"^\s*(?:from|import)\s+smolagents\b")),
]

# 已知豁免（与门禁脚本口径一致）
SKIP_DIR_PARTS = ("_deprecated", "/tests/", ".venv", "__pycache__")


def scan() -> dict[str, dict[str, int]]:
    """扫描四区，返回 {文件: {类别: 违规行数}}。"""
    result: dict[str, dict[str, int]] = {}
    for zone in FOUR_ZONE_DIRS:
        zone_root = os.path.join(APP_DIR, zone)
        if not os.path.isdir(zone_root):
            continue
        for root, _dirs, files in os.walk(zone_root):
            for f in files:
                if not f.endswith(".py"):
                    continue
                abs_path = os.path.join(root, f)
                rel = os.path.relpath(abs_path, APP_DIR).replace("\\", "/")
                if any(part in f"/{rel}" for part in SKIP_DIR_PARTS):
                    continue
                if rel.endswith("__init__.py"):
                    continue  # 兼容性 re-export shim 豁免（同门禁脚本）
                try:
                    with open(abs_path, encoding="utf-8") as fh:
                        content = fh.read()
                except Exception:
                    continue
                for lineno, line in enumerate(content.splitlines(), 1):
                    stripped = line.strip()
                    if stripped.startswith("#"):
                        continue
                    for name, pat in _PATTERNS:
                        if pat.search(line):
                            result.setdefault(rel, {}).setdefault(name, 0)
                            result[rel][name] += 1
                            break  # 一行只计一次（先匹配者优先）
    return result


def load_baseline() -> dict[str, dict[str, int]]:
    if not os.path.exists(BASELINE_PATH):
        return {}
    with open(BASELINE_PATH, encoding="utf-8") as fh:
        return json.load(fh)


def save_baseline(current: dict[str, dict[str, int]]) -> None:
    with open(BASELINE_PATH, "w", encoding="utf-8") as fh:
        json.dump(current, fh, ensure_ascii=False, indent=2, sort_keys=True)
        fh.write("\n")


def compare(baseline: dict, current: dict) -> list[str]:
    """返回新增违规描述列表（空列表 = 通过）。"""
    new_violations: list[str] = []
    all_files = set(baseline) | set(current)
    for rel in sorted(all_files):
        base_cats = baseline.get(rel, {})
        curr_cats = current.get(rel, {})
        for cat, _pat in _PATTERNS:
            b = base_cats.get(cat, 0)
            c = curr_cats.get(cat, 0)
            if c > b:
                new_violations.append(f"  新增 I1 违规: {rel} [{cat}] {b} -> {c} (+{c - b})")
    return new_violations


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--update", action="store_true", help="用当前扫描结果覆盖基线（消减存量后收紧）")
    args = ap.parse_args()

    current = scan()
    current_total = sum(sum(cats.values()) for cats in current.values())

    if args.update:
        save_baseline(current)
        print(f"[I1 棘轮] 基线已更新：{current_total} 处 / {len(current)} 文件")
        for rel, cats in sorted(current.items()):
            for cat, n in cats.items():
                print(f"  {rel} [{cat}] x{n}")
        return 0

    baseline = load_baseline()
    if not baseline:
        print("[I1 棘轮] 基线不存在，视为首次运行——用当前状态建立基线")
        save_baseline(current)
        print(f"[I1 棘轮] 基线已建立：{current_total} 处 / {len(current)} 文件")
        return 0

    base_total = sum(sum(cats.values()) for cats in baseline.values())
    new_violations = compare(baseline, current)

    if new_violations:
        print(f"[I1 棘轮] 阻塞：发现基线外新增违规 {len(new_violations)} 处（基线 {base_total}）：")
        for v in new_violations:
            print(v)
        print()
        print("  契约 I1：四区禁止 import assistant / agent_gateway.adapters / smolagents。")
        print("  新增依赖请走 services/agent_gateway 端口，不要直连旧内核。")
        return 1

    improved = base_total - current_total
    if improved > 0:
        print(f"[I1 棘轮] 通过：{current_total} 处（基线 {base_total}，消减 {improved}）。")
        print("  运行 `python scripts/kernel_boundary_ratchet.py --update` 收紧基线固化本次消减。")
    else:
        print(f"[I1 棘轮] 通过：{current_total} 处 / {len(current)} 文件（与基线持平）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
