"""Readwise Reader API v3 客户端。

提供 Readwise Reader 文档列表、高亮同步，以及到内部阅读项模型的映射。
文档：
    - Reader API v3: https://readwise.io/reader_api
    - Highlights API v2: https://readwise.io/api_deets
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Optional

import httpx

logger = logging.getLogger("siyi.integrations.readwise")

READWISE_BASE_URL = "https://readwise.io"
READWISE_READER_LIST_PATH = "/api/v3/list/"
READWISE_HIGHLIGHTS_PATH = "/api/v2/highlights/"
READWISE_BOOKS_PATH = "/api/v2/books/"


class ReadwiseError(RuntimeError):
    """Readwise API 调用失败。"""


@dataclass
class ReadwiseDocument:
    """Readwise Reader 文档（列表项）。"""
    id: str
    title: str
    url: str
    source: str
    category: str
    author: str = ""
    summary: str = ""
    image_url: str = ""
    location: str = ""
    tags: list[str] = field(default_factory=list)
    saved_at: str = ""
    last_moved_at: str = ""
    notes: str = ""
    highlights: list[dict[str, Any]] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)

    def to_reading_item(self) -> dict[str, Any]:
        """映射到内部阅读项模型。返回副本，避免外部修改影响 raw/tags。"""
        return {
            "id": f"readwise_{self.id}",
            "external_id": self.id,
            "source": "readwise",
            "title": self.title,
            "url": self.url,
            "author": self.author,
            "category": self.category,
            "summary": self.summary,
            "tags": list(self.tags),
            "saved_at": self.saved_at,
            "raw": dict(self.raw),
        }


@dataclass
class ReadwiseHighlight:
    """Readwise 高亮。"""
    id: str
    text: str
    note: str = ""
    location: Optional[int] = None
    location_type: str = ""
    highlighted_at: str = ""
    url: str = ""
    color: str = ""
    raw: dict[str, Any] = field(default_factory=dict)


class ReadwiseReaderClient:
    """Readwise Reader API 客户端。"""

    def __init__(
        self,
        token: str,
        base_url: str = READWISE_BASE_URL,
        session: "httpx.Client | None" = None,
    ):
        self.token = (token or "").strip()
        self.base_url = (base_url or "").rstrip("/")
        self._session = session

    def _get_session(self) -> httpx.Client:
        if self._session is None:
            self._session = httpx.Client(timeout=30.0)
        return self._session

    def _request(
        self,
        method: str,
        path: str,
        params: "dict[str, Any] | None" = None,
        json_body: "dict[str, Any] | None" = None,
    ) -> dict[str, Any]:
        """发起请求并返回 JSON dict。错误时抛 ReadwiseError。"""
        url = f"{self.base_url}{path}"
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
        }
        try:
            resp = self._get_session().request(
                method, url, params=params, json=json_body, headers=headers, timeout=30.0
            )
        except httpx.RequestError as exc:
            raise ReadwiseError(f"网络请求失败: {exc}") from exc

        if resp.status_code == 401:
            raise ReadwiseError("token 无效或已过期 (401)")
        if resp.status_code == 429:
            raise ReadwiseError("请求过于频繁 (429)，请稍后重试")
        if resp.status_code >= 400:
            body = resp.text or ""
            if len(body) > 200:
                body = body[:200] + "..."
            raise ReadwiseError(f"API 错误 {resp.status_code}: {body}")

        try:
            data = resp.json()
        except ValueError as exc:
            raise ReadwiseError(f"响应不是合法 JSON: {exc}") from exc
        if not isinstance(data, dict):
            raise ReadwiseError(f"响应格式异常: 期望 dict，实际 {type(data).__name__}")
        return data

    def verify_token(self) -> dict[str, Any]:
        """验证 token 是否有效（调用 list 接口，不翻页）。"""
        return self._request("GET", READWISE_READER_LIST_PATH, params={"pageSize": 1})

    def list_documents(
        self,
        category: "str | None" = None,
        location: "str | None" = None,
        page_cursor: "str | None" = None,
        page_size: int = 100,
    ) -> "tuple[list[ReadwiseDocument], str | None]":
        """获取单页文档列表。返回 (文档列表, 下一页 cursor)。"""
        params: dict[str, Any] = {"pageSize": max(1, min(int(page_size), 1000))}
        if category:
            params["category"] = category
        if location:
            params["location"] = location
        if page_cursor:
            params["pageCursor"] = page_cursor
        data = self._request("GET", READWISE_READER_LIST_PATH, params=params)
        results = data.get("results") or []
        docs = [self._parse_document(item) for item in results if isinstance(item, dict) and item.get("id")]
        return docs, data.get("next")

    def list_all_documents(
        self,
        category: "str | None" = None,
        location: "str | None" = None,
        max_pages: int = 100,
        page_size: int = 100,
    ) -> "list[ReadwiseDocument]":
        """获取所有文档（自动翻页）。max_pages<=0 时不请求。"""
        if max_pages <= 0:
            return []
        out: list[ReadwiseDocument] = []
        cursor: "str | None" = None
        pages = 0
        while pages < max_pages:
            docs, cursor = self.list_documents(
                category=category, location=location, page_cursor=cursor, page_size=page_size
            )
            out.extend(docs)
            pages += 1
            if not cursor:
                break
        return out

    def list_highlights(
        self,
        book_id: "str | None" = None,
        page_cursor: "str | None" = None,
        page_size: int = 100,
    ) -> "tuple[list[ReadwiseHighlight], str | None]":
        """获取单页高亮列表。返回 (高亮列表, 下一页 cursor)。"""
        params: dict[str, Any] = {"pageSize": max(1, min(int(page_size), 1000))}
        if book_id:
            params["book_id"] = book_id
        if page_cursor:
            params["pageCursor"] = page_cursor
        data = self._request("GET", READWISE_HIGHLIGHTS_PATH, params=params)
        results = data.get("results") or []
        hl = [self._parse_highlight(item) for item in results if isinstance(item, dict) and item.get("id")]
        return hl, data.get("next")

    def list_all_highlights(
        self,
        book_id: "str | None" = None,
        max_pages: int = 100,
        page_size: int = 100,
    ) -> "list[ReadwiseHighlight]":
        """获取所有高亮（自动翻页）。max_pages<=0 时不请求。"""
        if max_pages <= 0:
            return []
        out: list[ReadwiseHighlight] = []
        cursor: "str | None" = None
        pages = 0
        while pages < max_pages:
            hl, cursor = self.list_highlights(
                book_id=book_id, page_cursor=cursor, page_size=page_size
            )
            out.extend(hl)
            pages += 1
            if not cursor:
                break
        return out

    def get_books(
        self,
        category: "str | None" = None,
        page_cursor: "str | None" = None,
        page_size: int = 100,
    ) -> "tuple[list[ReadwiseDocument], str | None]":
        """获取单页书籍列表（映射为 ReadwiseDocument）。返回 (列表, 下一页 cursor)。"""
        params: dict[str, Any] = {"pageSize": max(1, min(int(page_size), 1000))}
        if category:
            params["category"] = category
        if page_cursor:
            params["pageCursor"] = page_cursor
        data = self._request("GET", READWISE_BOOKS_PATH, params=params)
        results = data.get("results") or []
        books = [self._parse_book(item) for item in results if isinstance(item, dict) and item.get("id")]
        return books, data.get("next")

    def _parse_document(self, item: dict[str, Any]) -> ReadwiseDocument:
        def s(v: Any) -> str:
            return str(v or "").strip()

        tags = item.get("tags") or []
        if isinstance(tags, list):
            tags = [str(t).strip() for t in tags if str(t).strip()]
        else:
            tags = []

        highlights = item.get("highlights") or []
        if not isinstance(highlights, list):
            highlights = []

        return ReadwiseDocument(
            id=s(item.get("id")),
            title=s(item.get("title") or item.get("readable_title")),
            url=s(item.get("url") or item.get("source_url")),
            source="readwise",
            category=s(item.get("category")),
            author=s(item.get("author") or item.get("readable_author")),
            summary=s(item.get("summary")),
            image_url=s(item.get("image_url")),
            location=s(item.get("location")),
            tags=tags,
            saved_at=s(item.get("saved_at")),
            last_moved_at=s(item.get("last_moved_at")),
            notes=s(item.get("notes")),
            highlights=highlights,
            raw=item,
        )

    def _parse_highlight(self, item: dict[str, Any]) -> ReadwiseHighlight:
        def s(v: Any) -> str:
            return str(v or "").strip()

        loc_raw = item.get("location")
        location: "int | None" = None
        if loc_raw is not None:
            loc_str = str(loc_raw).strip()
            if loc_str:
                try:
                    location = int(loc_str)
                except ValueError:
                    location = None

        return ReadwiseHighlight(
            id=s(item.get("id")),
            text=s(item.get("text")),
            note=s(item.get("note")),
            location=location,
            location_type=s(item.get("location_type")),
            highlighted_at=s(item.get("highlighted_at")),
            url=s(item.get("url")),
            color=s(item.get("color")),
            raw=item,
        )

    def _parse_book(self, item: dict[str, Any]) -> ReadwiseDocument:
        def s(v: Any) -> str:
            return str(v or "").strip()

        tags = item.get("tags") or []
        if isinstance(tags, list):
            tags = [str(t).strip() for t in tags if str(t).strip()]
        else:
            tags = []

        return ReadwiseDocument(
            id=s(item.get("id")),
            title=s(item.get("title")),
            url=s(item.get("source_url")),
            source="readwise",
            category=s(item.get("category")),
            author=s(item.get("author")),
            image_url=s(item.get("cover_image_url")),
            tags=tags,
            saved_at=s(item.get("user_book_created_at")),
            raw=item,
        )


def create_readwise_client(token: str) -> ReadwiseReaderClient:
    """工厂函数：创建 ReadwiseReaderClient 实例。"""
    return ReadwiseReaderClient(token=token)
