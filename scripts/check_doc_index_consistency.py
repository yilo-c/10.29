"""校验 docs/INDEX.md 三态标记与 docs/archive/ 的一致性（防漂移）。

规则依据 docs/INDEX.md「八、维护规则」：
1. 死链检查：INDEX.md（及 document-map.md）中所有相对 .md / 目录链接必须可解析；
   归档漂移（文档移入 archive/ 但链接未加 archive/ 前缀）会在此暴露为死链。
2. 孤儿检查：docs/ 顶层每个 .md 必须在 INDEX.md 中登记（规则 6：新文档必登记）。
3. archive 登记检查：docs/archive/ 每个 .md 必须在 INDEX.md 中提及（防静默归档）。
4. 双份检查：同一文件名不得同时存在于 docs/ 与 docs/archive/（防新旧两份漂移）。

用法：python scripts/check_doc_index_consistency.py
退出码：0 全部通过；1 有失败项（WARN 不计）。
"""
from __future__ import annotations

import os
import re
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DOCS = os.path.join(REPO, "docs")
INDEX = os.path.join(DOCS, "INDEX.md")
ARCHIVE = os.path.join(DOCS, "archive")

# 扫描死链的导航文档（INDEX 之外，历史模块地图仍承载大量链接）
DEADLINK_TARGETS = [INDEX, os.path.join(DOCS, "document-map.md")]

errors = 0


def check(condition: bool, msg: str) -> None:
    global errors
    if condition:
        print(f"[PASS] {msg}")
    else:
        print(f"[FAIL] {msg}")
        errors += 1


def warn(msg: str) -> None:
    print(f"[WARN] {msg}")


def check_relative_links(path: str) -> None:
    """检查单个文档内所有相对 markdown 链接是否可解析（相对 docs/）。"""
    base = os.path.dirname(path)
    with open(path, encoding="utf-8") as f:
        text = f.read()
    link_re = re.compile(r"\[[^\]]*\]\(([^)]+)\)")
    for m in link_re.finditer(text):
        target = m.group(1).strip()
        if target.startswith(("#", "http://", "https://", "mailto:")):
            continue
        full = os.path.normpath(os.path.join(base, target))
        exists = os.path.isfile(full) or os.path.isdir(full)
        check(exists, f"死链：{os.path.basename(path)} -> {target}")
        if not exists:
            print(f"        （解析为 {full} 不存在）")


def main() -> int:
    global errors

    # 1. 死链检查（INDEX.md + document-map.md）
    for doc in DEADLINK_TARGETS:
        check_relative_links(doc)

    # 2. 孤儿文档检查：docs/ 顶层每个 .md 必须在 INDEX.md 中登记
    with open(INDEX, encoding="utf-8") as f:
        index_text = f.read()
    top_mds = sorted(
        name for name in os.listdir(DOCS)
        if name.endswith(".md") and os.path.isfile(os.path.join(DOCS, name))
    )
    for name in top_mds:
        if name == "INDEX.md":
            continue
        registered = name in index_text or f"docs/{name}" in index_text
        check(registered, f"孤儿文档：docs/{name} 已在 INDEX.md 登记")
        if not registered:
            print("        （规则 6：任何新 docs/*.md 必须先登记一条）")

    # 3. archive 登记检查：docs/archive/ 每个 .md 必须在 INDEX.md 中提及
    if not os.path.isdir(ARCHIVE):
        warn("docs/archive/ 目录不存在")
    else:
        archived = sorted(
            name for name in os.listdir(ARCHIVE)
            if name.endswith(".md") and os.path.isfile(os.path.join(ARCHIVE, name))
        )
        for name in archived:
            registered = name in index_text
            check(registered, f"archive 已登记：docs/archive/{name} 在 INDEX.md 提及")
            if not registered:
                print("        （防静默归档：归档后须在 INDEX.md 标注）")

    # 4. 双份检查：同一文件名不得同时存在于 docs/ 与 docs/archive/
    for name in archived if os.path.isdir(ARCHIVE) else []:
        dup = os.path.isfile(os.path.join(DOCS, name))
        check(not dup, f"无双份：docs/{name} 与 docs/archive/{name} 不同时存在")
        if dup:
            print("        （同一文档新旧两份漂移，应只保留 archive/ 一份）")

    print(f"\n{'All checks passed!' if errors == 0 else f'{errors} failures'}")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
