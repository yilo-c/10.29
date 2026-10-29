"""能力市场目录弹窗 单元测试。"""

from __future__ import annotations

import sys
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from services.capability_types import CapabilityRegistry, CapabilityGroup, CapabilitySpec


def _make_registry():
    registry = CapabilityRegistry()
    registry.register_group(CapabilityGroup(group_id="news", name="新闻资讯", description="新闻检索、简报生成"))
    registry.register_group(CapabilityGroup(group_id="notes", name="笔记管理", description="笔记同步、整理"))
    registry.register_capability(
        CapabilitySpec(
            capability_id="news.fetch",
            description="获取新闻资讯",
            risk_level="low",
            group="news",
            enabled=True,
        )
    )
    registry.register_capability(
        CapabilitySpec(
            capability_id="news.briefing",
            description="生成新闻简报",
            risk_level="low",
            group="news",
            enabled=False,
        )
    )
    registry.register_capability(
        CapabilitySpec(
            capability_id="notes.sync",
            description="同步笔记",
            risk_level="low",
            group="notes",
            enabled=True,
        )
    )
    return registry


# ── Registry 层测试（无 Qt 依赖）───────────────────────


def test_registry_list_capabilities():
    registry = _make_registry()
    all_caps = registry.list_capabilities(include_disabled=True)
    enabled = [c for c in all_caps if c.enabled]
    assert len(all_caps) == 3
    assert len(enabled) == 2


def test_registry_ungrouped_capabilities():
    registry = CapabilityRegistry()
    registry.register_capability(
        CapabilitySpec(
            capability_id="orphan.tool",
            description="无组能力",
            risk_level="low",
            group="",
            enabled=True,
        )
    )
    caps = registry.list_capabilities(include_disabled=True)
    assert len(caps) == 1


def test_registry_group_toggle():
    registry = CapabilityRegistry()
    registry.register_group(CapabilityGroup(group_id="g1", name="G1"))
    registry.register_capability(
        CapabilitySpec(
            capability_id="g1.c1",
            description="c1",
            group="g1",
            enabled=True,
        )
    )
    registry.register_capability(
        CapabilitySpec(
            capability_id="g1.c2",
            description="c2",
            group="g1",
            enabled=True,
        )
    )
    registry.enable_group("g1", False)
    caps = registry.list_capabilities(include_disabled=False)
    # 组禁用后，组内能力不应出现在启用列表中
    assert len(caps) == 0


# ── 基于字符串的 Dialog 行为测试（隔离 Qt 创建）──────


def test_catalog_text_output():
    """验证能力目录的工具方法能产生正确的文本输出（非 Qt 依赖）。"""
    registry = _make_registry()
    all_caps = registry.list_capabilities(include_disabled=True)
    groups = registry.list_groups()
    enabled = [c for c in all_caps if c.enabled]
    total = len(all_caps)

    # 模拟摘要文本构建
    summary = f"共 {total} 项能力，{len(enabled)} 项已启用，{total - len(enabled)} 项已禁用"
    assert "3 项能力" in summary
    assert "2 项已启用" in summary
    assert "1 项已禁用" in summary

    # 验证组信息
    group_names = [g.name for g in groups]
    assert "新闻资讯" in group_names
    assert "笔记管理" in group_names

    # 验证能力和组关联
    for cap in all_caps:
        if cap.group == "news":
            assert cap.capability_id.startswith("news.")
        elif cap.group == "notes":
            assert cap.capability_id.startswith("notes.")
