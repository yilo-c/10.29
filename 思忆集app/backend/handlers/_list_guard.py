"""帧守卫：集合类 RPC 响应的无界增长保护（2026-10-15 U1 大响应治理批次）。

audit.list（712KB）/ knowledge.search（323KB）已单独治理（bd2776d5）；
本模块给其余全表/全文件加载的集合方法统一兜底：
  - 当前数据量（<< cap）下**零行为变化**；
  - 超过 cap 截断 + warning 告警 + （响应为 dict 时）truncated 标记；
  - 防止大表增长后单帧响应打爆 ~1MB WS 帧上限。
返回裸 list 的历史契约不改成 dict（前端兼容优先），只静默截断 + 日志留痕。
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("siyi.handlers.list_guard")

DEFAULT_LIST_CAP = 2000


def cap_list(items: list[Any], *, method: str, cap: int = DEFAULT_LIST_CAP) -> list[Any]:
    """超出 cap 截断并告警；未超限原样返回（同一对象）。"""
    if len(items) <= cap:
        return items
    logger.warning(
        "%s: %d items exceed cap %d, truncated (frame guard)",
        method,
        len(items),
        cap,
    )
    return items[:cap]


def capped_payload(
    items: list[Any],
    *,
    method: str,
    cap: int = DEFAULT_LIST_CAP,
) -> tuple[list[Any], bool]:
    """dict 形态响应用：返回 (截断后列表, 是否发生截断)。"""
    if len(items) <= cap:
        return items, False
    logger.warning(
        "%s: %d items exceed cap %d, truncated (frame guard)",
        method,
        len(items),
        cap,
    )
    return items[:cap], True


def clamp_limit(limit: Any, *, default: int, cap: int = DEFAULT_LIST_CAP) -> int:
    """归一化 limit：非法 / <=0 → default；> cap → cap。

    2026-10-15 帧守卫续批：前端全量核实**从不传 limit<=0**（桌面/手机均无
    limit:0 / limit:-1 调用方），故 0/负值从"穿透为全量"收紧为"回退默认"——
    堵住 notes.list / tasks.list / chat.history / translations.list 等的
    全量穿透洞（SQL LIMIT 负值在 SQLite 语义下也是"无上限"）；上限 clamp
    防显式大值打爆单帧。
    """
    try:
        n = int(limit)
    except (TypeError, ValueError):
        return default
    if n <= 0:
        return default
    return min(n, cap)
