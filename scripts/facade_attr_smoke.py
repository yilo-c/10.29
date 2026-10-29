# -*- coding: utf-8 -*-
"""facade_attr_smoke —— 共因 B 门禁雏形：AppContext / 领域门面「死属性」对表扫描。

背景（防复发）：全仓反复出现「handler 读 context.X / getattr(context,"X")，而 X
从未挂载在 AppContext 或任何领域门面上」→ 恒 None → 静默死案（"xxx not
available"）。历史四案：
  - autonomy_layer / autonomy   （_tools.py 旧 _get_router，tool.* 全族，AJ-1）
  - daily_scan / judge / create_backup（cron handler 臆造**子服务对象上的方法名**，
    属"方法面"，由 check_method_coverage.py +
    tests/test_cron_handler_attribute_fixes_20260916.py 覆盖；本脚本专抓
    context/门面**这一层**的"引用了但从未挂载的属性"）。

模型（纯 AST，只读，不改任何文件）：
  ① 消费集（限定在 AppContext 恒成立的目录 backend/ + app_runtime/）：
       context.<attr> / ctx.<attr>            （属性读取，非赋值）
       getattr(context|ctx, "<attr>", ...)    （字面量属性名，第一参须恰为 Name）
  ② 挂载集（**全仓**扫描）：任何 `obj.<attr> =`、`setattr(obj, "<attr>", ...)`
     出现即视为「有地方会挂这个名字」——覆盖 RPC handler 里的
     `context.provider_health_monitor = monitor`、`setattr(context,
     "_propose_words_for_article", ...)` 等动态挂载；再并上
     AppContext/混入的 self.X= / 类注解 / @property / def X(self)、
     _context_builder 的 ctx.X=、headless 的 setattr(context,"X")，
     以及 context_facades 各 _DomainFacade 子类的 _attrs 白名单。
  ③ 死案 = 消费集 - 挂载集 - 豁免；非空 exit 1。

为何消费限定 backend/ + app_runtime/：仓库里同名变量 `context`/`ctx` 在
assistant/（ExecutionCtx·RunContext）、cross_device/、services/job_hunter/
（Playwright BrowserContext）、alembic/（MigrationContext）等指别的对象，直接
属性访问会制造海量假阳性。backend/ 是 JSON-RPC handler 层、app_runtime/ 是
AppContext 装配层，二者 `context`/`ctx` 严格 = AppContext/领域门面（AJ-1 恰在此层）。

用法：
  python scripts/facade_attr_smoke.py                    # 扫工作树，死案非空 exit 1
  python scripts/facade_attr_smoke.py --include-file P    # 额外把文件 P 计入「消费」
                                                          #   （即便位于排除目录/未跟踪），
                                                          #   用于对 git show 旧版做「先红」取证
"""

from __future__ import annotations

import argparse
import ast
import io
import os
import re
import sys
from pathlib import Path

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "思忆集app"

# 扫描时跳过的目录名（路径任意片段命中即排除）
EXCLUDE_DIRS = {
    "tests",
    "verify_tmp",
    "__pycache__",
    ".git",
    "build",
    "dist",
    ".venv3.11",
    ".venv",
    "node_modules",
    ".idea",
    ".pytest_cache",
    "browser-extension",
    "cloudflare",
    "思忆集手机端app",
    "desk-notes",
    "desktop-pet-qinai",
    "alembic",
    "MagicMock",
}

# getattr(context,...) 里「带默认值、缺失即安全跳过/退化」的**可选共享实例 / 兜底别名**读取。
# 与 AJ-1（autonomy_layer 那种"读错名字→恒 None→致命死案"）不同：这些缺失时
# 不崩、有明确退化路径（临时实例 / 真名兜底 / 跳过可选特性），属已核实非致命，
# 列入待办另案治理（下方逐条注退化方式）。任何**新增**的、不在本表的未挂载读取仍会 FAIL。
KNOWN_FALLBACK_ALLOW: set[str] = {
    "capability_router",  # 测试 harness 直挂兼容路径，_tools/butler 均有 hasattr 守卫
    "data_store",  # 真名 local_data_store，services 里 getattr(...,None) 容错
    "task_manager",  # 兜底走 autonomous_assistant_manager.task_manager
    "settings",  # `or getattr(context,"settings_repository")` 真名兜底
    "info_source_registry",  # _info_sources/_inbox_info：共享实例缺失即建临时实例
    "rss_subscriber",  # _rss：同上，共享实例缺失即建临时实例
    "aggregator",  # aggregate_path：无聚合器则整条 A2A 聚合路径直接 return 跳过
    "image_generator_service",  # _image：未注入则返回带说明的 error dict（特性未接线，backlog）
}

# (文件路径片段, 属性名) 级豁免——同名变量≠AppContext 的作用域遮蔽点（逐个核实过）：
FILE_ATTR_ALLOW: set[tuple[str, str]] = {
    # _notes.py:626 `context.strip()`——该函数参数 context 是语气提示**字符串**
    # （prompt 参数遮蔽 AppContext 名），非属性访问面；建议后续改名 tone_hint_src
    ("backend/handlers/_notes.py", "strip"),
    # headless_main.py:177/178 `ctx.get(...)`——是 asyncio 未处理异常回调 lambda 的
    # 第二形参 ctx（异常上下文 **dict**），与 AppContext 无关，典型同名遮蔽假阳性
    ("backend/headless_main.py", "get"),
}

# 消费集只在这些"context=AppContext"目录里采集
CONSUME_ROOTS = [APP / "backend", APP / "app_runtime"]
# 挂载集全仓采集（含 services/assistant 等一切 setattr 动态挂载）

# 消费根只认 `context`（handler 签名惯例 register_xxx(registry, context)）。
# 本仓 `ctx` 在 backend/ 内大量指 asyncio exception handler 的 dict、builder 临时
# 对象等**非 AppContext** 同名遮蔽（如 headless_main.py:177 ctx.get("message")），
# 纳入消费面会假阳性；`ctx.X=` 注入仍全部计入挂载集（collect_declared 2b 段）。
CONTEXT_ROOTS = {"context"}

_ATTR = r"[A-Za-z_][A-Za-z0-9_]*"


def _excluded(path: Path) -> bool:
    return bool(set(path.parts) & EXCLUDE_DIRS)


def _walk_py(base: Path) -> list[Path]:
    out: list[Path] = []
    if not base.is_dir():
        return out
    for dirpath, dirnames, filenames in os.walk(base, onerror=lambda e: None):
        dirnames[:] = [d for d in dirnames if d not in EXCLUDE_DIRS and d != "node_modules"]
        dp = Path(dirpath)
        if _excluded(dp):
            continue
        for fn in filenames:
            if fn.endswith(".py"):
                out.append(dp / fn)
    return out


def _iter_all_py() -> list[Path]:
    files: list[Path] = []
    for base in (APP, ROOT / "scripts"):
        files.extend(_walk_py(base))
    return files


# ── 1. 消费集：限定目录内 context/ctx 字面量属性读取 ─────────────────────
def collect_consumed(files: list[Path]) -> dict[str, list[tuple[str, int]]]:
    consumed: dict[str, list[tuple[str, int]]] = {}

    def record(name: str, rel: str, line: int) -> None:
        consumed.setdefault(name, []).append((rel, line))

    for f in files:
        try:
            tree = ast.parse(f.read_text(encoding="utf-8", errors="replace"), filename=str(f))
        except (SyntaxError, ValueError):
            continue
        rel = str(f.relative_to(ROOT)) if f.is_relative_to(ROOT) else str(f)
        # 「context.X = / ctx.X =」是挂载动作，不当作消费
        assign_targets = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                for t in node.targets:
                    base, attr = _strict_context_attr(t)
                    if base:
                        assign_targets.add((base, attr))
        for node in ast.walk(tree):
            # 形态 A：getattr(context|ctx, "X")——第一参须恰为 Name(context|ctx)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "getattr" and len(node.args) >= 2:
                a0 = node.args[0]
                base = a0.id if isinstance(a0, ast.Name) else None
                a = node.args[1]
                if base in CONTEXT_ROOTS and isinstance(a, ast.Constant) and isinstance(a.value, str):
                    if (base, a.value) not in assign_targets:
                        record(a.value, rel, node.lineno)
                continue
            # 形态 B：context.X / ctx.X 属性读取（严格 Name 根，非赋值）
            base, attr = _strict_context_attr(node)
            if base and (base, attr) not in assign_targets:
                record(attr, rel, node.lineno)
    return consumed


def _strict_context_attr(node: ast.AST) -> tuple[str | None, str | None]:
    """node 恰为 context.X / ctx.X → ('context'|'ctx', 'X')；否则 (None, None)。"""
    if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id in CONTEXT_ROOTS:
        return node.value.id, node.attr
    return None, None


# ── 2. 挂载集：全仓赋值/setattr + AppContext 声明 + 门面白名单 ────────────
def collect_declared() -> set[str]:
    declared: set[str] = set()
    files = _iter_all_py()

    # 2a. 全仓：`context.X =` / `ctx.X =` / `setattr(context|ctx, "X", ...)`
    #     —— 动态挂载点（RPC handler 内偶发的 context.provider_health_monitor=、
    #     _word_extract 的 setattr(context,"_propose_words_for_article",...) 等）。
    #     只认根为 context/ctx 的赋值，避免把 ai_manager.self.aggregator 这类
    #     「别的对象的同名属性」误判为已挂载。
    for f in files:
        try:
            tree = ast.parse(f.read_text(encoding="utf-8", errors="replace"), filename=str(f))
        except (SyntaxError, ValueError):
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                for t in node.targets:
                    base, attr = _strict_context_attr(t)
                    if base:
                        declared.add(attr)
            elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "setattr" and len(node.args) >= 2:
                a0 = node.args[0]
                base = a0.id if isinstance(a0, ast.Name) else None
                nm = node.args[1]
                if base in CONTEXT_ROOTS and isinstance(nm, ast.Constant) and isinstance(nm.value, str):
                    declared.add(nm.value)

    # 2b. AppContext + _context_*.py 混入：self.X= / 注解=None / @property / def X(self)
    ar = APP / "app_runtime"
    re_self = re.compile(rf"self\.({_ATTR})\s*=")
    re_annot = re.compile(rf"^\s+({_ATTR})\s*:\s*\S.*=\s*None", re.M)
    re_plain = re.compile(rf"^\s+({_ATTR})\s*=\s*None", re.M)
    re_prop = re.compile(rf"@property\s+def\s+({_ATTR})", re.M)
    re_meth = re.compile(rf"^\s+def\s+({_ATTR})\s*\(\s*self", re.M)
    for f in [ar / "app_context.py", *sorted(ar.glob("_context_*.py"))]:
        if not f.is_file():
            continue
        src = f.read_text(encoding="utf-8", errors="replace")
        declared.update(re_self.findall(src))
        declared.update(re_annot.findall(src))
        declared.update(re_plain.findall(src))
        declared.update(re_prop.findall(src))
        declared.update(re_meth.findall(src))

    # 2c. _context_builder.py 的 ctx.X = 注入
    builder = ar / "_context_builder.py"
    if builder.is_file():
        bsrc = builder.read_text(encoding="utf-8", errors="replace")
        declared.update(re.findall(rf"\bctx\.({_ATTR})\s*=", bsrc))

    # 2d. context_facades.py 各 _DomainFacade 子类的 _attrs 白名单
    facades = ar / "context_facades.py"
    if facades.is_file():
        declared.update(_facade_attr_whitelist(facades.read_text(encoding="utf-8", errors="replace")))

    declared.add("event_bus")  # core.events 模块级单例
    return declared


def _facade_attr_whitelist(src: str) -> set[str]:
    """收集各 _DomainFacade 子类里 `_attrs = (..., "x", ...)` 的字符串常量。"""
    out: set[str] = set()
    try:
        tree = ast.parse(src)
    except (SyntaxError, ValueError):
        return out

    def _target_names(stmt) -> list[str]:
        if isinstance(stmt, ast.Assign):
            return [t.id for t in stmt.targets if isinstance(t, ast.Name)]
        if isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
            return [stmt.target.id]
        return []

    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        for stmt in node.body:
            if "_attrs" not in _target_names(stmt):
                continue
            val = getattr(stmt, "value", None)
            if isinstance(val, (ast.Tuple, ast.List)):
                for e in val.elts:
                    if isinstance(e, ast.Constant) and isinstance(e.value, str):
                        out.add(e.value)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--include-file", action="append", default=[], help="额外计入【消费】扫描的文件（即便位于排除目录），用于 git show 旧版取证")
    args = ap.parse_args()

    consume_files = []
    for base in CONSUME_ROOTS:
        consume_files.extend(_walk_py(base))
    for p in args.include_file:
        pp = Path(p).resolve()
        if pp.exists():
            consume_files.append(pp)
        else:
            print(f"[warn] --include-file 不存在: {p}", file=sys.stderr)

    consumed = collect_consumed(consume_files)
    declared = collect_declared()

    dead: dict[str, list[tuple[str, int]]] = {}
    for name, locs in sorted(consumed.items()):
        if name in declared or name in KNOWN_FALLBACK_ALLOW:
            continue
        kept = []
        for rel, line in locs:
            rel_norm = rel.replace("\\", "/")
            if any(frag in rel_norm and a == name for frag, a in FILE_ATTR_ALLOW):
                continue  # 异对象/作用域遮蔽，非 AppContext 面，逐点豁免
            kept.append((rel, line))
        if kept:
            dead[name] = kept

    print("== facade_attr_smoke ==")
    print(
        f"消费扫描目录: {[str(x.relative_to(ROOT)) for x in CONSUME_ROOTS]}"
        + (f" + {len(args.include_file)} 个 --include-file" if args.include_file else "")
    )
    print(f"挂载集全仓扫描 .py 文件数: {len(_iter_all_py())}")
    print(f"消费属性名（AppContext 面）: {len(consumed)}")
    print(f"挂载属性名（赋值/setattr/声明/门面白名单）: {len(declared)}")
    print(f"豁免（容错兜底/可选共享实例，已核实非致命，另案）: {sorted(KNOWN_FALLBACK_ALLOW)}")
    print(f"逐点豁免（同名遮蔽假阳性）: {sorted(FILE_ATTR_ALLOW)}")
    print(f"死案清单（引用了但全仓从未挂载、且非容错兜底）: {len(dead)}")
    for name, locs in dead.items():
        print(f"  ✗ {name}")
        for rel, line in locs[:8]:
            print(f"      {rel}:{line}")

    if dead:
        print("\nRESULT: FAIL —— 存在引用但从未挂载的 context/门面死属性（见上），exit 1")
        return 1
    print("\nRESULT: PASS —— 无新增 AppContext/门面死属性")
    return 0


if __name__ == "__main__":
    sys.exit(main())
