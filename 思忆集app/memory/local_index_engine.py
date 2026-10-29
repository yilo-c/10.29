"""SQLite FTS5 full-text index engine for memory entries with BM25 scoring.

Manages a local SQLite database with FTS5 virtual tables for fast
full-text search and BM25 relevance scoring of memory entries.
Uses jieba for Chinese text segmentation to enable proper CJK FTS5 matching.

Architecture:
- memory_entries: stores original (un-segmented) text for display
- memory_fts: standalone FTS5 table storing jieba-segmented text for search
- No external content / triggers: both tables managed explicitly
"""

from __future__ import annotations

import logging
import os
import re
import sqlite3
import threading
from collections.abc import Iterable
from pathlib import Path
from typing import Any

logger = logging.getLogger("siyi.local_index_engine")

try:
    import jieba
    JIEBA_AVAILABLE = True
except ImportError:
    JIEBA_AVAILABLE = False


def _segment(text: str) -> str:
    """使用 jieba 对文本进行分词，返回空格分隔的词序列。

    Args:
        text: 输入文本（可能包含中英文混合）。

    Returns:
        空格分隔的分词结果，例如 "数据库 设计 笔记"。
    """
    if not text or not text.strip():
        return ""
    text = str(text).strip()
    # Replace underscore with space since FTS5 ascii tokenizer treats _ as a
    # break character but MATCH queries created from jieba output may contain
    # standalone _ tokens (e.g. "single_tag" → "single _ tag").
    text = text.replace("_", " ")
    if JIEBA_AVAILABLE:
        words = list(jieba.cut(text))
        return " ".join(w.strip() for w in words if w.strip())
    # Fallback: simple character separation for CJK
    parts: list[str] = []
    current = ""
    for ch in text:
        if '一' <= ch <= '鿿' or '　' <= ch <= '〿':
            if current.strip():
                parts.extend(current.split())
                current = ""
            parts.append(ch)
        elif ch.isspace():
            if current.strip():
                parts.extend(current.split())
                current = ""
        else:
            current += ch
    if current.strip():
        parts.extend(current.split())
    return " ".join(parts)


class LocalIndexEngine:
    """SQLite FTS5 全文索引引擎，为记忆条目提供 BM25 关键词检索。

    Thread-safe: 所有公共方法使用 threading.Lock 保护。
    中文文本使用 jieba 分词后再存入 FTS5 索引。
    """

    _CREATE_ENTRIES_TABLE = """
        CREATE TABLE IF NOT EXISTS memory_entries (
            id TEXT PRIMARY KEY,
            title TEXT NOT NULL DEFAULT '',
            content TEXT NOT NULL DEFAULT '',
            tags TEXT NOT NULL DEFAULT '',
            source_type TEXT NOT NULL DEFAULT '',
            updated_at TEXT NOT NULL DEFAULT ''
        )
    """

    _CREATE_FTS_TABLE = """
        CREATE VIRTUAL TABLE IF NOT EXISTS memory_fts USING fts5(
            id UNINDEXED, title, content, tags,
            tokenize='ascii'
        )
    """

    def __init__(self, db_path: str | os.PathLike):
        """初始化 FTS5 索引引擎。

        Args:
            db_path: SQLite 数据库文件路径。
        """
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn: sqlite3.Connection | None = None
        self._initialize()

    # ── 公开接口 ──────────────────────────────────────────────────────

    def sync_entries(self, entries: Iterable[dict[str, Any]]) -> int:
        """全量同步记忆条目到索引（原始文本 + FTS5 分词索引）。

        先清空所有现有数据，再批量插入。

        Args:
            entries: 记忆条目列表，每项含 id/title/content/tags/source_type/updated_at。

        Returns:
            同步的条目数。
        """
        conn = self._get_connection()
        with self._lock:
            try:
                conn.execute("DELETE FROM memory_entries")
                conn.execute("DELETE FROM memory_fts")
                count = 0
                for entry in entries:
                    self._insert_entry(conn, entry)
                    count += 1
                conn.commit()
                logger.info("FTS5 index synced: %d entries", count)
                return count
            except Exception:
                conn.rollback()
                raise

    def add_entry(self, entry: dict[str, Any]) -> None:
        """添加单条记忆条目到索引。

        Args:
            entry: 记忆条目，至少含 id, title, content。
        """
        conn = self._get_connection()
        with self._lock:
            try:
                self._insert_entry(conn, entry)
                conn.commit()
            except Exception:
                conn.rollback()
                raise

    def update_entry(self, entry_id: str, entry: dict[str, Any]) -> None:
        """更新索引中的记忆条目。

        Args:
            entry_id: 条目 ID。
            entry: 更新的字段（title, content, tags 等）。
        """
        conn = self._get_connection()
        with self._lock:
            try:
                # Update content table (original text)
                fields = []
                values = []
                for key in ("title", "content", "tags", "source_type", "updated_at"):
                    if key in entry:
                        fields.append(f"{key} = ?")
                        values.append(str(entry[key] or ""))
                if fields:
                    values.append(entry_id)
                    sql = f"UPDATE memory_entries SET {', '.join(fields)} WHERE id = ?"
                    conn.execute(sql, values)

                    # Update FTS table (segmented text)
                    conn.execute(
                        "DELETE FROM memory_fts WHERE id = ?",
                        (entry_id,),
                    )
                    self._insert_fts(
                        conn, entry_id,
                        _segment(str(entry.get("title") or "")),
                        _segment(str(entry.get("content") or "")),
                        _segment(str(entry.get("tags") or "")),
                    )
                    conn.commit()
            except Exception:
                conn.rollback()
                raise

    def delete_entry(self, entry_id: str) -> None:
        """从索引中删除记忆条目。

        Args:
            entry_id: 条目 ID。
        """
        conn = self._get_connection()
        with self._lock:
            try:
                conn.execute("DELETE FROM memory_entries WHERE id = ?", (entry_id,))
                conn.execute("DELETE FROM memory_fts WHERE id = ?", (entry_id,))
                conn.commit()
            except Exception:
                conn.rollback()
                raise

    def search(
        self,
        query: str,
        top_k: int = 10,
    ) -> list[dict[str, Any]]:
        """FTS5 全文检索，按 BM25 相关性排序。

        查询文本会自动进行中文分词以匹配 FTS5 索引。

        Args:
            query: 搜索关键词（中英文均可）。
            top_k: 最多返回结果数。

        Returns:
            搜索结果列表，每项含 id, title, content, tags, source_type,
            updated_at, bm25_score (float, 0=无匹配)。
        """
        if not query or not query.strip():
            return []
        conn = self._get_connection()
        # Segment the query to match the indexed (segmented) text
        segmented_query = _segment(query.strip())
        if not segmented_query:
            return []
        cleaned = self._sanitize_fts5_query(segmented_query)
        if not cleaned:
            return []
        sql = """
            SELECT e.id, e.title, e.content, e.tags,
                   e.source_type, e.updated_at,
                   -bm25(memory_fts) AS bm25_score
            FROM memory_entries e
            JOIN memory_fts ON e.id = memory_fts.id
            WHERE memory_fts MATCH ?
            ORDER BY bm25_score DESC
            LIMIT ?
        """
        try:
            with self._lock:
                rows = conn.execute(sql, (cleaned, top_k)).fetchall()
        except sqlite3.OperationalError as exc:
            logger.info("FTS5 query failed (%s), trying simpler query", exc, exc_info=True)
            rows = self._fallback_search(conn, query, top_k)
        results = []
        for row in rows:
            results.append({
                "id": str(row[0] or ""),
                "title": str(row[1] or ""),
                "content": str(row[2] or ""),
                "tags": str(row[3] or ""),
                "source_type": str(row[4] or ""),
                "updated_at": str(row[5] or ""),
                "bm25_score": float(row[6] or 0),
            })
        return results

    def get_entry_count(self) -> int:
        """返回索引中的条目总数。"""
        conn = self._get_connection()
        with self._lock:
            row = conn.execute("SELECT COUNT(*) FROM memory_entries").fetchone()
            return row[0] if row else 0

    def close(self) -> None:
        """关闭数据库连接。"""
        with self._lock:
            if self._conn is not None:
                try:
                    self._conn.close()
                except Exception as exc:
                    logger.warning("error closing FTS5 connection: %s", exc, exc_info=True)
                self._conn = None

    def rebuild_fts_index(self) -> None:
        """重建 FTS5 索引。"""
        conn = self._get_connection()
        with self._lock:
            try:
                conn.execute("INSERT INTO memory_fts(memory_fts) VALUES('rebuild')")
                conn.commit()
                logger.info("FTS5 index rebuilt")
            except Exception:
                conn.rollback()
                raise

    # ── 内部实现 ──────────────────────────────────────────────────────

    def _initialize(self) -> None:
        """初始化数据库和表结构。"""
        conn = self._get_connection()
        with self._lock:
            conn.executescript(f"""
                PRAGMA journal_mode=WAL;
                PRAGMA synchronous=NORMAL;

                {self._CREATE_ENTRIES_TABLE};

                {self._CREATE_FTS_TABLE};
            """)
            conn.commit()
            logger.debug("FTS5 engine initialized at %s", self._db_path)

    def _get_connection(self) -> sqlite3.Connection:
        """获取或创建数据库连接（线程安全）。"""
        with self._lock:
            if self._conn is None:
                self._conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
                self._conn.execute("PRAGMA journal_mode=WAL")
            return self._conn

    @staticmethod
    def _insert_entry(conn: sqlite3.Connection, entry: dict[str, Any]) -> None:
        """插入单条条目到两个表：memory_entries（原始文本）和 memory_fts（分词文本）。"""
        tags_raw = entry.get("tags") or ""
        if isinstance(tags_raw, list):
            tags_raw = " ".join(str(t) for t in tags_raw if t)
        elif not isinstance(tags_raw, str):
            tags_raw = str(tags_raw)

        entry_id = str(entry.get("entry_id") or entry.get("id") or "")
        title = str(entry.get("title") or "")
        content = str(entry.get("content") or "")
        tags = tags_raw
        source_type = str(entry.get("source_type") or "")
        updated_at = str(entry.get("updated_at") or "")

        # Store original text in memory_entries (for display)
        conn.execute(
            """INSERT OR REPLACE INTO memory_entries
               (id, title, content, tags, source_type, updated_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (entry_id, title, content, tags, source_type, updated_at),
        )

        # Store segmented text in FTS5 (for search)
        LocalIndexEngine._insert_fts(
            conn, entry_id,
            _segment(title),
            _segment(content),
            _segment(tags),
        )

    @staticmethod
    def _insert_fts(
        conn: sqlite3.Connection,
        entry_id: str,
        segmented_title: str,
        segmented_content: str,
        segmented_tags: str,
    ) -> None:
        """插入分词文本到 FTS5 索引表（幂等：先删后插，避免重复行导致 BM25 失真）。"""
        conn.execute("DELETE FROM memory_fts WHERE id = ?", (entry_id,))
        conn.execute(
            """INSERT INTO memory_fts (id, title, content, tags)
               VALUES (?, ?, ?, ?)""",
            (entry_id, segmented_title, segmented_content, segmented_tags),
        )

    @staticmethod
    def _sanitize_fts5_query(query: str) -> str:
        """净化 FTS5 查询文本，移除特殊字符防止语法错误。

        FTS5 supports: AND, OR, NOT, NEAR, (, ), *, ", phrases
        The input is expected to be space-separated tokens (from jieba segmentation).
        """
        cleaned = re.sub(r'[_!@#$%^&={}\[\]|\\:;<>?,]', " ", query)
        cleaned = re.sub(r'["""]', "", cleaned)
        cleaned = " ".join(cleaned.split())
        tokens = [t for t in cleaned.split()
                  if t.upper() not in ("AND", "OR", "NOT", "") and not t.startswith("*")]
        if not tokens:
            return ""
        return " AND ".join(tokens[:8])

    @staticmethod
    def _fallback_search(
        conn: sqlite3.Connection,
        query: str,
        top_k: int,
    ) -> list[tuple[Any, ...]]:
        """FTS5 MATCH 失败时的简单 LIKE 回退搜索。"""
        terms = [t.strip().lower() for t in _segment(query).split() if t.strip()]
        if not terms:
            terms = [t.strip().lower() for t in query.split() if t.strip()]
        if not terms:
            return []
        conditions = []
        params: list[Any] = []
        for term in terms:
            pattern = f"%{term}%"
            conditions.append(
                "(LOWER(title) LIKE ? OR LOWER(content) LIKE ? OR LOWER(tags) LIKE ?)"
            )
            params.extend([pattern, pattern, pattern])
        sql = f"""
            SELECT id, title, content, tags, source_type, updated_at, 0.0 AS bm25_score
            FROM memory_entries
            WHERE {' AND '.join(conditions)}
            LIMIT ?
        """
        params.append(top_k)
        return conn.execute(sql, params).fetchall()
