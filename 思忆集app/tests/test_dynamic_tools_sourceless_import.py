# -*- coding: utf-8 -*-
"""A-8 回归测试：_dynamic_tools 必须解析到迁移后的 services.tooling。

背景（2026-08-24）：assistant/capability/_dynamic_tools.py 原以相对路径
`from ..generic_tool_bridge / from ..tool_spec` 导入，而 assistant/ 下这两个模块
只剩 2026-07-18 事故遗留的**无源码 .pyc**，导致生产路径加载陈旧字节码
（同进程存在两份 ToolSpecRegistry，行为可能不一致）。
修复：改用 services/tooling/（kernel-contract §5.4.1 迁移版）并删除遗留 .pyc。

本测试钉死"必须来自迁移版"：若未来有人把导入切回 assistant/ 旧路径
（该路径只会有 .pyc 或直接缺失），这里立即红。
"""

from assistant.capability._dynamic_tools import DynamicToolDomain, GenericToolBridge, ToolSpecRegistry


def test_dynamic_tools_resolves_to_migrated_tooling():
    """GenericToolBridge / ToolSpecRegistry 必须来自 services.tooling 迁移版。"""
    assert GenericToolBridge.__module__ == "services.tooling.generic_tool_bridge"
    assert ToolSpecRegistry.__module__ == "services.tooling.tool_spec"


def test_dynamic_tools_assembly_smoke():
    """DynamicToolDomain 可装配：no-arg 注册表可用，桥接器构造不崩。"""
    registry = ToolSpecRegistry()  # config_path=None -> 空注册表，合法
    assert registry.get_all_action_ids() == []
    domain = DynamicToolDomain(router=None)
    assert domain is not None
    # 无数据依赖的桥接构造：不传 registry 时也应能建（working_dir 为 None）
    bridge = GenericToolBridge(registry)
    assert bridge is not None
