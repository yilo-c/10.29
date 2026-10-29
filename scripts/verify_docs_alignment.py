"""Verify docs alignment with actual code.

Checks:
1. Handler module count in CLAUDE.md matches actual files
2. File references in docs exist on disk
"""
from __future__ import annotations

import os
import re
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
errors = 0


def check(condition: bool, msg: str) -> None:
    global errors
    if not condition:
        print(f"[FAIL] {msg}")
        errors += 1
    else:
        print(f"[PASS] {msg}")


# 1. Handler count
handler_dir = os.path.join(REPO, "思忆集app", "backend", "handlers")
actual_handlers = sum(1 for f in os.listdir(handler_dir) if f.startswith("_") and f.endswith(".py") and f != "__init__.py")
claude_md = os.path.join(REPO, "CLAUDE.md")
with open(claude_md, encoding="utf-8") as f:
    content = f.read()

# Count occurrences of handler number pattern
handler_pattern = r"(\d+)\s*个领域方法处理器模块"
handlers_in_docs = re.findall(handler_pattern, content)
for h in handlers_in_docs:
    check(int(h) == actual_handlers, f"Handler count: doc says {h}, actual {actual_handlers}")

# 2. Check file references in code-architecture-mapping.md
mapping = os.path.join(REPO, "docs", "code-architecture-mapping.md")
if os.path.isfile(mapping):
    with open(mapping, encoding="utf-8") as f:
        for line in f:
            m = re.findall(r'`([^`]+)`', line)
            for ref in m:
                # Check if it looks like a file path
                if ref.endswith(".py") or ref.endswith(".md") or ref.endswith(".ts"):
                    if " " not in ref:
                        full = os.path.join(REPO, ref.lstrip("/"))
                        if not os.path.isfile(full):
                            print(f"[WARN] doc references deleted file: {ref}")

print(f"\n{'All checks passed!' if errors == 0 else f'{errors} failures'}")
sys.exit(errors)
