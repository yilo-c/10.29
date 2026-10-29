# -*- coding: utf-8 -*-
"""外部内容集成 JSON-RPC handlers。

支持 Readwise Reader / Highlights、Kindle 剪贴、浏览器书签导入，
并将条目沉淀到阅读列表与知识图谱。
"""

from __future__ import annotations

import asyncio
import html
import json
import logging
import os
import re
import time
import uuid
from typing import Any
from urllib.parse import unquote

from core.events import AppEvent, event_bus
from core.secure_key_storage import SecureKeyStorage
from core.settings_keys import (
    KEY_BROWSER_BOOKMARKS_ENABLED,
    KEY_BROWSER_BOOKMARKS_IMPORT_DIR,
    KEY_KINDLE_IMPORT_DIR,
    KEY_READWISE_ENABLED,
    KEY_READWISE_LAST_SYNC_AT,
    KEY_READWISE_SYNC_BOOKS,
    KEY_READWISE_SYNC_HIGHLIGHTS,
    KEY_READWISE_TOKEN,
)
from integrations.readwise_client import ReadwiseError, ReadwiseReaderClient

logger = logging.getLogger("siyi.backend.handlers.external_content")

_SOURCE_READWISE = "readwise"
_SOURCE_KINDLE = "kindle"
_SOURCE_BROWSER_BOOKMARK = "browser_bookmark"
_MAX_FILE_SIZE_MB = 10
_MAX_IMPORT_ITEMS = 2000
_KINDLE_CLIPPING_SEPARATOR = "=========="


def _get_articles_path_from_store(data_store) -> str:
    if data_store and hasattr(data_store, "base_dir"):
        return os.path.join(data_store.base_dir, "saved_articles.json")
    fallback = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "data")
    return os.path.join(os.path.abspath(fallback), "saved_articles.json")


def _get_articles_path(context) -> str:
    return _get_articles_path_from_store(getattr(context, "local_data_store", None))


def _load_articles(path: str) -> dict[str, Any]:
    try:
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception as exc:
        logger.warning("Failed to load saved articles: %s", exc)
    return {"articles": {}, "order": []}


def _save_articles(path: str, data: dict[str, Any]) -> None:
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        # 原子写：先写临时文件再 os.replace，防止崩溃损坏已存文章
        tmp = f"{path}.tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
    except Exception as exc:
        logger.warning("Failed to save articles: %s", exc)


def _normalize_text_key(text: Any) -> str:
    """去重用的规范化键（NFKC + casefold + 空白折叠）。

    PD-5a（2026-09-18）治本：与 ``knowledge.graph_store.normalize_name`` 同一
    口径。"同一实体的名字在不同通道被当成不同名字"是本批次的**同源缺陷形态**
    ——PD-5a 是导入通道按原文本判重（大小写异体各建一个图谱节点），PD-5b 是查询
    通道用子串折叠把新实体折叠进旧实体。所以判重口径必须收敛成一处语义，而不是
    在导入处再补一个 ``.lower()``（那正是下次换个通道又漏一次的写法）。
    """
    import unicodedata

    folded = unicodedata.normalize("NFKC", str(text or ""))
    return " ".join(folded.split()).casefold()


def _build_identity_index(data: dict[str, Any]) -> dict[str, str]:
    """构建「规范化外部标识 → 内部文章 ID」索引（每次导入前构建一次，O(n)）。

    单独建索引而不是在逐条判重时全表扫：一次导入最多 2000 条、库内最多 500 条，
    逐条扫是 10^6 量级的重复规范化；建一次索引后每条判重都是 O(1)。
    """
    index: dict[str, str] = {}
    for article_id in data.get("articles") or {}:
        index.setdefault(_normalize_text_key(article_id), article_id)
    return index


def _article_id_for_source(source: str, external_id: str) -> str:
    """为外部来源生成稳定的内部文章 ID。"""
    external_id = str(external_id or "").strip()
    if not external_id:
        external_id = uuid.uuid4().hex[:12]
    return f"{source}_{external_id}"


def _dedupe_existing(
    data: dict[str, Any],
    source: str,
    external_id: str,
    identity_index: dict[str, str] | None = None,
) -> str | None:
    """如果已存在同来源同外部 ID 的文章，返回其内部 ID。

    ``identity_index``（PD-5a）：显式传入时额外做**规范化标识**判重——即大小写/
    全角/空白异体视为同一条目。只对"标识本身就是名称"的来源传（Kindle 剪贴的
    external_id 由 ``标题_位置`` 派生）；readwise 用稳定 doc.id、书签用 URL，
    它们的标识不是名称，casefold 一个 URL 会把路径大小写不同的两个真实资源并成
    一个，故这两条通道**不传**索引、保持原样精确判重。
    """
    target_id = _article_id_for_source(source, external_id)
    if target_id in data.get("articles", {}):
        return target_id
    if identity_index is not None:
        return identity_index.get(_normalize_text_key(target_id))
    return None


def _add_article(
    data: dict[str, Any],
    source: str,
    external_id: str,
    title: str,
    url: str,
    summary: str = "",
    tags: list[str] | None = None,
    extra: dict[str, Any] | None = None,
) -> str:
    article_id = _article_id_for_source(source, external_id)
    existing = data["articles"].get(article_id)
    now = time.time()

    article = {
        "id": article_id,
        "external_id": external_id,
        "source": source,
        "title": title,
        "url": url,
        "summary": summary,
        "tags": list(tags) if tags else [],
        "saved_at": existing.get("saved_at") if existing else now,
        "imported_at": now,
    }
    if extra:
        article.update(extra)
    data["articles"][article_id] = article
    if article_id not in data["order"]:
        data["order"].insert(0, article_id)
    while len(data["order"]) > 500:
        oldest = data["order"].pop()
        data["articles"].pop(oldest, None)
    return article_id


def _ensure_knowledge_entity(graph_store, article_id: str, title: str, source: str, url: str) -> str | None:
    if graph_store is None:
        return None
    try:
        entity_id = f"external_content_{article_id}"
        graph_store.add_entity(
            entity_id,
            title,
            "external_content",
            [],
            # PD-3（2026-09-17）：properties.source_id = 裸 article_id，与
            # graph_store.remove_by_source(source_type, source_id) 的候选键空间接合，
            # 删除文章时可反查级联清理图谱实体与边。
            properties={"source": source, "url": url, "source_id": article_id},
        )
        return entity_id
    except Exception as exc:
        logger.debug("Failed to create knowledge entity for %s: %s", article_id, exc)
        return None


def _flush_graph(graph_store, op: str) -> None:
    """PD-1（2026-09-17）：导入类操作在写图谱后必须落盘。

    旧病：三处 import（readwise/kindle/bookmarks）建实体+边后全程无 save()，
    图谱更新只活在内存，重启即丢，落盘时机=其他通道的偶然 flush（多进程下互相覆盖）。
    单条 add 失败仍走 best-effort，但整批写完必须 save；save 失败如实记日志（吞不得）。
    """
    if graph_store is None:
        return
    try:
        graph_store.save()
    except Exception:
        logger.exception("external_content %s: 图谱 save 失败（实体仅在内存，重启即丢）", op)


def _link_source_entity(graph_store, article_entity_id: str, source: str) -> None:
    if graph_store is None or not article_entity_id:
        return
    try:
        source_entity_id = f"external_source_{source}"
        graph_store.add_entity(entity_id=source_entity_id, name=source, entity_type="external_source")
        graph_store.add_relation(
            source_id=article_entity_id,
            target_id=source_entity_id,
            relation_type="sourced_from",
        )
    except Exception as exc:
        logger.debug("Failed to link source entity: %s", exc)


def _secure() -> SecureKeyStorage:
    return SecureKeyStorage(namespace="com.siyi.external")


def _load_readwise_token(settings_repository) -> str:
    if settings_repository is None:
        return ""
    raw = settings_repository.get_setting(KEY_READWISE_TOKEN, "")
    if not raw:
        return ""
    try:
        return _secure().decrypt(raw)
    except Exception:
        logger.warning("Failed to decrypt readwise token, treating as plaintext")
        return str(raw)


def _save_readwise_token(settings_repository, token: str) -> None:
    if settings_repository is None:
        return
    encrypted = _secure().encrypt(token, key_id="readwise_token") if token else ""
    settings_repository.set_setting(KEY_READWISE_TOKEN, encrypted)


def _sync_readwise(
    settings_repository,
    articles_path: str,
    knowledge_graph_store=None,
    max_documents: int = 500,
) -> dict[str, Any]:
    token = _load_readwise_token(settings_repository)
    if not token:
        return {"success": False, "error": "Readwise token 未配置"}

    client = ReadwiseReaderClient(token)
    try:
        client.verify_token()
    except ReadwiseError as exc:
        return {"success": False, "error": str(exc)}

    sync_books = bool(settings_repository.get_setting(KEY_READWISE_SYNC_BOOKS, True))
    sync_highlights = bool(settings_repository.get_setting(KEY_READWISE_SYNC_HIGHLIGHTS, True))

    data = _load_articles(articles_path)
    imported = 0
    skipped = 0
    failed = 0
    errors: list[str] = []

    try:
        documents: list[Any] = []
        if sync_books:
            docs, _ = client.list_documents(page_size=min(100, max_documents))
            documents.extend(docs)
            books, _ = client.get_books(page_size=min(100, max_documents))
            documents.extend(books)
        else:
            docs, _ = client.list_documents(page_size=min(100, max_documents))
            documents.extend(docs)

        documents = documents[:max_documents]

        for doc in documents:
            if _dedupe_existing(data, _SOURCE_READWISE, doc.id):
                skipped += 1
                continue
            summary_parts = [doc.summary]
            if sync_highlights and doc.highlights:
                summary_parts.append("\n".join(f"- {h.get('text', '')}" for h in doc.highlights[:20]))
            summary = "\n\n".join(p.strip() for p in summary_parts if p.strip())
            article_id = _add_article(
                data,
                _SOURCE_READWISE,
                doc.id,
                doc.title,
                doc.url,
                summary,
                doc.tags,
                extra={
                    "author": doc.author,
                    "category": doc.category,
                    "image_url": doc.image_url,
                    "location": doc.location,
                    "saved_at": doc.saved_at,
                },
            )
            entity_id = _ensure_knowledge_entity(knowledge_graph_store, article_id, doc.title, _SOURCE_READWISE, doc.url)
            if entity_id:
                _link_source_entity(knowledge_graph_store, entity_id, _SOURCE_READWISE)
            imported += 1

        if sync_highlights:
            highlights = client.list_all_highlights(100, max(1, max_documents // 100))
            for highlight in highlights:
                if not highlight.text:
                    continue
                hid = f"hl_{highlight.id}"
                if _dedupe_existing(data, _SOURCE_READWISE, hid):
                    skipped += 1
                    continue
                title = f"Readwise 高亮: {highlight.text[:60]}..."
                article_id = _add_article(
                    data,
                    _SOURCE_READWISE,
                    hid,
                    title,
                    highlight.url,
                    highlight.text,
                    extra={
                        "highlight_id": highlight.id,
                        "highlight_note": highlight.note,
                        "location": highlight.location,
                        "location_type": highlight.location_type,
                        "highlighted_at": highlight.highlighted_at,
                        "color": highlight.color,
                    },
                )
                entity_id = _ensure_knowledge_entity(knowledge_graph_store, article_id, title, _SOURCE_READWISE, highlight.url)
                if entity_id:
                    _link_source_entity(knowledge_graph_store, entity_id, _SOURCE_READWISE)
                imported += 1
    except ReadwiseError as exc:
        failed += 1
        errors.append(str(exc))
    except Exception as exc:
        logger.exception("Readwise sync failed")
        failed += 1
        errors.append(str(exc))

    _save_articles(articles_path, data)
    # PD-1：图谱实体/边随 articles 同批落盘（readwise 同步）
    _flush_graph(knowledge_graph_store, "readwise_sync")

    last_sync_at = int(time.time())
    settings_repository.set_setting(KEY_READWISE_LAST_SYNC_AT, last_sync_at)

    result = {
        "success": failed == 0,
        "imported": imported,
        "skipped": skipped,
        "failed": failed,
        "errors": errors[:10],
        "last_sync_at": last_sync_at,
    }
    return result


def _parse_kindle_clippings(text: str) -> list[dict[str, Any]]:
    """解析 Kindle My Clippings.txt 文件。"""
    entries: list[dict[str, Any]] = []
    blocks = text.split(_KINDLE_CLIPPING_SEPARATOR)
    for block in blocks:
        block = block.strip()
        if not block:
            continue
        lines = [line.strip() for line in block.splitlines() if line.strip()]
        if len(lines) < 3:
            continue
        title_author = lines[0]
        meta = lines[1]
        highlight = "\n".join(lines[2:])

        title = title_author
        author = ""
        if "(" in title_author and title_author.endswith(")"):
            title = title_author[: title_author.rfind("(")].strip()
            author = title_author[title_author.rfind("(") + 1 : -1].strip()

        location = ""
        added_at = ""
        loc_match = re.search(r"Location\s+([\d-]+)", meta, re.IGNORECASE)
        if loc_match:
            location = loc_match.group(1)
        date_match = re.search(r"Added on\s+(.+)", meta, re.IGNORECASE)
        if date_match:
            added_at = date_match.group(1).strip()

        entries.append(
            {
                "title": title,
                "author": author,
                "location": location,
                "added_at": added_at,
                "highlight": highlight,
            }
        )
    return entries


def _import_kindle_file(file_path: str, articles_path: str, knowledge_graph_store=None) -> dict[str, Any]:
    if not os.path.isfile(file_path):
        return {"success": False, "error": "文件不存在"}
    size_mb = os.path.getsize(file_path) / 1048576
    if size_mb > _MAX_FILE_SIZE_MB:
        return {"success": False, "error": f"文件过大（{size_mb:.1f} MB），最大支持 {_MAX_FILE_SIZE_MB} MB"}

    try:
        with open(file_path, "r", encoding="utf-8-sig") as f:
            text = f.read()
    except UnicodeDecodeError:
        try:
            with open(file_path, "r", encoding="gbk") as f:
                text = f.read()
        except Exception as exc:
            logger.exception("external_content 以 GBK 编码读取 Kindle 文件失败")
            return {"success": False, "error": f"无法读取文件: {exc}"}
    except Exception as exc:
        logger.exception("external_content 读取 Kindle 文件失败")
        return {"success": False, "error": f"无法读取文件: {exc}"}

    entries = _parse_kindle_clippings(text)
    data = _load_articles(articles_path)
    # PD-5a：Kindle 的 external_id = "标题_位置"，标识就是名称 → 判重必须走
    # 规范化键，否则标题大小写一变就成"新条目 + 新图谱节点"（同一本书被拆成两个）。
    identity_index = _build_identity_index(data)
    imported = 0
    skipped = 0
    for entry in entries[:_MAX_IMPORT_ITEMS]:
        external_id = f"{entry['title']}_{entry['location']}" if entry["location"] else entry["title"]
        if _dedupe_existing(data, _SOURCE_KINDLE, external_id, identity_index):
            skipped += 1
            continue
        article_id = _add_article(
            data,
            _SOURCE_KINDLE,
            external_id,
            entry["title"],
            "",
            entry["highlight"],
            extra={
                "author": entry["author"],
                "location": entry["location"],
                "added_at": entry["added_at"],
            },
        )
        entity_id = _ensure_knowledge_entity(knowledge_graph_store, article_id, entry["title"], _SOURCE_KINDLE, "")
        if entity_id:
            _link_source_entity(knowledge_graph_store, entity_id, _SOURCE_KINDLE)
        imported += 1

    _save_articles(articles_path, data)
    # PD-1：导入类操作图谱随 articles 同批落盘（kindle / 书签同型）
    _flush_graph(knowledge_graph_store, "import_file")

    return {
        "success": True,
        "imported": imported,
        "skipped": skipped,
        "total": len(entries),
    }


def _parse_browser_bookmarks(html_text: str) -> list[dict[str, Any]]:
    """解析 Netscape Bookmark File Format HTML。"""
    entries: list[dict[str, Any]] = []
    pattern = re.compile(
        r"<a\s+[^>]*href=[\"']([^\"']+)[\"'][^>]*>(.*?)</a>",
        re.IGNORECASE | re.DOTALL,
    )
    for match in pattern.finditer(html_text):
        url = unquote(html.unescape(match.group(1)).strip())
        title = re.sub("<[^>]+>", "", html.unescape(match.group(2))).strip()
        if not url or url.startswith("javascript:") or url.startswith("data:"):
            continue
        add_date_match = re.search(r"add_date=[\"'](\d+)[\"']", match.group(0), re.IGNORECASE)
        add_date = ""
        if add_date_match:
            try:
                add_date = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(int(add_date_match.group(1))))
            except Exception:
                logger.exception("external_content 解析书签 add_date 时间戳失败，置空")
                add_date = ""
        entries.append(
            {
                "title": title or url,
                "url": url,
                "added_at": add_date,
            }
        )
    return entries


def _import_browser_bookmarks_file(file_path: str, articles_path: str, knowledge_graph_store=None) -> dict[str, Any]:
    if not os.path.isfile(file_path):
        return {"success": False, "error": "文件不存在"}
    size_mb = os.path.getsize(file_path) / 1048576
    if size_mb > _MAX_FILE_SIZE_MB:
        return {"success": False, "error": f"文件过大（{size_mb:.1f} MB），最大支持 {_MAX_FILE_SIZE_MB} MB"}

    try:
        with open(file_path, "r", encoding="utf-8") as f:
            html_text = f.read()
    except Exception as exc:
        logger.exception("external_content 读取浏览器书签文件失败")
        return {"success": False, "error": f"无法读取文件: {exc}"}

    entries = _parse_browser_bookmarks(html_text)
    data = _load_articles(articles_path)
    imported = 0
    skipped = 0
    for entry in entries[:_MAX_IMPORT_ITEMS]:
        if _dedupe_existing(data, _SOURCE_BROWSER_BOOKMARK, entry["url"]):
            skipped += 1
            continue
        article_id = _add_article(
            data,
            _SOURCE_BROWSER_BOOKMARK,
            entry["url"],
            entry["title"],
            entry["url"],
            extra={"added_at": entry["added_at"]},
        )
        entity_id = _ensure_knowledge_entity(knowledge_graph_store, article_id, entry["title"], _SOURCE_BROWSER_BOOKMARK, entry["url"])
        if entity_id:
            _link_source_entity(knowledge_graph_store, entity_id, _SOURCE_BROWSER_BOOKMARK)
        imported += 1

    _save_articles(articles_path, data)
    # PD-1：导入类操作图谱随 articles 同批落盘（kindle / 书签同型）
    _flush_graph(knowledge_graph_store, "import_file")

    return {
        "success": True,
        "imported": imported,
        "skipped": skipped,
        "total": len(entries),
    }


def _validate_file_path(file_path: str) -> str | None:
    """校验导入文件路径，防止路径遍历攻击。

    按 AGENTS.md 约定：拒绝包含 ``..`` 的路径；相对路径使用 ``os.path.basename()``
    规范化为纯文件名。返回清洗后的路径，非法时返回 None。
    """
    if not file_path or not isinstance(file_path, str):
        return None
    if ".." in file_path:
        return None
    # 相对路径仅允许纯文件名（相对 data/transfer_files 解析）
    if not os.path.isabs(file_path):
        return os.path.basename(file_path)
    return file_path


def register_external_content(registry, context) -> None:
    """注册外部内容集成 JSON-RPC 方法。"""

    io_lock_holder: dict = {}

    def _io_lock() -> asyncio.Lock:
        lock = io_lock_holder.get("lock")
        if lock is None:
            lock = asyncio.Lock()
            io_lock_holder["lock"] = lock
        return lock

    def _settings() -> Any:
        return getattr(context, "settings_repository", None)

    def _graph_store() -> Any:
        return getattr(context, "knowledge_graph_store", None)

    def _articles_path() -> str:
        return _get_articles_path(context)

    async def _get_articles_async() -> dict[str, Any]:
        path = _articles_path()
        async with _io_lock():
            return await asyncio.to_thread(_load_articles, path)

    async def _save_articles_async(data: dict[str, Any]) -> None:
        path = _articles_path()
        async with _io_lock():
            await asyncio.to_thread(_save_articles, path, data)

    async def external_content_list_sources(**kwargs: Any) -> dict[str, Any]:
        """列出已配置的外部内容来源。"""
        repo = _settings()
        if repo is None:
            return {"sources": []}

        def _read():
            return {
                "readwise": {
                    "enabled": bool(repo.get_setting(KEY_READWISE_ENABLED, False)),
                    "configured": bool(_load_readwise_token(repo)),
                    "last_sync_at": int(repo.get_setting(KEY_READWISE_LAST_SYNC_AT, 0) or 0),
                    "sync_highlights": bool(repo.get_setting(KEY_READWISE_SYNC_HIGHLIGHTS, True)),
                    "sync_books": bool(repo.get_setting(KEY_READWISE_SYNC_BOOKS, True)),
                },
                "kindle": {
                    "enabled": bool(repo.get_setting(KEY_KINDLE_IMPORT_DIR, "")),
                    "import_dir": repo.get_setting(KEY_KINDLE_IMPORT_DIR, ""),
                },
                "browser_bookmarks": {
                    "enabled": bool(repo.get_setting(KEY_BROWSER_BOOKMARKS_ENABLED, False)),
                    "import_dir": repo.get_setting(KEY_BROWSER_BOOKMARKS_IMPORT_DIR, ""),
                },
            }

        return {"success": True, "sources": await asyncio.to_thread(_read)}

    registry.register("external_content.list_sources", external_content_list_sources)

    async def external_content_configure_readwise(
        token: str = "",
        enabled: bool | None = None,
        sync_highlights: bool | None = None,
        sync_books: bool | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """配置 Readwise 集成。

        Args:
            token: Readwise API Token；传空字符串表示仅更新开关。
            enabled: 是否启用 Readwise 同步。
            sync_highlights: 是否同步高亮。
            sync_books: 是否同步书籍。
        """
        repo = _settings()
        if repo is None:
            return {"success": False, "error": "settings_repository 不可用"}

        def _configure():
            if token:
                _save_readwise_token(repo, token)
            if enabled is not None:
                repo.set_setting(KEY_READWISE_ENABLED, bool(enabled))
            if sync_highlights is not None:
                repo.set_setting(KEY_READWISE_SYNC_HIGHLIGHTS, bool(sync_highlights))
            if sync_books is not None:
                repo.set_setting(KEY_READWISE_SYNC_BOOKS, bool(sync_books))
            return {
                "enabled": bool(repo.get_setting(KEY_READWISE_ENABLED, False)),
                "configured": bool(_load_readwise_token(repo)),
            }

        return {"success": True, "config": await asyncio.to_thread(_configure)}

    registry.register("external_content.configure_readwise", external_content_configure_readwise)

    async def external_content_sync_readwise(max_documents: int = 500, **kwargs: Any) -> dict[str, Any]:
        """立即同步 Readwise 内容。"""
        repo = _settings()
        if repo is None:
            return {"success": False, "error": "settings_repository 不可用"}

        enabled = await asyncio.to_thread(repo.get_setting, KEY_READWISE_ENABLED, False)
        if not enabled:
            return {"success": False, "error": "Readwise 同步未启用"}

        result = await asyncio.to_thread(
            _sync_readwise,
            repo,
            _articles_path(),
            _graph_store(),
            max(1, min(int(max_documents), 2000)),
        )

        event_bus.emit(
            AppEvent(
                "external_content.sync_completed",
                payload={
                    "source": _SOURCE_READWISE,
                    "imported": result.get("imported", 0),
                    "skipped": result.get("skipped", 0),
                    "failed": result.get("failed", 0),
                    "last_sync_at": result.get("last_sync_at"),
                },
                source="external_content",
            )
        )

        return result

    registry.register("external_content.sync_readwise", external_content_sync_readwise)

    async def external_content_import_kindle(file_path: str = "", **kwargs: Any) -> dict[str, Any]:
        """从 Kindle My Clippings.txt 文件导入高亮。"""
        if not file_path:
            return {"success": False, "error": "file_path required"}
        cleaned = _validate_file_path(file_path)
        if cleaned is None:
            return {"success": False, "error": "invalid file path"}
        resolved_path = cleaned
        # 相对路径：优先在 data/transfer_files/ 下查找（手机端上传场景）
        if not os.path.isabs(resolved_path):
            ds = getattr(context, "local_data_store", None)
            base_dir = getattr(ds, "base_dir", None) if ds else None
            if base_dir:
                candidate = os.path.join(base_dir, "transfer_files", resolved_path)
                if os.path.isfile(candidate):
                    resolved_path = candidate

        result = await asyncio.to_thread(
            _import_kindle_file,
            resolved_path,
            _articles_path(),
            _graph_store(),
        )

        if result.get("success"):
            event_bus.emit(
                AppEvent(
                    "external_content.sync_completed",
                    payload={
                        "source": _SOURCE_KINDLE,
                        "imported": result.get("imported", 0),
                        "skipped": result.get("skipped", 0),
                        "failed": 0,
                    },
                    source="external_content",
                )
            )

        return result

    registry.register("external_content.import_kindle", external_content_import_kindle)

    async def external_content_import_browser_bookmarks(file_path: str = "", **kwargs: Any) -> dict[str, Any]:
        """从浏览器书签 HTML 文件导入。"""
        if not file_path:
            return {"success": False, "error": "file_path required"}
        cleaned = _validate_file_path(file_path)
        if cleaned is None:
            return {"success": False, "error": "invalid file path"}
        resolved_path = cleaned
        # 相对路径：优先在 data/transfer_files/ 下查找（手机端上传场景）
        if not os.path.isabs(resolved_path):
            ds = getattr(context, "local_data_store", None)
            base_dir = getattr(ds, "base_dir", None) if ds else None
            if base_dir:
                candidate = os.path.join(base_dir, "transfer_files", resolved_path)
                if os.path.isfile(candidate):
                    resolved_path = candidate

        result = await asyncio.to_thread(
            _import_browser_bookmarks_file,
            resolved_path,
            _articles_path(),
            _graph_store(),
        )

        if result.get("success"):
            event_bus.emit(
                AppEvent(
                    "external_content.sync_completed",
                    payload={
                        "source": _SOURCE_BROWSER_BOOKMARK,
                        "imported": result.get("imported", 0),
                        "skipped": result.get("skipped", 0),
                        "failed": 0,
                    },
                    source="external_content",
                )
            )

        return result

    registry.register("external_content.import_browser_bookmarks", external_content_import_browser_bookmarks)
