"""AI health and model discovery JSON-RPC handlers: ping, model.list.

从原 _ai.py 拆分而来（健康检查 + 模型发现域）。
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

logger = logging.getLogger("siyi.backend.handlers.ai_health")


def register_ai_health(registry, context) -> None:
    """Register AI health and model-discovery JSON-RPC methods."""

    # ── health ──
    async def ping(**kwargs: Any) -> str:
        return "pong"

    registry.register("ping", ping)

    # ── model.list ──
    async def model_list(**kwargs: Any) -> list[dict[str, Any]]:
        """Return all available providers and their model lists.

        Tries discover_models() for each provider to get actual running
        models (e.g. Ollama /api/tags), falling back to static get_all_models()。

        性能修复：load_providers() 是同步函数（读设置 + 解密 api_key），
        用 asyncio.to_thread 包裹避免阻塞事件循环；
        discover_models() 对各 provider 并发执行（asyncio.gather），
        避免 N 个 provider 串行 脳 5s 超时 = 15s+ 累积导致 30s RPC 超时。
        """
        providers: list[dict[str, Any]] = []
        try:
            from core.provider_config import load_providers

            # load_providers 是同步函数（文件 I/O + 解密），用 to_thread 避免阻塞
            all_providers = await asyncio.to_thread(load_providers, context.settings_repository)

            # 过滤出启用的 provider
            enabled = [p for p in all_providers if p.enabled]

            # 并发发现所有 provider 的模型列表（每个最多 5s 超时，并发总耗时 ≈ max 而非 sum）
            async def _discover_safe(p):
                try:
                    if p.api_base:
                        return await p.discover_models(timeout=5)
                except Exception:
                    logger.exception("发现模型列表失败")
                    pass
                return None

            discovered_list = await asyncio.gather(
                *[_discover_safe(p) for p in enabled],
                return_exceptions=False,
            )

            for p, discovered in zip(enabled, discovered_list):
                models = discovered if discovered else p.get_all_models()
                providers.append({
                    "provider": p.name,
                    "api_base": p.api_base,
                    "models": models,
                    "is_local": p.is_local,
                    "is_fallback": p.is_fallback,
                    "enabled": p.enabled,
                })
        except Exception as exc:
            logger.warning("model.list failed: %s", exc)
        return providers

    registry.register("model.list", model_list)
