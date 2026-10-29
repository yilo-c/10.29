"""原始信息条目仓储 — 个人知识体系底座 L2（统一信息捕获存储）。

所有来源（微信/AI 聊天、浏览器、剪贴板、OCR、语音、阅读、收件箱、URL 学习…）
的原始内容统一落库为 raw_item，带来源/敏感度标签，支持内容哈希去重。

敏感度分级（个人知识体系隐私核心）：
- personal  : 本地优先处理，可同步
- sensitive : 仅本地 AI 处理，禁止出本机（云同步/云端 AI）

表结构：
    raw_items (
        id TEXT PRIMARY KEY,
        source TEXT NOT NULL,          -- wechat/chatgpt/claude/kimi/markdown/browser/...
        kind TEXT NOT NULL DEFAULT 'text',
        sensitivity TEXT NOT NULL DEFAULT 'personal',  -- personal|sensitive
        content TEXT NOT NULL,
        meta TEXT NOT NULL DEFAULT '{}',               -- JSON: sender/title/ts/原文件名
        content_hash TEXT NOT NULL,                    -- sha256 去重
        created_at TEXT NOT NULL,                      -- ISO
        processed INTEGER NOT NULL DEFAULT 0           -- 已消化标记
    )
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger("siyi.repositories.raw_item")

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS raw_items (
    id TEXT PRIMARY KEY,
    source TEXT NOT NULL,
    kind TEXT NOT NULL DEFAULT 'text',
    sensitivity TEXT NOT NULL DEFAULT 'personal',
    content TEXT NOT NULL,
    meta TEXT NOT NULL DEFAULT '{}',
    content_hash TEXT NOT NULL,
    created_at TEXT NOT NULL,
    processed INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_raw_items_source ON raw_items(source);
CREATE INDEX IF NOT EXISTS idx_raw_items_sensitivity ON raw_items(sensitivity);
CREATE INDEX IF NOT EXISTS idx_raw_items_created ON raw_items(created_at);
CREATE UNIQUE INDEX IF NOT EXISTS idx_raw_items_hash ON raw_items(content_hash);
"""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _content_hash(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8", errors="ignore")).hexdigest()


class RawItemRepository:
    """SQLite 原始信息条目仓储（线程本地连接，参考 PersistentTaskQueue 设计）。"""

    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
        parent = os.path.dirname(db_path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        self._local = threading.local()
        self._all_conns: list[sqlite3.Connection] = []
        self._conns_lock = threading.Lock()
        with self._connect() as conn:
            conn.executescript(_SCHEMA_SQL)
            conn.commit()

    # ── 连接管理 ────────────────────────────────────────────

    def _connect(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(self.db_path, timeout=30)
            conn.row_factory = sqlite3.Row
            self._local.conn = conn
            with self._conns_lock:
                self._all_conns.append(conn)
        return conn

    def close(self) -> None:
        with self._conns_lock:
            for conn in self._all_conns:
                try:
                    conn.close()
                except Exception:
                    pass
            self._all_conns.clear()

    # ── 写入 ────────────────────────────────────────────────

    def add_one(
        self,
        content: str,
        source: str,
        *,
        kind: str = "text",
        sensitivity: str = "personal",
        meta: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        """添加一条原始条目；内容哈希已存在时返回 None（去重）。"""
        content = (content or "").strip()
        if not content:
            return None
        item_id = uuid.uuid4().hex[:16]
        h = _content_hash(content)
        now = _now_iso()
        with self._connect() as conn:
            exists = conn.execute(
                "SELECT id FROM raw_items WHERE content_hash=?", (h,)
            ).fetchone()
            if exists:
                return None
            conn.execute(
                "INSERT INTO raw_items (id, source, kind, sensitivity, content, meta, content_hash, created_at, processed) "
                "VALUES (?,?,?,?,?,?,?,?,0)",
                (item_id, source, kind, sensitivity, content,
                 json.dumps(meta or {}, ensure_ascii=False), h, now),
            )
            conn.commit()
        return self.get(item_id)

    def add_many(
        self,
        items: list[dict[str, Any]],
    ) -> int:
        """批量添加（自动去重），返回新增条数。"""
        added = 0
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                for item in items:
                    content = str(item.get("content") or "").strip()
                    if not content:
                        continue
                    h = _content_hash(content)
                    exists = conn.execute(
                        "SELECT id FROM raw_items WHERE content_hash=?", (h,)
                    ).fetchone()
                    if exists:
                        continue
                    item_id = uuid.uuid4().hex[:16]
                    conn.execute(
                        "INSERT INTO raw_items (id, source, kind, sensitivity, content, meta, content_hash, created_at, processed) "
                        "VALUES (?,?,?,?,?,?,?,?,0)",
                        (
                            item_id,
                            str(item.get("source") or "unknown"),
                            str(item.get("kind") or "text"),
                            str(item.get("sensitivity") or "personal"),
                            content,
                            json.dumps(item.get("meta") or {}, ensure_ascii=False),
                            h,
                            str(item.get("created_at") or _now_iso()),
                        ),
                    )
                    added += 1
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise
        return added

    # ── 查询 ────────────────────────────────────────────────

    def get(self, item_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM raw_items WHERE id=?", (item_id,)
            ).fetchone()
        return _row_to_dict(row) if row else None

    def list(
        self,
        *,
        source: str | None = None,
        sensitivity: str | None = None,
        limit: int = 100,
        processed: int | None = None,
        created_after: str | None = None,
    ) -> list[dict[str, Any]]:
        sql = "SELECT * FROM raw_items WHERE 1=1"
        params: list[Any] = []
        if source:
            sql += " AND source=?"
            params.append(source)
        if sensitivity:
            sql += " AND sensitivity=?"
            params.append(sensitivity)
        if processed is not None:
            sql += " AND processed=?"
            params.append(processed)
        if created_after:
            sql += " AND created_at >= ?"
            params.append(created_after)
        sql += " ORDER BY created_at DESC LIMIT ?"
        params.append(max(1, min(int(limit), 500)))
        with self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [_row_to_dict(r) for r in rows]

    def count(self) -> int:
        with self._connect() as conn:
            row = conn.execute("SELECT COUNT(*) AS c FROM raw_items").fetchone()
        return int(row["c"]) if row else 0

    def stats(self) -> dict[str, Any]:
        with self._connect() as conn:
            by_source = {
                r["source"]: r["c"]
                for r in conn.execute(
                    "SELECT source, COUNT(*) AS c FROM raw_items GROUP BY source"
                ).fetchall()
            }
            by_sens = {
                r["sensitivity"]: r["c"]
                for r in conn.execute(
                    "SELECT sensitivity, COUNT(*) AS c FROM raw_items GROUP BY sensitivity"
                ).fetchall()
            }
            processed = conn.execute(
                "SELECT COUNT(*) AS c FROM raw_items WHERE processed=1"
            ).fetchone()["c"]
        return {
            "total": sum(by_source.values()),
            "by_source": by_source,
            "by_sensitivity": by_sens,
            "processed": int(processed),
        }

    # ── 更新 ────────────────────────────────────────────────

    def set_sensitivity(self, item_id: str, sensitivity: str) -> bool:
        if sensitivity not in ("personal", "sensitive"):
            return False
        with self._connect() as conn:
            cur = conn.execute(
                "UPDATE raw_items SET sensitivity=? WHERE id=?", (sensitivity, item_id)
            )
            conn.commit()
        return cur.rowcount > 0

    def mark_processed(self, item_id: str) -> bool:
        with self._connect() as conn:
            cur = conn.execute(
                "UPDATE raw_items SET processed=1 WHERE id=?", (item_id,)
            )
            conn.commit()
        return cur.rowcount > 0

    def mark_processed_many(self, item_ids: list[str]) -> int:
        if not item_ids:
            return 0
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                cur = conn.executemany(
                    "UPDATE raw_items SET processed=1 WHERE id=?",
                    [(i,) for i in item_ids],
                )
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise
        return cur.rowcount


def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    d = dict(row)
    try:
        d["meta"] = json.loads(d.get("meta") or "{}")
    except (ValueError, TypeError):
        d["meta"] = {}
    return d
