"""R0 结构测试：context 属性名接线一致性（facade↔builder + 死接线负向守卫）。

对应 deliverables/audit-report-2026-08-27.md R0 #5。不依赖任何运行时注入，
纯静态解析，避免误报：

1. ``ChatFacade._attrs`` 必须是 ``_context_builder`` 的 ctx 注入集子集 —— 守卫 B-19
   类「门面列了 AppContext 不存在的属性名 → 恒返 None」漂移复发；
2. ``backend/handlers`` 中不得再出现已知的死接线属性名
   （note_repository / task_repository / notes_repository / knowledge_graph /
   graph_store / memory_index / structured_store）——守卫 B-17/19/20/21 复发。
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

APP = Path(__file__).resolve().parents[1]  # 思忆集app

# 已修复的死接线属性名（生产注入名分别为 personal_* / *_store / *_service）。
KNOWN_BAD = {
    "note_repository",
    "task_repository",
    "notes_repository",
    "knowledge_graph",
    "graph_store",
    "memory_index",
    "structured_store",
}


def _builder_injected() -> set[str]:
    """提取 context 真实注入属性名集合：builder 的 `ctx.X =` 与
    headless_main 的 `setattr(context, "X", ...)` / `context.X =` 并集。

    仅看 builder 会漏掉 headless_main 用 setattr 注入的属性（如 trace_recorder），
    导致对合法 facade 属性误报。
    """
    names: set[str] = set()
    builder = (APP / "app_runtime" / "_context_builder.py").read_text(encoding="utf-8")
    names |= set(re.findall(r"\bctx\.([A-Za-z_][A-Za-z0-9_]*)\s*=", builder))
    headless = (APP / "backend" / "headless_main.py").read_text(encoding="utf-8")
    names |= set(re.findall(r'setattr\(\s*context\s*,\s*"([A-Za-z_][A-Za-z0-9_]*)"', headless))
    names |= set(re.findall(r"\bcontext\.([A-Za-z_][A-Za-z0-9_]*)\s*=", headless))
    return names


def _facade_attrs() -> tuple[str, ...]:
    """静态解析 ChatFacade._attrs 元组内容。"""
    src = (APP / "app_runtime" / "context_facades.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == "ChatFacade":
            for n in node.body:
                if isinstance(n, ast.Assign) and getattr(n.targets[0], "id", None) == "_attrs":
                    return tuple(e.value for e in n.value.elts)  # type: ignore[attr-defined]
    raise AssertionError("ChatFacade._attrs not found")


def test_facade_attrs_subset_of_builder_injected() -> None:
    injected = _builder_injected()
    attrs = _facade_attrs()
    missing = [a for a in attrs if a not in injected]
    assert not missing, f"ChatFacade._attrs 含 builder 未注入的属性（死接线/静默 None 风险，B-19 复发）：{missing}"


# 2026-08-29 扩围：扫描目录从 backend/handlers 扩到 services/ 等，并同时覆盖
# 「直接属性访问 context.X」形态。旧版只扫 handlers 且只认 getattr(context,"X")，
# 导致 services/cron_actions.py:90-91 的 context.task_repository / note_repository
# 死接线漏报数月（主动管家每 30 分钟全失败，见 2026-08-29 审查 H1）。
SCAN_DIRS = ("backend", "services", "app_runtime", "assistant")

# 形态 A：getattr(context|ctx, "X", ...)
GETATTR_RE = re.compile(r'getattr\(\s*(?:context|ctx)\s*,\s*"([A-Za-z_][A-Za-z0-9_]*)"')
# 形态 B：直接属性访问 context.X / ctx.X（排除赋值 `ctx.X = ...`，排除 `==` 比较）
DIRECT_RE = re.compile(r"\b(?:context|ctx)\.([A-Za-z_][A-Za-z0-9_]*)\s*(?!=[^=])")


def test_no_known_bad_context_attr_wiring() -> None:
    bad_hits: list[str] = []
    for d in SCAN_DIRS:
        base = APP / d
        if not base.is_dir():
            continue
        for f in sorted(base.rglob("*.py")):
            if "__pycache__" in str(f):
                continue
            src = f.read_text(encoding="utf-8", errors="replace")
            rel = str(f.relative_to(APP))
            lines = src.splitlines()
            for m in GETATTR_RE.finditer(src):
                if m.group(1) in KNOWN_BAD:
                    bad_hits.append(f"{rel}:{src.count(chr(10), 0, m.start()) + 1} getattr {m.group(1)}")
            for m in DIRECT_RE.finditer(src):
                if m.group(1) not in KNOWN_BAD:
                    continue
                lineno = src.count("\n", 0, m.start())
                # 跳过注释行（故障说明里常会写出旧属性名本身）
                if lineno < len(lines) and lines[lineno].strip().startswith("#"):
                    continue
                bad_hits.append(f"{rel}:{lineno + 1} attr {m.group(1)}")
    assert not bad_hits, f"发现已知死接线属性名（B-17/19/20/21 复发）：{bad_hits}"
