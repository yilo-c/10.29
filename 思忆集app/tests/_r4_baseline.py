# -*- coding: utf-8 -*-
"""``_R4_METHODS`` 集中清单的「只增不减」基线（批4 治本，2026-09-18）。

## 为什么需要它

``backend/handlers/__init__.py`` 的 ``_R4_METHODS`` 是 R4（强制 sandbox 审批）
方法的**唯一真源**。四个测试各自硬编码了它的项数：

    assert len(_R4_METHODS) == 40

这条断言的真实意图是"裁决/重构不得把成员**删掉**"（删掉 = 该操作失去审批
闸门）。但写成等号后，任何**合法新增**都会让四处同时假红——本批新增
``knowledge.entity.delete``（P2-PD-NODEL，删除图谱实体，按项目规则必须 R4）
即触发 40→41 的四连假红。历史上也已发生过一次同类漂移（39→40）。

四处各改一次 = 下次再漂一次。故收敛成这里的**单点基线 + 下界断言**：

* 新增合法 R4 方法 → 不报红（更新基线是可选动作）；
* 移出成员 → 立刻报红，且报错文案直接指出"该操作失去审批闸门"。

真正的"新增删除类方法忘了进 R4"由结构级门禁覆盖：
``test_p0_6_delete_r4_gating.test_no_delete_method_unclassified``
（扫描真实 register_all 注册表，比任何计数都权威）。
"""

from __future__ import annotations

from typing import Iterable

#: 基线：本批（批4，2026-09-18）之前的项数。只允许**上调**，绝不允许下调。
R4_METHODS_MIN = 40


def assert_r4_not_shrunk(r4_methods: Iterable[str]) -> None:
    """断言 R4 集中清单未缩水（新增合法，移出即红）。"""
    count = len(list(r4_methods))
    assert count >= R4_METHODS_MIN, f"_R4_METHODS 项数缩水：{count} < 基线 {R4_METHODS_MIN}（有成员被移出 R4 → 该操作失去 sandbox 审批闸门）"
