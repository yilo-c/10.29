"""后端 JSON-RPC 方法调用覆盖率检查（S11 产出）。

扫描后端所有 registry.register(...) 注册的方法，并与前端（desk-notes / 手机端app）
及后端内部的真实调用做交叉比对，输出未调用方法报告。

用途:
    - 本地排查「我新增的后端方法有没有被前端调用」
    - CI 中作为契约测试 test_no_new_unused_methods 的补充报告工具

退出码:
    0 — 无新增未调用方法（与 tests/test_jsonrpc_contract.py 的基线一致）
    1 — 存在基线之外的未调用方法（僵尸接口）

用法:
    python scripts/check_method_coverage.py
    python scripts/check_method_coverage.py --json report.json

详见: claudejiaoben/audit_unused_methods_report.md
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

# [stdio-utf8-guard] 中文 Windows 控制台(cp1252)下 print 中文会 UnicodeEncodeError 并退出 1，
# 导致 CI quality-gates 中断、后续步骤全部 skipped。详见 scripts/_stdio.py。
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
except Exception:
    pass

REPO_ROOT = Path(__file__).resolve().parents[1]
APP_DIR = REPO_ROOT / "思忆集app"
BACKEND = APP_DIR / "backend"

CALLER_ROOTS = [
    REPO_ROOT / "desk-notes",
    REPO_ROOT / "思忆集手机端app",
]

EXCLUDE_DIRS = {
    ".git", "node_modules", "dist", "build", ".next", "coverage",
    "web_dist", "assets", "generated-images", "__pycache__", "archive",
}

REG_RE = re.compile(
    r"""\bregister(?:_with_schema)?\s*\(\s*["']([a-zA-Z][a-zA-Z0-9_.]+)["']"""
)

# 动态注册前缀（与 test_jsonrpc_contract.py 保持一致）
# 2026-08-27 R2-4：移除 `info_sources.`（下划线）死豁免——真实注册名是 `info.sources.`（点号），
# 该前缀永不匹配任何注册方法；info.sources.* 未文档化问题由 --undocumented 门禁覆盖。
DYNAMIC_PREFIXES = {
    "cron.", "job_hunter.", "rss.", "process.", "timeline.",
    "task_queue.",
}

# 前端真实 RPC 调用模式
CALL_PATTERNS = [
    re.compile(r"""\.call<[^>]*>\s*\(\s*["']([a-zA-Z][a-zA-Z0-9_.]+)["']"""),
    re.compile(r"""\.stream(?:Typed)?<[^>]*>\s*\(\s*["']([a-zA-Z][a-zA-Z0-9_.]+)["']"""),
    re.compile(
        r"""\b(?:client|wsClient|ws|syncCall|invoke|router|bridge)\b\.call\s*\(\s*["']([a-zA-Z][a-zA-Z0-9_.]+)["']"""
    ),
    re.compile(r"""\bcallRpc\s*\(\s*["']([a-zA-Z][a-zA-Z0-9_.]+)["']"""),
]
# 点号字符串字面量（粗匹配，含白名单/常量定义）
DOTSTR_RE = re.compile(r"""['"]([a-zA-Z][a-zA-Z0-9_]+\.[a-zA-Z0-9_.]+)['"]""")

# AGENTS.md 文档化方法提取：反引号包裹的 `域.方法` 形态（排除 .py / 路径 / 行号引用）
AGENTS_MD = REPO_ROOT / "AGENTS.md"
AGENTS_METHOD_RE = re.compile(r"`([A-Za-z][A-Za-z0-9_.]*\.[A-Za-z0-9_]+)`")
UNDOCUMENTED_BASELINE_FILE = REPO_ROOT / "scripts" / "undocumented_methods_baseline.txt"


def extract_documented_methods() -> set[str]:
    """从 AGENTS.md 方法表提取已文档化方法名（反引号 `域.方法`，排除文件/路径形态）。"""
    if not AGENTS_MD.exists():
        return set()
    text = AGENTS_MD.read_text(encoding="utf-8", errors="ignore")
    names = set(AGENTS_METHOD_RE.findall(text))
    return {
        n for n in names
        if ".py" not in n and "/" not in n and ":" not in n and n.count(".") >= 1
    }


def load_undocumented_baseline() -> set[str]:
    """读取未文档化方法基线（一行一个方法名）。

    utf-8-sig：容忍编辑器/PowerShell 写入的 UTF-8 BOM（否则首行混入 \\ufeff
    被误判为基线外方法，产生假红）。
    """
    if not UNDOCUMENTED_BASELINE_FILE.exists():
        return set()
    return {
        ln.strip() for ln in
        UNDOCUMENTED_BASELINE_FILE.read_text(encoding="utf-8-sig", errors="ignore").splitlines()
        if ln.strip() and not ln.strip().startswith("#")
    }


def check_undocumented(registered: set[str]) -> tuple[int, set[str], set[str]]:
    """返回 (未文档化总数, 新增未文档化, 基线中已删除/改名)."""
    documented = extract_documented_methods()
    baseline = load_undocumented_baseline()
    undocumented = registered - documented
    new_undoc = undocumented - baseline
    gone = baseline - registered
    return len(undocumented), new_undoc, gone


def extract_registered() -> dict[str, list[str]]:
    registered: dict[str, list[str]] = {}
    for py in BACKEND.rglob("*.py"):
        if any(part in EXCLUDE_DIRS for part in py.parts):
            continue
        try:
            text = py.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for m in REG_RE.finditer(text):
            registered.setdefault(m.group(1), []).append(
                str(py.relative_to(REPO_ROOT)))
    return registered


def scan_callers() -> tuple[set[str], int]:
    refs: set[str] = set()
    file_count = 0
    for root in CALLER_ROOTS:
        if not root.exists():
            continue
        for dp, dn, fn in _walk(root):
            for f in fn:
                if not f.endswith((".ts", ".tsx", ".js", ".jsx", ".vue")):
                    continue
                full = Path(dp) / f
                try:
                    lines = full.read_text(encoding="utf-8", errors="ignore").splitlines()
                except OSError:
                    continue
                file_count += 1
                for raw in lines:
                    s = raw.strip()
                    if s.startswith("//") or s.startswith("*") or s.startswith("#"):
                        continue
                    for pat in CALL_PATTERNS:
                        refs.update(pat.findall(raw))
                    refs.update(DOTSTR_RE.findall(raw))
    return refs, file_count


def _walk(root: Path):
    import os
    for dp, dn, fn in os.walk(root):
        dn[:] = [d for d in dn if d not in EXCLUDE_DIRS]
        yield dp, dn, fn


def scan_backend_internal() -> set[str]:
    refs: set[str] = set()
    for py in BACKEND.rglob("*.py"):
        if any(part in EXCLUDE_DIRS for part in py.parts):
            continue
        try:
            text = py.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        refs.update(DOTSTR_RE.findall(text))
    return refs


def is_dynamic(method: str) -> bool:
    return any(method.startswith(p) for p in DYNAMIC_PREFIXES)


def main() -> int:
    parser = argparse.ArgumentParser(description="后端方法调用覆盖率检查")
    parser.add_argument("--json", dest="json_out", help="JSON 报告输出路径")
    parser.add_argument("--baseline", help="基线文件（JSON 数组），默认从 test_jsonrpc_contract.py 读取")
    parser.add_argument(
        "--undocumented", action="store_true",
        help="检查未文档化方法（注册但不在 AGENTS.md 方法表）。"
             "存量未文档化入 scripts/undocumented_methods_baseline.txt 基线不阻塞；"
             "新增未文档化（基线之外）→ 退出码 1（堵流式新债，原则 P-4）",
    )
    args = parser.parse_args()

    registered = extract_registered()
    all_methods = set(registered.keys())
    frontend_refs, fc = scan_callers()
    backend_refs = scan_backend_internal()
    referenced = frontend_refs | backend_refs

    unused = {
        m for m in all_methods
        if m not in referenced and not is_dynamic(m)
    }

    # 读取基线（与 test_jsonrpc_contract.py 的 UNUSED_METHODS_BASELINE 对齐）
    baseline = _load_baseline()
    new_unused = unused - baseline
    gone = baseline - all_methods

    print(f"[check-method-coverage] 已注册方法: {len(all_methods)}")
    print(f"[check-method-coverage] 前端源文件: {fc}")
    print(f"[check-method-coverage] 未调用方法: {len(unused)} (基线 {len(baseline)})")
    print(f"[check-method-coverage] 新增未调用: {len(new_unused)}")
    print(f"[check-method-coverage] 基线已失效(方法已删除): {len(gone)}")

    if new_unused:
        print("\n==== 新增未调用方法（僵尸接口）====")
        for m in sorted(new_unused):
            print(f"  + {m}  注册于: {registered.get(m, [])}")
    if gone:
        print("\n==== 基线中已不存在的注册方法（请从基线移除）====")
        for m in sorted(gone):
            print(f"  - {m}")

    exit_code = 1 if (new_unused or gone) else 0

    if args.undocumented:
        undoc_total, new_undoc, undoc_gone = check_undocumented(all_methods)
        print(f"\n[check-method-coverage] 未文档化方法: {undoc_total} "
              f"(基线 {len(load_undocumented_baseline())})")
        print(f"[check-method-coverage] 新增未文档化: {len(new_undoc)}")
        print(f"[check-method-coverage] 基线已失效(已文档化/已删除): {len(undoc_gone)}")
        if new_undoc:
            print("\n==== 新增未文档化方法（P-4 流式新债！请补 AGENTS.md 方法表）====")
            for m in sorted(new_undoc):
                print(f"  + {m}  注册于: {registered.get(m, [])}")
        if undoc_gone:
            print("\n==== 基线中已文档化/已删除的方法（请从基线移除）====")
            for m in sorted(undoc_gone):
                print(f"  - {m}")
        if new_undoc:
            exit_code = 1

    if args.json_out:
        report = {
            "total_registered": len(all_methods),
            "unused_count": len(unused),
            "new_unused_count": len(new_unused),
            "new_unused": sorted(new_unused),
            "gone_from_baseline": sorted(gone),
            "unused_all": sorted(unused),
        }
        if args.undocumented:
            report["undocumented_count"] = undoc_total
            report["new_undocumented"] = sorted(new_undoc)
            report["undocumented_gone_from_baseline"] = sorted(undoc_gone)
        Path(args.json_out).write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n[check-method-coverage] JSON 报告已写入 {args.json_out}")

    # 退出码：有新增未调用 / 基线过期 / 新增未文档化 → 1
    return exit_code


def _load_baseline() -> set[str]:
    """从 test_jsonrpc_contract.py 解析 UNUSED_METHODS_BASELINE 集合。"""
    contract = APP_DIR / "tests" / "test_jsonrpc_contract.py"
    if not contract.exists():
        return set()
    text = contract.read_text(encoding="utf-8", errors="ignore")
    # 提取 UNUSED_METHODS_BASELINE: set[str] = { ... } 块中的字符串字面量
    m = re.search(
        r"UNUSED_METHODS_BASELINE\s*:\s*set\[str\]\s*=\s*\{(.*?)\}",
        text, re.DOTALL,
    )
    if not m:
        return set()
    return set(re.findall(r'"([^"]+)"', m.group(1)))


if __name__ == "__main__":
    raise SystemExit(main())
