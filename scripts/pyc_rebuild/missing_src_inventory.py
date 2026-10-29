# -*- coding: utf-8 -*-
"""统计思忆集app 下源码缺失但被存活源码引用的模块（精确版）。"""
from pathlib import Path
import re

root = Path(__file__).resolve().parents[1] / "思忆集app"

missing: dict[str, Path] = {}
for p in root.rglob("*.pyc"):
    if "__pycache__" in p.parts:
        continue
    if not p.with_suffix(".py").exists():
        missing[".".join(p.relative_to(root).with_suffix("").parts)] = p.parent

src_files = [f for f in root.rglob("*.py") if "__pycache__" not in f.parts]
texts = {f: f.read_text(encoding="utf-8", errors="ignore") for f in src_files}

referenced: dict[str, str] = {}
for mod, pkgdir in sorted(missing.items()):
    short = mod.rsplit(".", 1)[-1]
    pats = [
        re.compile(r"(?:from|import)\s+" + re.escape(mod) + r"\b"),
        re.compile(r"from\s+\.\s+import\b[^\n]*\b" + re.escape(short) + r"\b"),
        re.compile(r"from\s+\." + re.escape(short) + r"\s+import"),
    ]
    for f, t in texts.items():
        same_pkg = f.parent == pkgdir
        if pats[0].search(t) or (same_pkg and (pats[1].search(t) or pats[2].search(t))):
            referenced[mod] = str(f.relative_to(root))
            break

print(f"源码缺失模块总数: {len(missing)}")
print(f"其中被存活源码引用（高危）: {len(referenced)}")
for mod, ref in referenced.items():
    print(f"  {mod}  <-  {ref}")
