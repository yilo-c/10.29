# -*- coding: utf-8 -*-
"""AI 平台账户聊天同步 RPC 方法。

ai_chat.sync_deepseek — 抓取 DeepSeek 账户聊天并导入知识体系
ai_chat.set_token    — 配置/更新 DeepSeek 账户 token
ai_chat.clear_token  — 清除 DeepSeek 账户 token
ai_chat.status       — 查看已配置的 AI 平台账户状态
ai_chat.list_actions — 列出可用的 AI 聊天同步 cron 动作（供新建任务 UI 使用）
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

logger = logging.getLogger("siyi.backend.handlers.ai_chat")


def register_ai_chat(registry, context) -> None:
    from services.ai_chat_sync import (
        get_account_token,
        list_configured_accounts,
        refresh_deepseek,
        set_account_token,
    )

    async def ai_chat_sync_deepseek(
        token: str = "",
        max_sessions: int = 30,
        since_hours: float = 0,
        persist_token: bool = False,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """抓取 DeepSeek 账户聊天并导入思忆集知识体系。"""
        try:
            result = await asyncio.to_thread(
                refresh_deepseek,
                context,
                token=token,
                max_sessions=int(max_sessions or 30),
                since_hours=float(since_hours or 0),
                persist_token=bool(persist_token),
            )
            return result
        except Exception as exc:  # noqa: BLE001
            logger.warning("ai_chat.sync_deepseek failed: %s", exc)
            return {"success": False, "error": str(exc)}

    async def ai_chat_set_token(token: str = "", **kwargs: Any) -> dict[str, Any]:
        token = str(token or "").strip()
        if not token:
            return {"success": False, "error": "token required"}
        try:
            saved = await asyncio.to_thread(set_account_token, context, "deepseek", token)
            if not saved:
                return {"success": False, "error": "本地存储不可用，无法保存 token"}
            from integrations.deepseek_client import DeepSeekAccountClient

            client = DeepSeekAccountClient(token)
            return {
                "success": True,
                "platform": "deepseek",
                "token_expires_at": client.token_expires_at,
            }
        except Exception as exc:  # noqa: BLE001
            logger.warning("ai_chat.set_token failed: %s", exc)
            return {"success": False, "error": str(exc)}

    async def ai_chat_clear_token(**kwargs: Any) -> dict[str, Any]:
        try:
            cleared = await asyncio.to_thread(set_account_token, context, "deepseek", "")
            return {"success": cleared, "platform": "deepseek"}
        except Exception as exc:  # noqa: BLE001
            logger.warning("ai_chat.clear_token failed: %s", exc)
            return {"success": False, "error": str(exc)}

    async def ai_chat_status(**kwargs: Any) -> dict[str, Any]:
        try:
            accounts = await asyncio.to_thread(list_configured_accounts, context)
            detail = {}
            for account in accounts:
                if account["platform"] == "deepseek":
                    from integrations.deepseek_client import DeepSeekAccountClient

                    token = get_account_token(context, "deepseek")
                    client = DeepSeekAccountClient(token)
                    detail["deepseek"] = {"configured": True, "token_expires_at": client.token_expires_at}
            return {"success": True, "accounts": accounts, "detail": detail}
        except Exception as exc:  # noqa: BLE001
            logger.warning("ai_chat.status failed: %s", exc)
            return {"success": False, "error": str(exc)}

    async def ai_chat_list_actions(**kwargs: Any) -> dict[str, Any]:
        from services.cron_actions import list_cron_actions

        actions = list_cron_actions()
        return {"success": True, "actions": actions}

    registry.register("ai_chat.sync_deepseek", ai_chat_sync_deepseek)
    # 该链路含 LLM 图谱抽取，多会话时耗时长，放宽到 600s（与 cron executor 一致）
    registry.set_timeout("ai_chat.sync_deepseek", 600)
    registry.register("ai_chat.set_token", ai_chat_set_token)
    registry.register("ai_chat.clear_token", ai_chat_clear_token)
    registry.register("ai_chat.status", ai_chat_status)
    registry.register("ai_chat.list_actions", ai_chat_list_actions)
    logger.info("AI chat RPC methods registered")
