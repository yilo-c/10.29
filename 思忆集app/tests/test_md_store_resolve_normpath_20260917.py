# -*- coding: utf-8 -*-
"""N-C 回归：md_store.resolve 的 root normpath 修复（2026-09-17 夜间批）。

缺陷：__init__ 里 root = os.path.join(base_dir, KNOWLEDGE_ROOT) 未 normpath。
当 base_dir 以正斜杠传入（SIYI_DATA_DIR / 跨平台拼接）时，resolve() 中
normpath 后的 target 被规范为系统分隔符（Windows 反斜杠），而前缀比较仍用
`self.root + os.sep`（正斜杠形态）→ 合法路径恒不匹配 → 误判「MD 路径越界」。

修复语义：root 与 target 在比较前统一 normpath，正斜杠不误判，且 `..` 越界
拦截与绝对路径归一行为全部不破。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from knowledge.md_store import MDKnowledgeStore


def _slash_base(tmp_path: Path) -> str:
    """构造正斜杠形态的 base_dir（复现缺陷触发条件）。"""
    return str(tmp_path).replace(os.sep, "/") + "/data"


def test_forward_slash_base_dir_resolves_without_false_positive(tmp_path):
    """核心回归：正斜杠 base_dir 下的合法相对路径不得被误判越界。"""
    md = MDKnowledgeStore(_slash_base(tmp_path))
    # 修复前：以下每一句在 Windows 上都抛 ValueError
    for rel in (
        "2026/2026-09/2026-09-17_记忆.md",
        "主题/测试主题/卡片-demo.md",
        "00_索引/索引.md",
    ):
        got = md.resolve(rel)
        assert os.path.isabs(got)
        assert got == os.path.normpath(os.path.join(md.root, rel.replace("\\", "/")))


def test_root_is_normalized(tmp_path):
    """root 本身应被 normpath（分隔符统一），使前缀比较两侧同形。"""
    md = MDKnowledgeStore(_slash_base(tmp_path))
    assert md.root == os.path.normpath(md.root)
    assert "/" not in md.root  # Windows 下规范后不含正斜杠


def test_write_read_roundtrip_under_slash_base(tmp_path):
    """正斜杠 base_dir 下读写往返必须可用（曾被误判彻底堵死）。"""
    md = MDKnowledgeStore(_slash_base(tmp_path))
    md.write("t/x.md", "hello")
    assert md.exists("t/x.md")
    assert md.read("t/x.md") == "hello"


@pytest.mark.parametrize(
    "evil",
    [
        "../outside.md",
        "../../etc/passwd",
        "a/../../b.md",
        "..\\outside.md",
        "主题/../../evil.md",
    ],
)
def test_traversal_still_blocked(tmp_path, evil):
    """越界拦截不得因 normpath 修复而放宽（回归护栏）。"""
    md = MDKnowledgeStore(_slash_base(tmp_path))
    with pytest.raises(ValueError):
        md.resolve(evil)


def test_prefix_lookalike_dir_blocked(tmp_path):
    """兄弟目录仿冒前缀（knowledge_evil）不得被判为根内。"""
    base = _slash_base(tmp_path)
    md = MDKnowledgeStore(base)
    # 手工造一个 knowledge 的仿冒兄弟目录
    sibling = os.path.normpath(md.root + "_evil")
    os.makedirs(sibling, exist_ok=True)
    with pytest.raises(ValueError):
        md.resolve("../knowledge_evil/x.md")


def test_backslash_base_dir_still_works(tmp_path):
    """反斜杠 base_dir 路径不受修复影响（原行为保持）。"""
    base = str(tmp_path) + os.sep + "data"
    md = MDKnowledgeStore(base)
    assert md.root == os.path.normpath(os.path.join(base, "knowledge"))
    assert md.resolve("a/b.md").startswith(md.root)
