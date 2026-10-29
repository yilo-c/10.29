"""RSS/Atom 订阅服务 — C3。

提供独立的 RSS/Atom feed 订阅管理能力，与 ``InfoSourceRegistry``（C1，管理内置源
开关与自定义源）互补：

  - C1 的 ``InfoSourceRegistry`` 管理的是 *参与 collect() 调度* 的源（含内置 20 个 +
    用户自定义 RSS 源），其自定义源会被 ``InfoCollectorService._fetch_rss`` 抓取。
  - C3 的 ``RSSSubscriber`` 面向 *独立的 feed 订阅* 场景：用户可订阅任意 RSS/Atom
    feed，订阅时会校验 URL 可达性与 feed 格式合法性，并解析出 feed 标题。订阅项
    独立持久化（``rss_subscriptions`` key），同时也会被 ``collect()`` 拉取并合并进
    资讯流水线（标记 ``source="rss:{feed_name}"``）。

设计要点：
  - 抓取使用 ``aiohttp``，解析使用 ``feedparser``（兼容 RSS 2.0 / Atom 1.0）。
  - 每个 feed 超时 15s；每个 feed 最多保留 50 条。
  - 订阅持久化到 ``settings_repository`` 的 ``rss_subscriptions`` key，结构为
    ``{"subscriptions": [entry, ...]}``，entry 含 id/url/name/category_hint/
    last_fetched_at/last_error/item_count/feed_title。
  - 无 ``settings_repository`` 时退化为纯内存模式（便于测试）。
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
from datetime import datetime, timezone
from typing import Any

import aiohttp
import feedparser

logger = logging.getLogger("siyi.services.rss_subscriber")

# settings 中存放订阅列表的键
SETTINGS_KEY = "rss_subscriptions"

# 单个 feed 抓取超时（秒）
FEED_TIMEOUT_SECONDS = 15.0
# 单个 feed 最多保留的条目数
MAX_ITEMS_PER_FEED = 50

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)


def _now_iso() -> str:
    """当前 UTC 时间的 ISO8601 字符串。"""
    return datetime.now(timezone.utc).isoformat()


def _make_feed_id(url: str) -> str:
    """根据 URL 生成稳定的 feed id（sha1 前 12 位），避免特殊字符。"""
    return hashlib.sha1(url.encode("utf-8"), usedforsecurity=False).hexdigest()[:12]


def _parse_published(entry: dict[str, Any]) -> str:
    """从 feedparser entry 中提取发布时间，统一返回 ISO8601 字符串（失败返回空串）。

    feedparser 会把各种格式的时间解析为 ``time.struct_time`` 并放在
    ``entry.published_parsed`` / ``entry.updated_parsed``。
    """
    for key in ("published_parsed", "updated_parsed", "created_parsed"):
        st = entry.get(key)
        if st:
            try:
                dt = datetime(*st[:6], tzinfo=timezone.utc)
                return dt.isoformat()
            except Exception:
                continue
    for key in ("published", "updated", "created"):
        raw = entry.get(key)
        if raw:
            return str(raw)
    return ""


class RSSSubscriber:
    """RSS/Atom feed 订阅管理器。

    用法::

        sub = RSSSubscriber(settings_repository=repo)
        result = await sub.subscribe("https://example.com/feed.xml", name="Example")
        items = await sub.fetch_feed("https://example.com/feed.xml")
        all_items = await sub.fetch_all()  # dict[url, list[items]]
    """

    def __init__(self, settings_repository: Any = None) -> None:
        self._settings_repository = settings_repository
        # feed_id -> entry
        self._subscriptions: dict[str, dict[str, Any]] = {}
        self._load()

    # ------------------------------------------------------------------
    # 持久化
    # ------------------------------------------------------------------

    def _load(self) -> None:
        """从 settings 加载已保存的订阅。"""
        if self._settings_repository is None:
            return
        try:
            raw = self._settings_repository.get_setting(SETTINGS_KEY, None)
        except Exception as exc:
            logger.debug("load %s failed: %s", SETTINGS_KEY, exc)
            return
        if isinstance(raw, list):
            # 兼容旧格式：直接是列表
            for entry in raw:
                if not isinstance(entry, dict):
                    continue
                fid = str(entry.get("id") or _make_feed_id(str(entry.get("url", ""))))
                self._subscriptions[fid] = dict(entry)
            return
        if not isinstance(raw, dict):
            return
        subs = raw.get("subscriptions")
        if not isinstance(subs, list):
            return
        for entry in subs:
            if not isinstance(entry, dict):
                continue
            fid = str(entry.get("id") or _make_feed_id(str(entry.get("url", ""))))
            self._subscriptions[fid] = dict(entry)

    def _persist(self) -> None:
        """将订阅列表写入 settings。"""
        if self._settings_repository is None:
            return
        try:
            self._settings_repository.set_setting(
                SETTINGS_KEY,
                {"subscriptions": list(self._subscriptions.values())},
            )
        except Exception as exc:
            logger.warning("persist %s failed: %s", SETTINGS_KEY, exc)

    # ------------------------------------------------------------------
    # 订阅管理
    # ------------------------------------------------------------------

    def list_subscriptions(self) -> list[dict[str, Any]]:
        """返回所有订阅，每项含 id/url/name/category_hint/last_fetched_at/
        item_count/last_error/feed_title。"""
        result = []
        for entry in self._subscriptions.values():
            result.append({
                "id": entry.get("id", ""),
                "url": entry.get("url", ""),
                "name": entry.get("name", ""),
                "category_hint": entry.get("category_hint", ""),
                "feed_title": entry.get("feed_title", ""),
                "last_fetched_at": entry.get("last_fetched_at", ""),
                "item_count": int(entry.get("item_count", 0) or 0),
                "last_error": entry.get("last_error", ""),
            })
        return result

    def get_subscription(self, feed_id: str) -> dict[str, Any] | None:
        """按 id 获取单个订阅的完整 entry（含内部字段）。"""
        return self._subscriptions.get(feed_id)

    async def subscribe(
        self,
        url: str,
        name: str = "",
        category_hint: str = "",
    ) -> dict[str, Any]:
        """订阅一个 RSS/Atom feed。

        流程：
          1. 校验 URL 非空且格式合法（http/https）。
          2. 抓取并解析 feed，确认是合法 RSS/Atom。
          3. 从 feed 中提取标题（feed.feed.title），未提供 name 时用作订阅名。
          4. 持久化订阅。

        Returns:
            成功: ``{"success": True, "feed": {...}}``
            失败: ``{"success": False, "error": "..."}``
        """
        url = (url or "").strip()
        if not url:
            return {"success": False, "error": "url 不能为空"}
        if not (url.startswith("http://") or url.startswith("https://")):
            return {"success": False, "error": "url 必须以 http:// 或 https:// 开头"}

        feed_id = _make_feed_id(url)

        existing = self._subscriptions.get(feed_id)
        if existing is not None:
            return {"success": True, "feed": self._public_view(existing), "already_subscribed": True}

        # 校验可达性 + 格式合法性
        try:
            items, feed_title = await self._fetch_and_parse(url)
        except asyncio.TimeoutError:
            return {"success": False, "error": f"抓取超时（{FEED_TIMEOUT_SECONDS}s）"}
        except aiohttp.ClientError as exc:
            return {"success": False, "error": f"网络错误: {exc}"}
        except _InvalidFeedError as exc:
            return {"success": False, "error": str(exc)}
        except Exception as exc:
            return {"success": False, "error": f"解析失败: {exc}"}

        effective_name = (name or feed_title or url).strip()

        entry = {
            "id": feed_id,
            "url": url,
            "name": effective_name,
            "category_hint": (category_hint or "").strip(),
            "feed_title": feed_title or "",
            "last_fetched_at": _now_iso(),
            "item_count": len(items),
            "last_error": "",
        }
        self._subscriptions[feed_id] = entry
        self._persist()
        logger.info("rss subscribed: %s (%s) -> %d items", effective_name, url, len(items))
        return {"success": True, "feed": self._public_view(entry)}

    def unsubscribe(self, feed_id: str) -> bool:
        """取消订阅。返回是否成功（feed_id 不存在时返回 False）。"""
        if feed_id not in self._subscriptions:
            return False
        del self._subscriptions[feed_id]
        self._persist()
        logger.info("rss unsubscribed: %s", feed_id)
        return True

    # ------------------------------------------------------------------
    # 抓取
    # ------------------------------------------------------------------

    async def fetch_feed(self, url: str) -> list[dict[str, Any]]:
        """抓取并解析单个 feed URL，返回归一化后的条目列表。

        异常会向上抛出（``asyncio.TimeoutError`` / ``aiohttp.ClientError`` /
        ``_InvalidFeedError``）。
        """
        items, _title = await self._fetch_and_parse(url)
        return items

    async def fetch_all(self) -> dict[str, list[dict[str, Any]]]:
        """并发抓取所有已订阅的 feed。

        Returns:
            ``{url: [items...]}``。失败的 feed 对应空列表（错误记录在订阅的
            ``last_error`` 字段，不影响其他 feed）。
        """
        if not self._subscriptions:
            return {}
        urls = [(fid, entry["url"]) for fid, entry in self._subscriptions.items() if entry.get("url")]
        if not urls:
            return {}

        results: dict[str, list[dict[str, Any]]] = {url: [] for _fid, url in urls}

        async def _one(fid: str, url: str) -> None:
            try:
                items, _title = await self._fetch_and_parse(url)
                results[url] = items
                self._update_fetch_state(fid, len(items), "")
            except asyncio.TimeoutError:
                self._update_fetch_state(fid, 0, f"超时（{FEED_TIMEOUT_SECONDS}s）")
            except aiohttp.ClientError as exc:
                self._update_fetch_state(fid, 0, f"网络错误: {exc}")
            except _InvalidFeedError as exc:
                self._update_fetch_state(fid, 0, str(exc))
            except Exception as exc:
                self._update_fetch_state(fid, 0, f"解析失败: {exc}")

        await asyncio.gather(*[_one(fid, url) for fid, url in urls], return_exceptions=False)
        self._persist()
        return results

    async def fetch_one(self, feed_id: str) -> dict[str, Any]:
        """抓取单个订阅的 feed。

        Returns:
            ``{"success": True, "items": [...], "feed": {...}}`` 或
            ``{"success": False, "error": "..."}``
        """
        entry = self._subscriptions.get(feed_id)
        if entry is None:
            return {"success": False, "error": f"订阅 {feed_id} 不存在"}
        url = entry.get("url", "")
        if not url:
            return {"success": False, "error": "订阅缺少 url"}
        try:
            items, _title = await self._fetch_and_parse(url)
        except asyncio.TimeoutError:
            self._update_fetch_state(feed_id, 0, f"超时（{FEED_TIMEOUT_SECONDS}s）")
            self._persist()
            return {"success": False, "error": f"超时（{FEED_TIMEOUT_SECONDS}s）"}
        except aiohttp.ClientError as exc:
            self._update_fetch_state(feed_id, 0, f"网络错误: {exc}")
            self._persist()
            return {"success": False, "error": f"网络错误: {exc}"}
        except _InvalidFeedError as exc:
            self._update_fetch_state(feed_id, 0, str(exc))
            self._persist()
            return {"success": False, "error": str(exc)}
        except Exception as exc:
            self._update_fetch_state(feed_id, 0, f"解析失败: {exc}")
            self._persist()
            return {"success": False, "error": f"解析失败: {exc}"}
        self._update_fetch_state(feed_id, len(items), "")
        self._persist()
        return {"success": True, "items": items, "feed": self._public_view(entry)}

    # ------------------------------------------------------------------
    # 解析
    # ------------------------------------------------------------------

    def parse_items(self, feed_content: str | bytes, url: str = "") -> list[dict[str, Any]]:
        """解析 feed 内容（XML 字符串或字节）为归一化条目列表。

        归一化条目格式::

            {
                "title": str,
                "url": str,
                "summary": str,
                "published_at": str,   # ISO8601 或原始字符串
                "source": str,         # url（便于调用方重命名为 rss:{name}
            }

        使用 feedparser 兼容 RSS 2.0 / Atom 1.0。
        """
        if not feed_content:
            return []

        parsed = feedparser.parse(feed_content)
        if parsed is None:
            return []

        entries = getattr(parsed, "entries", []) or []
        items: list[dict[str, Any]] = []
        for entry in entries:
            if len(items) >= MAX_ITEMS_PER_FEED:
                break
            title = self._extract_text(entry, "title")
            link = self._extract_text(entry, "link")
            summary_raw = (
                self._extract_text(entry, "summary")
                or self._extract_text(entry, "description")
                or self._extract_text(entry, "content")
            )
            summary = _strip_html(summary_raw)[:500]
            published_at = _parse_published(entry)
            items.append({
                "title": title,
                "url": link,
                "summary": summary,
                "published_at": published_at,
                "source": url,
            })
        return items

    async def _fetch_and_parse(self, url: str) -> tuple[list[dict[str, Any]], str]:
        """抓取 url 并解析为 (items, feed_title)。

        Raises:
            asyncio.TimeoutError: 抓取超时
            aiohttp.ClientError: 网络错误
            _InvalidFeedError: 响应不是合法 RSS/Atom feed
        """
        timeout = aiohttp.ClientTimeout(total=FEED_TIMEOUT_SECONDS)
        headers = {
            "User-Agent": _USER_AGENT,
            "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml, */*",
        }
        async with aiohttp.ClientSession(timeout=timeout, trust_env=True) as session:
            async with session.get(url, headers=headers) as resp:
                if resp.status >= 400:
                    raise _InvalidFeedError(f"HTTP {resp.status}: {resp.reason}")
                content = await resp.text()

        # feedparser 是同步解析库，大 feed（数百条目）解析耗时可达数百毫秒；
        # 直接在事件循环内调用会阻塞全部 WS RPC（2026-08-29 审查 M8，遵守 to_thread 铁律）。
        parsed = await asyncio.to_thread(feedparser.parse, content)

        feed_meta = getattr(parsed, "feed", None)
        feed_title = ""
        if feed_meta is not None:
            feed_title = self._extract_text(feed_meta, "title")

        entries = getattr(parsed, "entries", []) or []
        if not entries:
            bozo = getattr(parsed, "bozo", 0)
            if bozo:
                raise _InvalidFeedError("URL 未返回有效的 RSS/Atom feed（无法解析）")
            return [], feed_title

        items: list[dict[str, Any]] = []
        for entry in entries:
            if len(items) >= MAX_ITEMS_PER_FEED:
                break
            title = self._extract_text(entry, "title")
            link = self._extract_text(entry, "link")
            summary_raw = (
                self._extract_text(entry, "summary")
                or self._extract_text(entry, "description")
                or self._extract_text(entry, "content")
            )
            summary = _strip_html(summary_raw)[:500]
            published_at = _parse_published(entry)
            items.append({
                "title": title,
                "url": link,
                "summary": summary,
                "published_at": published_at,
                "source": url,
            })
        return items, feed_title

    def _update_fetch_state(self, feed_id: str, item_count: int, error: str) -> None:
        """更新订阅的抓取状态字段。"""
        entry = self._subscriptions.get(feed_id)
        if entry is None:
            return
        entry["last_fetched_at"] = _now_iso()
        entry["item_count"] = item_count
        entry["last_error"] = error

    @staticmethod
    def _extract_text(obj: Any, key: str) -> str:
        """从 feedparser 对象中安全提取文本字段（兼容 dict 与 FeedParserDict）。"""
        if obj is None:
            return ""
        try:
            val = obj.get(key, "")
        except Exception:
            return ""
        if val is None:
            return ""
        if isinstance(val, list):
            parts = []
            for piece in val:
                if isinstance(piece, dict):
                    parts.append(str(piece.get("value", "") or ""))
                elif isinstance(piece, str):
                    parts.append(piece)
            return "".join(parts)
        return str(val)

    @staticmethod
    def _public_view(entry: dict[str, Any]) -> dict[str, Any]:
        """返回订阅 entry 的对外视图（与 list_subscriptions 一致）。"""
        return {
            "id": entry.get("id", ""),
            "url": entry.get("url", ""),
            "name": entry.get("name", ""),
            "category_hint": entry.get("category_hint", ""),
            "feed_title": entry.get("feed_title", ""),
            "last_fetched_at": entry.get("last_fetched_at", ""),
            "item_count": int(entry.get("item_count", 0) or 0),
            "last_error": entry.get("last_error", ""),
        }


class _InvalidFeedError(Exception):
    """URL 返回的内容不是合法的 RSS/Atom feed。"""


def _strip_html(text: str) -> str:
    """简单去除 HTML 标签并 unescape（与 info_collector_service 一致）。"""
    import html as _html
    import re as _re

    text = _re.sub(r"<[^>]+>", "", text or "")
    text = _html.unescape(text)
    return text.strip()


__all__ = [
    "RSSSubscriber",
    "SETTINGS_KEY",
    "FEED_TIMEOUT_SECONDS",
    "MAX_ITEMS_PER_FEED",
]
