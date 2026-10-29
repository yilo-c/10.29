#!/usr/bin/env python3
"""出域直连棘轮门禁（M1.6a · egress ratchet）.

统计仓库内「直接 ``aiohttp.ClientSession(`` 调用点」vs「走统一工厂
``services/http_client.py`` 的调用点」，并以**棘轮**方式只减不增：

  * 基线文件记录当前每个生产文件的直连点数（存量豁免）
  * 基线之外的**新文件**出现直连 → 失败（新增文件禁直连）
  * 基线内文件的直连数**上升** → 失败（只减不增）
  * 直连数下降 → 通过，并提示可用 ``--update-baseline`` 收紧基线

默认范围：``思忆集app/`` 生产代码（排除 ``tests/``、``test_*.py``、``smoke/``、
``scripts/``、``.scripts/``、``_probe*/probe_*`` 等测试与工具目录——它们的直连
属于测试脚手架，不计入治理面）。``--all`` 可把工具/测试一并统计（只报告，不门禁）。

用法：
    python scripts/egress_ratchet.py                 # 校验当前代码 vs 基线
    python scripts/egress_ratchet.py --update-baseline   # 重算并写入基线
    python scripts/egress_ratchet.py --json              # 机器可读输出
    python scripts/egress_ratchet.py --all -v            # 含测试/工具的完整清单

设计说明：纯文本扫描（不 import 目标模块、不依赖第三方库），零副作用、CI 可直跑。
行内 ``# egress-ratchet: ignore`` 注释用于豁免个别无法迁移的历史行（如自定义
session 子类构造）。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_ROOTS = ("思忆集app", "scripts", ".scripts")
BASELINE = os.path.join(REPO_ROOT, "scripts", "egress-baseline.json")

EXCLUDE_DIR_NAMES = {
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    ".pytest-tmp",
    ".pytest-tmp-root",
    ".qoder",
    ".ruff_cache",
    ".venv",
    ".venv3.11",
    ".workbuddy",
    ".claude",
    "MagicMock",
    "__pycache__",
    "build",
    "dist",
    "node_modules",
    "ollama_models",
    "site-packages",
    "venv",
    "verify_tmp",
    "web_dist",
}

# 统一工厂模块本体（直连点的唯一合法来源）+ 本脚本自身（含正则字面量）
FACTORY_REL = "思忆集app/services/http_client.py"
SELF_REL = "scripts/egress_ratchet.py"

# 直连点：任意 ClientSession( 构造（aiohttp. 前缀或裸名）
DIRECT_RE = re.compile(r"\bClientSession\s*\(")
# 工厂调用点：egress_session( / new_session(
FACTORY_RE = re.compile(r"\b(?:egress_session|new_session)\s*\(")
# 工厂导入：from services.http_client import ... / import services.http_client / from .http_client import ...
FACTORY_IMPORT_RE = re.compile(
    r"^\s*(?:from\s+[\w.]*services\.http_client\s+import|import\s+[\w.]*services\.http_client"
    r"|from\s+[\w.]*services\s+import\s+[^\n#]*\bhttp_client\b"
    r"|from\s+\.+http_client\s+import)"
)
IGNORE_MARK = "egress-ratchet: ignore"

TOOLING_FILE_PREFIXES = ("test_", "probe_", "_probe", "_run_", "_check_")
TOOLING_FILE_SUFFIXES = ("_test.py",)


def _rel(path: str) -> str:
    try:
        return os.path.relpath(path, REPO_ROOT).replace("\\", "/")
    except ValueError:
        # 跨盘符（Windows）等情况：退回绝对路径，保证扫描不因基准目录不同而崩溃
        return os.path.abspath(path).replace("\\", "/")


def is_tooling(rel_path: str) -> bool:
    """测试脚手架 / 脚本工具：不计入出域治理面（默认豁免）。

    ``rel_path`` 是**相对扫描根**的路径（posix 分隔），因此判定不受临时目录、
    盘符、仓库位置影响。
    """
    parts = [p for p in rel_path.split("/") if p]
    if any(p in parts for p in ("tests", "test", "smoke", "scripts", ".scripts")):
        return True
    name = parts[-1] if parts else ""
    return name.startswith(TOOLING_FILE_PREFIXES) or name.endswith(TOOLING_FILE_SUFFIXES)


def _root_rel(path: str, root: str) -> str:
    base = root if os.path.isdir(root) else os.path.dirname(root)
    try:
        return os.path.relpath(path, base).replace("\\", "/")
    except ValueError:
        return os.path.basename(path)


def _root_is_tooling(root: str) -> bool:
    """扫描根本身是否属于工具/测试目录（如 ``scripts``、``.scripts``、``tests``）。"""
    base = root if os.path.isabs(root) else os.path.join(REPO_ROOT, root)
    try:
        rel = os.path.relpath(base, REPO_ROOT).replace("\\", "/")
    except ValueError:
        return False
    return bool(is_tooling(rel))


def iter_py_files(roots: list[str]) -> list[tuple[str, str, bool]]:
    """返回 (绝对路径, 相对该扫描根的路径, 该根是否工具目录)。"""
    found: list[tuple[str, str, bool]] = []
    for root in roots:
        base = root if os.path.isabs(root) else os.path.join(REPO_ROOT, root)
        root_tooling = _root_is_tooling(root)
        if os.path.isfile(base):
            found.append((base, _root_rel(base, root), root_tooling))
            continue
        if not os.path.isdir(base):
            continue
        for dirpath, dirnames, filenames in os.walk(base):
            dirnames[:] = sorted(d for d in dirnames if d not in EXCLUDE_DIR_NAMES)
            for fn in sorted(filenames):
                if fn.endswith(".py"):
                    full = os.path.join(dirpath, fn)
                    found.append((full, _root_rel(full, root), root_tooling))
    return found


def scan_file(path: str, root_rel: str | None = None, root_tooling: bool = False) -> dict:
    rel = _rel(path)
    direct: list[int] = []
    factory_sites = 0
    imports_factory = False
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            for lineno, line in enumerate(fh, start=1):
                code = line.split("#", 1)[0]
                if IGNORE_MARK in line:
                    continue
                if FACTORY_IMPORT_RE.search(code):
                    imports_factory = True
                if rel != FACTORY_REL and DIRECT_RE.search(code):
                    direct.append(lineno)
                if rel != FACTORY_REL and FACTORY_RE.search(code):
                    factory_sites += 1
    except OSError:
        return {
            "path": rel,
            "root_rel": root_rel or rel,
            "tooling": root_tooling,
            "error": "unreadable",
            "direct_lines": [],
            "factory_sites": 0,
            "imports_factory": False,
        }
    return {
        "path": rel,
        "root_rel": root_rel or rel,
        "tooling": root_tooling,
        "direct_lines": direct,
        "factory_sites": factory_sites,
        "imports_factory": imports_factory,
    }


def scan(roots: list[str]) -> dict:
    files = [scan_file(p, rr, rt) for p, rr, rt in iter_py_files(roots)]
    prod = [f for f in files if not (f["tooling"] or is_tooling(f["root_rel"])) and f["path"] != SELF_REL]
    tooling = [f for f in files if f["path"] != SELF_REL and (f["tooling"] or is_tooling(f["root_rel"]))]
    prod_direct = [f for f in prod if f["direct_lines"]]
    tool_direct = [f for f in tooling if f["direct_lines"]]
    return {
        "production_direct_total": sum(len(f["direct_lines"]) for f in prod_direct),
        "production_direct_files": len(prod_direct),
        "production_scanned": len(prod),
        "factory_sites": sum(f["factory_sites"] for f in files if f["path"] != SELF_REL),
        "factory_sites_production": sum(f["factory_sites"] for f in prod if f["path"] not in (SELF_REL, FACTORY_REL)),
        "factory_importers": sorted(f["path"] for f in files if f["imports_factory"] and f["path"] != SELF_REL),
        "production": {f["path"]: f["direct_lines"] for f in sorted(prod_direct, key=lambda x: -len(x["direct_lines"]))},
        "tooling_direct_total": sum(len(f["direct_lines"]) for f in tool_direct),
        "tooling": {f["path"]: f["direct_lines"] for f in sorted(tool_direct, key=lambda x: -len(x["direct_lines"]))},
    }


def load_baseline(path: str) -> dict | None:
    if not os.path.exists(path):
        return None
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        print(f"[egress-ratchet] 基线读取失败 {path}: {exc}", file=sys.stderr)
        return None


def write_baseline(path: str, result: dict) -> dict:
    data = {
        "tool": "egress_ratchet",
        "version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "factory": FACTORY_REL,
        "total_direct": result["production_direct_total"],
        "files": {p: len(lines) for p, lines in result["production"].items()},
    }
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2, sort_keys=True)
        fh.write("\n")
    return data


def check_against_baseline(result: dict, baseline: dict) -> list[str]:
    """返回违规清单（空 = 通过）。棘轮两条硬规则：新文件禁直连、存量只减不增。"""
    problems: list[str] = []
    base_files: dict[str, int] = {str(k): int(v) for k, v in (baseline.get("files") or {}).items()}
    current: dict[str, int] = {p: len(lines) for p, lines in result["production"].items()}
    for path, count in sorted(current.items()):
        allowed = base_files.get(path)
        if allowed is None:
            problems.append(f"新增直连文件（基线外，禁直连）: {path} 直连 {count} 处 → 请改用 {FACTORY_REL}")
            continue
        if count > allowed:
            problems.append(f"直连点上升（棘轮只减不增）: {path} 基线 {allowed} → 当前 {count}")
    total_allowed = int(baseline.get("total_direct", sum(base_files.values())))
    if result["production_direct_total"] > total_allowed:
        problems.append(f"生产直连总数上升: 基线 {total_allowed} → 当前 {result['production_direct_total']}")
    return problems


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="出域 ClientSession 直连棘轮门禁")
    ap.add_argument("--root", action="append", dest="roots", help="扫描根（可重复），默认 思忆集app/scripts/.scripts")
    ap.add_argument("--baseline", default=BASELINE)
    ap.add_argument("--update-baseline", "-w", action="store_true", help="以当前状态重写基线")
    ap.add_argument("--json", action="store_true", dest="as_json")
    ap.add_argument("--all", action="store_true", help="同时统计测试/工具目录（仅报告）")
    ap.add_argument("-v", "--verbose", action="store_true", help="逐文件列出直连行号")
    args = ap.parse_args(argv)

    roots = args.roots or list(DEFAULT_ROOTS)
    result = scan(roots)

    if args.update_baseline:
        data = write_baseline(args.baseline, result)
        print(f"[egress-ratchet] 基线已写入 {args.baseline}：生产直连 {data['total_direct']} 处 / {len(data['files'])} 文件")
        return 0

    if args.as_json:
        payload = dict(result)
        payload["roots"] = roots
        payload["baseline"] = load_baseline(args.baseline)
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    print("出域直连棘轮（M1.6a）")
    print(f"  扫描范围        : {', '.join(roots)}")
    print(f"  生产文件数      : {result['production_scanned']}")
    print(f"  直连 ClientSession: {result['production_direct_total']} 处 / {result['production_direct_files']} 文件（生产口径）")
    print(
        f"  走工厂调用点    : {result['factory_sites_production']}（生产口径；全部口径 {result['factory_sites']}，"
        f"导入工厂模块的文件：{len(result['factory_importers'])}）"
    )
    print(f"  测试/工具直连    : {result['tooling_direct_total']} 处（不计入门禁；--all 查看清单）")

    if args.verbose or args.all:
        print("\n  生产直连清单（文件:行号）")
        for path, lines in result["production"].items():
            print(f"    {path}:{','.join(str(n) for n in lines)}")
        if args.all:
            print("\n  测试/工具直连清单（豁免）")
            for path, lines in result["tooling"].items():
                print(f"    {path}:{','.join(str(n) for n in lines)}")

    baseline = load_baseline(args.baseline)
    if baseline is None:
        print(f"\n[egress-ratchet] 缺基线 {args.baseline} —— 先跑 --update-baseline 建立存量豁免", file=sys.stderr)
        return 2

    problems = check_against_baseline(result, baseline)
    base_total = int(baseline.get("total_direct", 0))
    if problems:
        print("\n违规：", file=sys.stderr)
        for p in problems:
            print(f"  ✗ {p}", file=sys.stderr)
        return 1
    if result["production_direct_total"] < base_total:
        print(f"\n✓ 通过（较基线减少 {base_total - result['production_direct_total']} 处；可跑 --update-baseline 收紧棘轮）")
    else:
        print("\n✓ 通过（与基线持平，未新增直连）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
