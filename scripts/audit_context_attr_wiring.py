"""R0 同型扫描：context 属性名接线一致性。

提取真实注入/声明集（builder ctx.X=、headless_main setattr、AppContext+mixin property、
类注解），与 backend/handlers 的 getattr(context,"X") 消费名交叉比对，输出死接线。
只读扫描，不修改任何文件。
"""
from __future__ import annotations

import io
import re
import sys
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "思忆集app"

# ── 1. 注入/声明集 ──
injected: set[str] = set()

# 1a. _context_builder.py: ctx.xxx =
builder_src = (APP / "app_runtime" / "_context_builder.py").read_text(encoding="utf-8")
injected |= set(re.findall(r"\bctx\.([A-Za-z_][A-Za-z0-9_]*)\s*=", builder_src))

# 1b. headless_main.py: setattr(context, "xxx", ...) + context.xxx = ... (含多行)
headless_src = (APP / "backend" / "headless_main.py").read_text(encoding="utf-8")
injected |= set(re.findall(r'setattr\(\s*context\s*,\s*"([A-Za-z_][A-Za-z0-9_]*)"', headless_src, re.S))
injected |= set(re.findall(r"\bcontext\.([A-Za-z_][A-Za-z0-9_]*)\s*=", headless_src))

# 1c. AppContext + mixins 的 @property / 类注解 / __init__ 赋值 / 方法
for f in (
    "app_context.py",
    "_context_cron.py",
    "_context_memory.py",
    "_context_config.py",
):
    src = (APP / "app_runtime" / f).read_text(encoding="utf-8")
    injected |= set(re.findall(r"^\s+([A-Za-z_][A-Za-z0-9_]*)\s*:\s*(Any|.*?)\s*=\s*None", src, re.M))
    injected |= set(re.findall(r"^\s+([A-Za-z_][A-Za-z0-9_]*)\s*=\s*None", src, re.M))
    injected |= set(re.findall(r"self\.([A-Za-z_][A-Za-z0-9_]*)\s*=", src))
    # @property def xxx(self)
    injected |= set(re.findall(r"@property\s+def\s+([A-Za-z_][A-Za-z0-9_]*)", src, re.M))
    # 普通方法 def xxx(self) —— 也是属性
    injected |= set(re.findall(r"^\s+def\s+([A-Za-z_][A-Za-z0-9_]*)\s*\(\s*self", src, re.M))

# 1d. 其他 _context_*.py mixins
for f in (APP / "app_runtime").glob("_context_*.py"):
    src = f.read_text(encoding="utf-8")
    injected |= set(re.findall(r"self\.([A-Za-z_][A-Za-z0-9_]*)\s*=", src))
    injected |= set(re.findall(r"^\s+([A-Za-z_][A-Za-z0-9_]*)\s*:\s*(Any|.*?)\s*=\s*None", src, re.M))

# 1e. 不带 self 前缀的 builder 赋值其它形态: setattr(self.ctx,...)
# (headless_main 已覆盖 setattr(context,...)；builder 用 ctx.X= 已覆盖)

# ── 2. 消费名（两种形态 × 更广范围）──
# 2026-08-29 教训：cron_actions.py:90-91 用「直接属性访问」context.task_repository
# 造成主动管家长期失效，而旧版只扫 backend/handlers 且只认 getattr(context,"X")
# 形态，范围×形态双重缺口导致漏报。现扩到下列目录并同时识别两种形态。
SCAN_DIRS = ["backend", "services", "app_runtime", "assistant"]

# 形态 A：getattr(context|ctx, "X", ...)
GETATTR_RE = re.compile(r'getattr\(\s*(?:context|ctx)\s*,\s*"([A-Za-z_][A-Za-z0-9_]*)"')
# 形态 B：直接属性访问 context.X / ctx.X（排除赋值 ctx.X = ...，排除 == 比较）
DIRECT_RE = re.compile(r"\b(?:context|ctx)\.([A-Za-z_][A-Za-z0-9_]*)\s*(?!=[^=])")

consumed: dict[str, list[tuple[str, int, str]]] = {}


def _record(name: str, rel: str, line: int, form: str) -> None:
    consumed.setdefault(name, []).append((rel, line, form))


for d in SCAN_DIRS:
    base = APP / d
    if not base.is_dir():
        continue
    for f in sorted(base.rglob("*.py")):
        if "__pycache__" in str(f) or f.name.startswith("test_"):
            continue
        src = f.read_text(encoding="utf-8", errors="replace")
        rel = str(f.relative_to(APP))
        for m in GETATTR_RE.finditer(src):
            _record(m.group(1), rel, src.count("\n", 0, m.start()) + 1, "getattr")
        for m in DIRECT_RE.finditer(src):
            _record(m.group(1), rel, src.count("\n", 0, m.start()) + 1, "attr")

# ── 3. 全部声明点集合（用于注释消费而非死接线）──
all_decl = injected | {
    "event_bus",  # core.events 模块级单例，非 context 属性
}

missing = {name: locs for name, locs in sorted(consumed.items()) if name not in all_decl}
print(f"== 注入/声明集大小: {len(injected)}")
print(f"== 扫描目录: {SCAN_DIRS}")
print(f"== 消费名数（getattr + 直接属性访问）: {len(consumed)}")
print(f"== 疑似死接线（消费名不在注入集）: {len(missing)}")
for name, locs in missing.items():
    for loc in locs[:6]:
        print(f"  {name}  [{loc[2]}]  {loc[0]}:{loc[1]}")