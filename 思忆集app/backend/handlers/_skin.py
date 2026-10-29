"""皮肤 RPC — skin.list / skin.active / skin.activate。

前端通过 WS JSON-RPC 调用本模块获取皮肤列表、激活皮肤，并用
既有 applyCustomTheme 把 colors 注入 CSS 变量。
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

logger = logging.getLogger("siyi.backend.handlers.skin")


def register_skin(registry, context: Any) -> None:
    """注册皮肤相关 RPC 方法。"""

    def _service():
        from services.skin_service import SkinService

        return SkinService(getattr(context, "settings_repository", None))

    async def skin_list(**kwargs: Any) -> dict[str, Any]:
        """列出所有可用皮肤。"""
        try:
            skins = await asyncio.to_thread(lambda: _service().list())
            return {"success": True, "skins": skins}
        except Exception as exc:
            logger.warning("skin.list failed: %s", exc)
            return {"success": False, "error": str(exc)}

    async def skin_active(**kwargs: Any) -> dict[str, Any]:
        """返回当前激活皮肤（未激活返回 skin=None）。"""
        try:
            active = await asyncio.to_thread(lambda: _service().active())
            return {"success": True, "skin": active}
        except Exception as exc:
            logger.warning("skin.active failed: %s", exc)
            return {"success": False, "error": str(exc)}

    async def skin_activate(skin_id: str = "", **kwargs: Any) -> dict[str, Any]:
        """激活指定皮肤并持久化。"""
        ok, error = await asyncio.to_thread(lambda: _service().activate(skin_id))
        if not ok:
            return {"success": False, "error": error}
        active = await asyncio.to_thread(lambda: _service().active())
        return {"success": True, "skin": active}

    registry.register("skin.list", skin_list)
    registry.register("skin.active", skin_active)
    registry.register("skin.activate", skin_activate)
