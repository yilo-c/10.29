from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime, timezone
from typing import Any

from data._hot_tables import INBOX_ENTRIES_TB, table_exists

from ._protocols import *

logger = logging.getLogger("siyi.repositories")


class LocalPersonalInboxRepository:

    """本地动态收件箱仓储。

    D22-5 读写路径改道：``inbox_entries_tb`` 存在时走表路径（行级读写，
    :70 原 ``list_entries`` 惰性修补 ``save_json`` 裸写彻底消灭——归一化由
    迁移 coalesce + ``add_entry`` 默认值保证）；表不存在时回退 kv 路径
    （现行为不变）。kv 旧键 ``inbox_entries`` 保留只读回滚锚。
    """

    STORAGE_KEY = "inbox_entries"
    TABLE = INBOX_ENTRIES_TB

    def __init__(self, data_store):

        self.data_store = data_store

    # ── 表路径基础设施 ────────────────────────────────────────────────

    def _table_conn(self) -> sqlite3.Connection | None:
        if self.data_store is None:
            return None
        sqlite = getattr(self.data_store, "_sqlite", None)
        connect = getattr(sqlite, "_connect", None)
        if connect is None:
            return None
        try:
            conn = connect()
        except Exception:
            return None
        if not table_exists(conn, self.TABLE):
            return None
        return conn

    @staticmethod
    def _clean_tags(raw: Any) -> list[str]:
        return [str(tag).strip() for tag in (raw or []) if str(tag).strip()]

    def _entry_columns(self, entry: dict[str, Any]) -> tuple:
        return (
            entry.get("id"),
            entry.get("title"),
            entry.get("content"),
            str(entry.get("source") or ""),
            str(entry.get("status") or "new"),
            json.dumps(self._clean_tags(entry.get("tags")), ensure_ascii=False),
            json.dumps(
                entry.get("metadata") if isinstance(entry.get("metadata"), dict) else {},
                ensure_ascii=False,
            ),
            entry.get("kind"),
            entry.get("created_at"),
            entry.get("updated_at"),
        )

    _INSERT_SQL = (
        "INSERT INTO inbox_entries_tb (entry_id, title, content, source, status, tags,"
        " metadata, kind, created_at, updated_at, raw_json) VALUES (?,?,?,?,?,?,?,?,?,?,?)"
    )
    _UPDATE_SQL = (
        "UPDATE inbox_entries_tb SET title=?, content=?, source=?, status=?, tags=?,"
        " metadata=?, kind=?, created_at=?, updated_at=?, raw_json=? WHERE entry_id=?"
    )

    def _write_entry(self, conn: sqlite3.Connection, entry: dict[str, Any]) -> None:
        cols = self._entry_columns(entry)
        raw = json.dumps(entry, ensure_ascii=False)
        conn.execute(self._UPDATE_SQL, cols[1:] + (raw, cols[0]))

    def _fetch_raw(self, conn: sqlite3.Connection, entry_id: "str | int") -> str | None:
        target = str(entry_id)
        try:
            row = conn.execute(
                "SELECT raw_json FROM " + self.TABLE + " WHERE entry_id = ? LIMIT 1",
                (int(target),),
            ).fetchone()
        except (ValueError, TypeError):
            row = None
        if row is None:
            row = conn.execute(
                "SELECT raw_json FROM " + self.TABLE + " WHERE CAST(entry_id AS TEXT) = ? LIMIT 1",
                (target,),
            ).fetchone()
        return row[0] if row else None

    # ── 分页常量（handler 层与仓储层共用同一套安全边界）──
    DEFAULT_LIST_LIMIT = 50
    MAX_LIST_LIMIT = 200

    @classmethod
    def _normalize_page(
        cls, limit: int | None, offset: int, order: str
    ) -> tuple[int | None, int, str]:
        """归一化分页参数。

        limit=None 表示"不分页"（仅供内部全量路径使用，RPC 面不会传入）；
        limit<=0 回落到默认 50；超过 MAX_LIST_LIMIT 一律夹到 200。
        order 仅接受 asc/desc（大小写不敏感），非法值回落 asc。
        """
        if limit is None:
            norm_limit: int | None = None
        else:
            try:
                norm_limit = int(limit)
            except (TypeError, ValueError):
                norm_limit = cls.DEFAULT_LIST_LIMIT
            if norm_limit <= 0:
                norm_limit = cls.DEFAULT_LIST_LIMIT
            elif norm_limit > cls.MAX_LIST_LIMIT:
                norm_limit = cls.MAX_LIST_LIMIT
        try:
            norm_offset = max(0, int(offset or 0))
        except (TypeError, ValueError):
            norm_offset = 0
        norm_order = "desc" if str(order or "").strip().lower() == "desc" else "asc"
        return norm_limit, norm_offset, norm_order

    def _load_all_rows(
        self,
        conn: sqlite3.Connection,
        include_archived: bool,
        limit: int | None = None,
        offset: int = 0,
        order: str = "asc",
    ) -> list[dict[str, Any]]:
        sql = "SELECT raw_json FROM " + self.TABLE
        if not include_archived:
            sql += " WHERE status != 'archived'"
        sql += " ORDER BY entry_id " + ("DESC" if order == "desc" else "ASC")
        args: tuple = ()
        # 分页下推到 SQL：不再把全表 raw_json 读入内存再切片
        if limit is not None:
            sql += " LIMIT ? OFFSET ?"
            args = (limit, offset)
        out: list[dict[str, Any]] = []
        for row in conn.execute(sql, args).fetchall():
            try:
                entry = json.loads(row[0])
            except (json.JSONDecodeError, TypeError):
                continue
            if isinstance(entry, dict):
                out.append(entry)
        return out

    def count_entries(self, include_archived: bool = False) -> int:
        """返回条目总数（供分页可观测性：判断"还有下一页"）。"""
        conn = self._table_conn()
        if conn is not None:
            sql = "SELECT COUNT(*) FROM " + self.TABLE
            if not include_archived:
                sql += " WHERE status != 'archived'"
            try:
                return int(conn.execute(sql).fetchone()[0])
            except (TypeError, ValueError, sqlite3.Error):
                return 0
        if self.data_store is None:
            return 0
        entries = self.data_store.load_json("inbox_entries", []) or []
        if include_archived:
            return len(entries)
        return len([e for e in entries if isinstance(e, dict) and e.get("status") != "archived"])

    # ── 公共方法（表路径优先，kv 回退）───────────────────────────────

    def list_entries(
        self,
        include_archived: bool = False,
        limit: int | None = None,
        offset: int = 0,
        order: str = "asc",
    ) -> list[dict[str, Any]]:
        """列出收件箱条目（表路径优先，kv 回退）。

        limit/offset/order 为新增分页原语：默认 ``limit=None`` 保持历史行为
        （全量返回），RPC 面由 :mod:`backend.handlers._inbox_crud` 显式传
        ``limit=50`` 兜底，杜绝"单帧 2.3MB 撑爆客户端"。
        """
        norm_limit, norm_offset, norm_order = self._normalize_page(limit, offset, order)

        if self.data_store is None:

            return []

        conn = self._table_conn()
        if conn is not None:
            return self._load_all_rows(conn, include_archived, norm_limit, norm_offset, norm_order)

        entries = self.data_store.load_json("inbox_entries", [])

        changed = False

        max_id = max((entry.get("id", 0) or 0 for entry in entries), default=0)

        for entry in entries:

            if not entry.get("id"):

                max_id += 1
                entry["id"] = max_id
                changed = True

            if not entry.get("created_at"):

                entry["created_at"] = datetime.now(timezone.utc).isoformat()
                changed = True

            if not entry.get("updated_at"):

                entry["updated_at"] = entry.get("created_at")
                changed = True

            if not entry.get("status"):

                entry["status"] = "new"
                changed = True

            if not isinstance(entry.get("tags"), list):

                entry["tags"] = []
                changed = True

        if changed:

            self.data_store.update_json("inbox_entries", [], lambda current: entries)

        if not include_archived:

            entries = [entry for entry in entries if entry.get("status") != "archived"]

        if norm_order == "desc":
            entries = list(reversed(entries))
        if norm_limit is not None:
            entries = entries[norm_offset: norm_offset + norm_limit]
        elif norm_offset:
            entries = entries[norm_offset:]
        return entries



    def add_entry(self, entry_info: dict[str, Any]) -> int:

        if self.data_store is None:

            return 0

        now = datetime.now(timezone.utc).isoformat()

        entry = dict(entry_info)

        conn = self._table_conn()
        if conn is not None:
            new_id: int = 0
            conn.execute("BEGIN IMMEDIATE")
            try:
                new_id = int(
                    conn.execute(
                        "SELECT COALESCE(MAX(entry_id), 0) + 1 FROM " + self.TABLE
                    ).fetchone()[0]
                )
                entry["id"] = new_id
                entry.setdefault("title", "未命名收件")
                entry.setdefault("content", "")
                entry.setdefault("source", "manual")
                entry.setdefault("status", "new")
                entry["tags"] = self._clean_tags(entry.get("tags"))
                entry.setdefault("metadata", {})
                entry.setdefault("created_at", now)
                entry.setdefault("updated_at", now)
                cols = self._entry_columns(entry)
                conn.execute(self._INSERT_SQL, cols + (json.dumps(entry, ensure_ascii=False),))
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise
            return new_id

        new_id_box: list[int] = [0]



        def _apply(entries: Any) -> list[dict[str, Any]]:

            if not isinstance(entries, list):

                entries = []

            new_id_box[0] = self.data_store.next_id(entries)

            entry["id"] = new_id_box[0]

            entry.setdefault("title", "未命名收件")

            entry.setdefault("content", "")

            entry.setdefault("source", "manual")

            entry.setdefault("status", "new")

            entry["tags"] = self._clean_tags(entry.get("tags"))

            entry.setdefault("metadata", {})

            entry.setdefault("created_at", now)

            entry.setdefault("updated_at", now)

            entries.append(entry)

            return entries



        self.data_store.update_json("inbox_entries", [], _apply)

        return new_id_box[0]




    def update_entry(self, entry_id: int, new_info: dict[str, Any]) -> None:

        if self.data_store is None:

            return

        now = datetime.now(timezone.utc).isoformat()

        conn = self._table_conn()
        if conn is not None:
            conn.execute("BEGIN IMMEDIATE")
            try:
                raw = self._fetch_raw(conn, entry_id)
                if raw is not None:
                    try:
                        entry = json.loads(raw)
                    except (json.JSONDecodeError, TypeError):
                        entry = None
                    if isinstance(entry, dict):
                        entry.update(new_info)
                        if "tags" in entry:
                            entry["tags"] = self._clean_tags(entry.get("tags"))
                        entry["updated_at"] = now
                        if not entry.get("id"):
                            try:
                                entry["id"] = int(str(entry_id))
                            except (ValueError, TypeError):
                                pass
                        self._write_entry(conn, entry)
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise
            return

        def _apply(entries: Any) -> list[dict[str, Any]]:

            if not isinstance(entries, list):

                return []

            for entry in entries:

                if entry.get("id") != entry_id:

                    continue

                entry.update(new_info)

                if "tags" in entry:

                    entry["tags"] = self._clean_tags(entry.get("tags"))

                entry["updated_at"] = now

                break

            return entries



        self.data_store.update_json("inbox_entries", [], _apply)



    def get_entry(self, entry_id: int) -> dict[str, Any] | None:

        if self.data_store is None:
            return None

        conn = self._table_conn()
        if conn is not None:
            raw = self._fetch_raw(conn, entry_id)
            if raw is None:
                return None
            try:
                entry = json.loads(raw)
            except (json.JSONDecodeError, TypeError):
                return None
            return dict(entry) if isinstance(entry, dict) else None

        for entry in self.list_entries(include_archived=True):

            if entry.get("id") == entry_id:

                return dict(entry)

        return None


