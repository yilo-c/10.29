# -*- coding: utf-8 -*-
"""modules.status JSON-RPC handler — U4 前端隐藏机制的数据源。

契约（与前端批约定，不得偏离）::

    modules.status {} → {
        "success": true,
        "modules": [
            {"module": "reminder", "configured": bool, "hasData": bool},
            {"module": "reading", "configured": bool, "hasData": bool},
            {"module": "external_content", "configured": bool, "hasData": bool},
        ],
    }

语义：
- configured = 该能力是否需要外部凭据且已配置。
  * reminder / reading 为本地能力，无外部凭据依赖 → 恒 True（内部判定失败也保底 True）。
  * external_content 需要 Readwise token（或 Kindle/浏览器书签导入目录任一）→ 有则 True。
- hasData = 该模块是否有真实数据（任务含 reminder_time 计数 / 阅读进度文件计数 /
  saved_articles 计数）。
- 前端隐藏判定式：configured=false 才隐藏 ⇒ 本端保证 **hasData=true 时 configured
  宽松为 true**（有数据说明能用）。
- 铁律：任何子判定抛异常 → 该模块 configured=True 保底（绝不因探测失败误隐藏），
  logger.warning 留痕，整体不崩。
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Callable

logger = logging.getLogger("siyi.backend.handlers.modules_status")

# 契约模块名（前端按此名单隐藏，不得改名）
MODULE_NAMES = ("reminder", "reading", "external_content")


def _field(item: Any, name: str) -> Any:
    """兼容 dict / dataclass 式对象的字段读取（与 _reminder.py 同构）。"""
    if isinstance(item, dict):
        return item.get(name)
    return getattr(item, name, None)


def _probe_reminder(context: Any) -> tuple[bool, bool]:
    """reminder：本地能力不依赖外部凭据 → configured 恒 True；
    hasData = 任务列表中存在带 reminder_time 的条目。"""
    repo = getattr(context, "personal_task_repository", None)
    if repo is None:
        # 仓储未注入：无法判定数据，configured 保底 True，hasData False
        return True, False
    all_tasks = repo.list_tasks()
    has_data = any(_field(t, "reminder_time") for t in all_tasks or [])
    return True, bool(has_data)


def _probe_reading(context: Any) -> tuple[bool, bool]:
    """reading：阅读进度为本地 JSON（reading_positions.json），无凭据依赖 →
    configured 恒 True；hasData = 进度文件条目数 > 0。"""
    from backend.handlers import _reading as _reading_mod

    path = _reading_mod._get_store_path(context)
    positions = _reading_mod._load_positions(path)
    # 存储形状兼容：直接 {note_id: pos} 或 {"positions": {...}} 均按条目数判定
    if isinstance(positions, dict) and isinstance(positions.get("positions"), dict):
        entries = positions["positions"]
    else:
        entries = positions
    return True, bool(entries)


def _probe_external_content(context: Any) -> tuple[bool, bool]:
    """external_content：需要外部凭据（Readwise token）或本地导入目录
    （Kindle / 浏览器书签）任一即视为已配置；hasData = saved_articles.json
    中文章数 > 0（token 密文不解密，非空即已配置，探测代价最小）。"""
    from backend.handlers import _external_content as _ec_mod
    from core.settings_keys import (
        KEY_BROWSER_BOOKMARKS_IMPORT_DIR,
        KEY_KINDLE_IMPORT_DIR,
        KEY_READWISE_TOKEN,
    )

    repo = getattr(context, "settings_repository", None)
    configured = False
    if repo is not None:
        readwise = str(repo.get_setting(KEY_READWISE_TOKEN, "") or "")
        kindle_dir = str(repo.get_setting(KEY_KINDLE_IMPORT_DIR, "") or "")
        bookmark_dir = str(repo.get_setting(KEY_BROWSER_BOOKMARKS_IMPORT_DIR, "") or "")
        configured = bool(readwise or kindle_dir or bookmark_dir)
    articles = _ec_mod._load_articles(_ec_mod._get_articles_path(context))
    has_data = bool(articles.get("articles"))
    return configured, has_data


_PROBES: dict[str, Callable[[Any], tuple[bool, bool]]] = {
    "reminder": _probe_reminder,
    "reading": _probe_reading,
    "external_content": _probe_external_content,
}


def _probe_one(module: str, context: Any) -> dict[str, Any]:
    """单模块探测 + 异常保底：任何失败 → configured=True（不误隐藏），不抛出。"""
    probe = _PROBES.get(module)
    if probe is None:
        # 查不到实体/探测器缺位：契约要求的保底形状（configured True）
        return {"module": module, "configured": True, "hasData": False}
    try:
        configured, has_data = probe(context)
    except Exception as exc:
        logger.warning("modules.status probe failed for %s: %s", module, exc, exc_info=True)
        return {"module": module, "configured": True, "hasData": False}
    # 契约：hasData=True 的模块 configured 宽松为 True（有数据说明能用）
    return {"module": module, "configured": bool(configured) or bool(has_data), "hasData": bool(has_data)}


def register_modules_status(registry, context) -> None:
    """注册 modules.status JSON-RPC 方法。"""

    async def modules_status(**kwargs: Any) -> dict[str, Any]:
        # 探测涉及文件 I/O（to_thread 释放事件循环）；逐模块并发 gather，
        # 单模块异常已在 _probe_one 内兜底，不会传染。
        results = await asyncio.gather(*(asyncio.to_thread(_probe_one, module, context) for module in MODULE_NAMES))
        return {"success": True, "modules": list(results)}

    registry.register_with_schema(
        "modules.status",
        modules_status,
        schema={"properties": {}, "type": "object"},
    )

    logger.info("modules.status handler registered (U4 模块状态探针: reminder/reading/external_content)")
