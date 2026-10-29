"""D22 拆表热点结构表：DDL 单一事实源 + 存在性探测（设计稿见
``deliverables/refined/d22-schema-design.md``）。

- ``ensure_hot_tables(conn)``：幂等建表/索引（迁移脚本与测试共用）。
- ``table_exists(conn, name)``：repo 层据此选择表路径或 kv 回退路径。
表创建只发生在迁移脚本/显式测试，repo **不自动建表**，避免迁移前空表
劫持读写路径造成数据"消失"（部署时序见设计稿 §3 注记）。
"""
from __future__ import annotations

import sqlite3

INFO_ITEMS_TB = "info_items_tb"
INBOX_ENTRIES_TB = "inbox_entries_tb"
HOT_TABLES = (INFO_ITEMS_TB, INBOX_ENTRIES_TB)

_HOT_TABLES_DDL = """
CREATE TABLE IF NOT EXISTS info_items_tb (
    id           INTEGER PRIMARY KEY,
    url          TEXT,
    title        TEXT,
    summary      TEXT,
    summary_zh   TEXT,
    category     TEXT,
    source       TEXT,
    tags         TEXT,
    read         INTEGER NOT NULL DEFAULT 0,
    saved        INTEGER NOT NULL DEFAULT 0,
    created_at   TEXT,
    updated_at   TEXT,
    collected_at TEXT,
    raw_json     TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_info_items_collected ON info_items_tb(collected_at DESC);
CREATE INDEX IF NOT EXISTS idx_info_items_category  ON info_items_tb(category);
CREATE INDEX IF NOT EXISTS idx_info_items_source    ON info_items_tb(source);
CREATE TABLE IF NOT EXISTS inbox_entries_tb (
    entry_id   INTEGER PRIMARY KEY,
    title      TEXT,
    content    TEXT,
    source     TEXT,
    status     TEXT    NOT NULL DEFAULT 'new',
    tags       TEXT,
    metadata   TEXT,
    kind       TEXT,
    created_at TEXT,
    updated_at TEXT,
    raw_json   TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_inbox_status  ON inbox_entries_tb(status);
CREATE INDEX IF NOT EXISTS idx_inbox_created ON inbox_entries_tb(created_at DESC);
"""


def ensure_hot_tables(conn: sqlite3.Connection) -> None:
    conn.executescript(_HOT_TABLES_DDL)
    conn.commit()


def table_exists(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = ? AND name = ? LIMIT 1",
        ("table", name),
    ).fetchone()
    return row is not None


__all__ = [
    "INFO_ITEMS_TB",
    "INBOX_ENTRIES_TB",
    "HOT_TABLES",
    "ensure_hot_tables",
    "table_exists",
]
