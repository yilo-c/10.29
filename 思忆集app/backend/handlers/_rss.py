# -*- coding: utf-8 -*-
"""rss.* RPC handlers — RSS/Atom feed 订阅管理（C3）。

提供 RSS/Atom feed 的订阅/退订/列表/抓取/测试能力。与 InfoCollectorService
通过共享的 ``context.rss_subscriber`` 实例联动：RPC 层的订阅变更会即时反映到
下一次 collect() 调用（订阅的 feed 会被抓取并合并进资讯流水线）。

注册的方法：
  - ``rss.subscribe`` — 订阅 feed（校验可达性 + 解析 feed 标题）
  - ``rss.unsubscribe`` — 退订
  - ``rss.list`` — 列出所有订阅
  - ``rss.fetch_now`` — 立即抓取（单个 feed 或全部）
  - ``rss.test`` — 测试 URL 是否为合法 feed（不订阅）
"""

from __future__ import annotations

import logging
from typing import Any

from services.rss_subscriber import RSSSubscriber

logger = logging.getLogger("siyi.backend.handlers.rss")


async def register(registry, context: Any) -> None:
    """注册 rss.* JSON-RPC 方法。"""
    settings_repo = getattr(context, "settings_repository", None)

    def _get_subscriber() -> "RSSSubscriber":
        """优先复用 context 上共享的 subscriber 实例（与 collector 共用同一份状态）。

        无共享实例时退化为临时实例（仍读写同一 settings key，但内存状态不共享）。
        """
        shared = getattr(context, "rss_subscriber", None)
        if isinstance(shared, RSSSubscriber):
            return shared
        return RSSSubscriber(settings_repository=settings_repo)

    async def rss_subscribe(
        url: str = "",
        name: str = "",
        category_hint: str = "",
        **kwargs: Any,
    ) -> dict[str, Any]:
        """订阅一个 RSS/Atom feed。

        会先抓取并校验 URL 返回的是合法 RSS/Atom feed，并解析出 feed 标题。
        未提供 name 时使用 feed 标题作为订阅名。

        Args:
            url: feed URL（http/https）
            name: 订阅名（可选，留空则用 feed 标题）
            category_hint: 分类提示（如 ai/dev/tool）
        """
        if not url:
            return {"success": False, "error": "缺少 url 参数"}
        sub = _get_subscriber()
        result = await sub.subscribe(url, name=name, category_hint=category_hint)
        return result

    async def rss_unsubscribe(feed_id: str = "", **kwargs: Any) -> dict[str, Any]:
        """退订指定 feed。

        Args:
            feed_id: 订阅 id（由 rss.list / rss.subscribe 返回）
        """
        if not feed_id:
            return {"success": False, "error": "缺少 feed_id 参数"}
        sub = _get_subscriber()
        ok = sub.unsubscribe(feed_id)
        if not ok:
            return {"success": False, "error": f"订阅 {feed_id} 不存在"}
        return {"success": True, "feed_id": feed_id}

    async def rss_list(**kwargs: Any) -> dict[str, Any]:
        """列出所有 RSS 订阅。

        D5（2026-09-17）：返回键统一为 ``feeds``。此前返回 ``subscriptions``，
        而全仓唯一消费方（桌面端 InfoModule.tsx:126）读的是 ``feeds``，导致
        订阅列表恒空。已裁决单键直改，不做双键过渡。
        """
        sub = _get_subscriber()
        feeds = sub.list_subscriptions()
        return {"feeds": feeds, "total": len(feeds)}

    async def rss_fetch_now(feed_id: str = "", **kwargs: Any) -> dict[str, Any]:
        """立即抓取订阅的 feed。

        Args:
            feed_id: 指定单个订阅 id 抓取；留空则抓取全部订阅。
        """
        sub = _get_subscriber()
        if feed_id:
            return await sub.fetch_one(feed_id)
        results = await sub.fetch_all()
        summary = []
        for entry in sub.list_subscriptions():
            url = entry.get("url", "")
            summary.append(
                {
                    "id": entry.get("id", ""),
                    "url": url,
                    "name": entry.get("name", ""),
                    "item_count": len(results.get(url, [])),
                    "last_error": entry.get("last_error", ""),
                    "last_fetched_at": entry.get("last_fetched_at", ""),
                }
            )
        total_items = sum(len(v) for v in results.values())
        return {
            "success": True,
            "total_feeds": len(results),
            "total_items": total_items,
            "feeds": summary,
            "items_by_url": results,
        }

    async def rss_test(url: str = "", **kwargs: Any) -> dict[str, Any]:
        """测试 URL 是否为合法 RSS/Atom feed（不订阅）。

        Args:
            url: 待测试的 feed URL
        """
        if not url:
            return {"success": False, "error": "缺少 url 参数"}
        sub = _get_subscriber()
        try:
            items = await sub.fetch_feed(url)
        except Exception as exc:
            logger.exception("测试RSS订阅源失败")
            return {
                "success": False,
                "error": str(exc),
                "is_feed": False,
            }
        return {
            "success": True,
            "is_feed": True,
            "item_count": len(items),
            "sample": items[None:3],
        }

    registry.register("rss.subscribe", rss_subscribe)
    registry.register("rss.unsubscribe", rss_unsubscribe)
    registry.register("rss.list", rss_list)
    registry.register("rss.fetch_now", rss_fetch_now)
    registry.register("rss.test", rss_test)
