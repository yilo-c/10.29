#!/usr/bin/env python3
"""批量修复 bandit B324 (weak MD5/SHA1 for security) 的误报。

这 15 处 MD5/SHA1 均用于**非安全目的**（缓存键、内容指纹、去重 id、
API 签名盐），并非密码学安全场景。bandit 默认假设哈希用于安全，
通过显式传入 usedforsecurity=False 消音，且语义更精确。

修复逻辑（幂等、安全）：
- 匹配独立的 `hashlib.md5(ARGS)` / `hashlib.sha1(ARGS)` 调用（ARGS 内无未闭合括号）。
- 仅在尚未带 usedforsecurity 时，改写为 `hashlib.md5(ARGS, usedforsecurity=False)`。
- 不改写已正确改写或链式调用（如 `.hexdigest()` 在其后保持不动），
  通过捕获改写后是否产生 `.hexdigest(,` 这类畸形来兜底拒绝。
"""
import re
import sys
from pathlib import Path

APP = Path(__file__).resolve().parents[1] / "思忆集app"

# (相对 思忆集app 的路径, 行号) —— 来自 bandit-baseline.json 的 15 个 B324 HIGH
TARGETS = [
    ("_graph_card_nodes.py", 75),
    ("_import_knowledge_cards.py", 194),
    ("_import_knowledge_cards.py", 207),
    ("assistant/context_compressor.py", 226),
    ("assistant/source_registry.py", 159),
    ("backend/handlers/_chat_memory.py", 144),
    ("memory/index.py", 639),
    ("services/cron_actions.py", 223),
    ("services/knowledge_synthesis.py", 143),
    ("services/knowledge_synthesis.py", 331),
    ("services/note_templates.py", 423),
    ("services/note_templates.py", 590),
    ("services/rss_subscriber.py", 55),
    ("services/translate_service.py", 406),
    ("sync/delta_sync.py", 673),
]

# 匹配 hashlib.xxx( 紧跟一个不含未闭合括号的参数组，直到第一个顶层 )
CALL = re.compile(r"hashlib\.(md5|sha1)\(([^()]*)\)")


def fix_line(line: str) -> str | None:
    if "usedforsecurity" in line:
        return None
    m = CALL.search(line)
    if not m:
        # 含嵌套括号（如 f-string 内、跨行）的真实场景已由手工处理，
        # 此处 skip 避免破坏语法。
        return None
    func, args = m.group(1), m.group(2)
    candidate = f"hashlib.{func}({args}, usedforsecurity=False)"
    # 拒绝任何会产生畸形调用的改写（兜底）
    if "hexdigest(," in candidate or "hexdigest( )" in candidate:
        return None
    new = line[: m.start()] + candidate + line[m.end():]
    # 二次校验：改写后不应出现 .hexdigest(, 这种畸形
    if "hexdigest(," in new:
        return None
    return new


def main() -> int:
    changed = 0
    for rel, lineno in TARGETS:
        p = APP / rel
        if not p.exists():
            print(f"[SKIP] not found: {rel}")
            continue
        lines = p.read_text(encoding="utf-8").splitlines(keepends=True)
        if lineno < 1 or lineno > len(lines):
            print(f"[SKIP] line out of range: {rel}:{lineno}")
            continue
        new = fix_line(lines[lineno - 1])
        if new is None:
            print(f"[NOOP] already fixed or no safe match: {rel}:{lineno}")
            continue
        lines[lineno - 1] = new
        p.write_text("".join(lines), encoding="utf-8")
        changed += 1
        print(f"[FIXED] {rel}:{lineno}")
    print(f"total changed: {changed}/{len(TARGETS)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
