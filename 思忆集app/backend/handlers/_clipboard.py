# -*- coding: utf-8 -*-
"""剪贴板上下文 RPC 处理器（第二档 #4：复制即候选上下文）。

暴露 `clipboard.context` / `clipboard.latest` 供前端划词 / 悬浮在用户复制后
自动拉取候选上下文并注入聊天（比现在「弹卡片等点击」进一格）。同时在注册时
拉起后台轮询线程，使复制行为实时沉淀为上下文。
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("siyi.backend.handlers.clipboard")


def register_clipboard(registry, context) -> None:
    """注册剪贴板上下文相关 RPC，并启动后台轮询。"""
    from services.clipboard_context import store, start_background_polling

    async def clipboard_context(limit: int = 5, **kwargs: Any) -> dict[str, Any]:
        """返回最近复制的片段（候选上下文），前端可自动注入。"""
        items = store.recent(int(limit or 5))
        return {"success": True, "items": items, "count": len(items)}

    registry.register("clipboard.context", clipboard_context)

    async def clipboard_latest(**kwargs: Any) -> dict[str, Any]:
        """返回最近一次复制的片段。"""
        item = store.latest()
        return {"success": True, "item": item}

    registry.register("clipboard.latest", clipboard_latest)

    # 启动后台轮询：复制即沉淀为候选上下文（失败降级，不阻断启动）
    try:
        started = start_background_polling(interval=3.0)
        if started:
            logger.info("剪贴板上下文轮询已随 RPC 注册启动")
        else:
            logger.info("剪贴板上下文轮询未启动（环境不支持，RPC 仍可用但内容为空）")
    except Exception as exc:  # noqa: BLE001
        logger.warning("剪贴板轮询启动失败（功能降级）: %s", exc)
