# -*- coding: utf-8 -*-
"""适配器统一接口协议 (P3-5)。

定义 ``ExternalAdapter`` 协议与 ``AdapterStatus`` 枚举，让 ``BrowserAdapter`` /
``TodoistAdapter`` / ``YoudaoNoteAdapter`` 等外部适配器拥有共同的最小接口，
便于在 IntegrationHub / 健康聚合 / 遥测中统一处理。

设计原则：
- 使用 ``typing.Protocol`` (PEP 544) — 结构化子类型，现有适配器无需继承基类即可满足协议。
- ``runtime_checkable`` 允许 ``isinstance`` 检查（仅检查方法存在性，不检查签名）。
- 协议只要求最小公共表面（``adapter_name`` + ``health_check`` + ``is_available``），
  各适配器仍保留自身领域方法（如 ``get_page_context`` / ``list_tasks`` / ``create_note``）。
"""
from __future__ import annotations

import enum
from typing import Protocol, runtime_checkable


class AdapterStatus(enum.StrEnum):
    """适配器健康状态。"""

    ONLINE = enum.auto()
    OFFLINE = enum.auto()
    UNCONFIGURED = enum.auto()
    DEGRADED = enum.auto()


@runtime_checkable
class ExternalAdapter(Protocol):
    """外部适配器统一协议。

    所有外部适配器应至少提供：
    - ``adapter_name``: 人类可读名称（用于日志、遥测、UI 展示）
    - ``health_check()``: 返回 ``AdapterStatus``，供健康聚合器轮询
    - ``is_available()``: 简便布尔判断（等价于 ``health_check() == AdapterStatus.ONLINE``）

    各适配器在自身模块中实现这三个成员即可，无需继承本协议。
    """

    @property
    def adapter_name(self) -> str:
        """适配器人类可读名称。"""
        ...

    def health_check(self) -> AdapterStatus:
        """返回适配器当前健康状态。"""
        ...

    def is_available(self) -> bool:
        """适配器是否可用（已配置且在线）。"""
        ...


__all__ = ["AdapterStatus", "ExternalAdapter"]
