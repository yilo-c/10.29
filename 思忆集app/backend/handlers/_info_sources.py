# -*- coding: utf-8 -*-
"""info.sources.* RPC handlers — 资讯源配置管理（C1）。

提供资讯源的查询、启用/禁用、添加自定义源、删除自定义源、重置为默认等能力。
与 InfoCollectorService 通过共享的 ``context.info_source_registry`` 实例联动：
RPC 层的变更会即时反映到下一次 collect() 调用。
"""
from __future__ import annotations

import logging
from dataclasses import asdict
from typing import Any

from services.info_source_registry import InfoSourceRegistry

logger = logging.getLogger("siyi.backend.handlers.info_sources")


async def register(registry, context: Any) -> None:
    """注册 info.sources.* JSON-RPC 方法。"""
    settings_repo = getattr(context, "settings_repository", None)

    def _get_registry() -> InfoSourceRegistry:
        """优先复用 context 上共享的 registry 实例（与 collector 共用同一份状态）。

        无共享实例时退化为临时实例（仍读写同一 settings key，但内存状态不共享）。
        """
        shared = getattr(context, "info_source_registry", None)
        if isinstance(shared, InfoSourceRegistry):
            return shared
        return InfoSourceRegistry(settings_repository=settings_repo)

    async def info_sources_list(**kwargs: Any) -> dict[str, Any]:
        """返回所有已配置的资讯源（默认 + 自定义，含禁用项）。"""
        reg = _get_registry()
        sources = [asdict(s) for s in reg.list_sources()]
        return {"sources": sources, "total": len(sources)}

    async def info_sources_toggle(
        name: str = "", enabled: bool = True, **kwargs: Any
    ) -> dict[str, Any]:
        """启用/禁用指定资讯源。

        Args:
            name: 源名（如 ``hackernews``）
            enabled: True 启用 / False 禁用
        """
        if not name:
            return {"success": False, "error": "缺少 name 参数"}
        reg = _get_registry()
        ok = reg.set_enabled(name, bool(enabled))
        if not ok:
            return {"success": False, "error": f"源 '{name}' 不存在"}
        return {"success": True, "name": name, "enabled": bool(enabled)}

    async def info_sources_add(
        name: str = "",
        url: str = "",
        parser_type: str = "rss",
        language: str = "en",
        category_hint: str = "",
        display_name: str = "",
        **kwargs: Any,
    ) -> dict[str, Any]:
        """添加用户自定义资讯源（仅 rss/xml 类型会被 collect() 抓取）。

        Args:
            name: 源唯一标识（不可与内置源重名）
            url: feed URL
            parser_type: json/xml/rss/html（推荐 rss）
            language: en/zh
            category_hint: 分类提示（如 ai/dev/tool）
            display_name: 前端展示名（可选，默认与 name 相同）
        """
        if not name or not url:
            return {"success": False, "error": "name 和 url 为必填参数"}
        reg = _get_registry()
        src = reg.add_custom_source(
            name,
            url,
            parser_type,
            language,
            category_hint,
            display_name,
        )
        if src is None:
            return {"success": False, "error": f"源 '{name}' 已存在或参数无效"}
        return {"success": True, "source": asdict(src)}

    async def info_sources_remove(name: str = "", **kwargs: Any) -> dict[str, Any]:
        """删除用户自定义资讯源（内置源不可删除，可禁用）。"""
        if not name:
            return {"success": False, "error": "缺少 name 参数"}
        reg = _get_registry()
        ok = reg.remove_custom_source(name)
        if not ok:
            return {
                "success": False,
                "error": f"源 '{name}' 不存在或为内置源（不可删除，请改用禁用）",
            }
        return {"success": True, "name": name}

    async def info_sources_reset(**kwargs: Any) -> dict[str, Any]:
        """重置为默认源配置：移除所有自定义源，重新启用全部默认源。"""
        reg = _get_registry()
        reg.reset_to_defaults()
        return {
            "success": True,
            "sources": [asdict(s) for s in reg.list_sources()],
        }

    registry.register("info.sources.list", info_sources_list)
    registry.register("info.sources.toggle", info_sources_toggle)
    registry.register("info.sources.add", info_sources_add)
    registry.register("info.sources.remove", info_sources_remove)
    registry.register("info.sources.reset", info_sources_reset)
