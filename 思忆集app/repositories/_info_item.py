from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from typing import Any

from data._hot_tables import INFO_ITEMS_TB, table_exists


class LocalInfoItemRepository:
    """Local repository for collected information items.

    D22-5 读写路径改道：``info_items_tb`` 存在时全方法走表路径（行级
    read-modify-write，消灭 15.65MB 整值序列化）；表不存在时回退 kv 路径
    （迁移前生产行为不变，见 deliverables/refined/d22-schema-design.md §3）。
    kv_store 旧键 ``info_items`` 迁移后保留只读，作回滚锚。
    """

    STORAGE_KEY = "info_items"
    TABLE = INFO_ITEMS_TB
    _CONTENT_FIELDS = (
        "title",
        "title_zh",
        "summary",
        "summary_zh",
        "source",
        "source_label",
        "category",
        "score",
        "collected_at",
        "tags",
    )

    def __init__(self, data_store: Any) -> None:
        self.data_store = data_store

    # ── 表路径基础设施 ────────────────────────────────────────────────

    def _table_conn(self) -> sqlite3.Connection | None:
        """返回热点表连接；data_store 无 SQLite 底座或表未建时返回 None（走 kv）。"""
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
    def _id_str(item_id: "str | int") -> str:
        return str(item_id)

    def _fetch_raw(self, conn: sqlite3.Connection, item_id: "str | int") -> str | None:
        target = self._id_str(item_id)
        try:
            row = conn.execute(
                "SELECT raw_json FROM " + self.TABLE + " WHERE id = ? LIMIT 1",
                (int(target),),
            ).fetchone()
        except (ValueError, TypeError):
            row = None
        if row is None:
            row = conn.execute(
                "SELECT raw_json FROM " + self.TABLE + " WHERE CAST(id AS TEXT) = ? LIMIT 1",
                (target,),
            ).fetchone()
        return row[0] if row else None

    def _entry_columns(self, entry: dict[str, Any]) -> tuple:
        """条目 dict → 表列值元组（不含 raw_json；列序见 _INSERT_SQL）。"""
        tags = entry.get("tags")
        return (
            entry.get("id"),
            str(entry.get("url") or "") or None,
            entry.get("title"),
            entry.get("summary"),
            entry.get("summary_zh"),
            entry.get("category"),
            str(entry.get("source") or "") or None,
            json.dumps(tags if isinstance(tags, list) else [], ensure_ascii=False),
            1 if bool(entry.get("read")) else 0,
            1 if bool(entry.get("saved")) else 0,
            entry.get("created_at"),
            entry.get("updated_at"),
            entry.get("collected_at"),
        )

    _INSERT_SQL = (
        "INSERT INTO info_items_tb (id, url, title, summary, summary_zh, category,"
        " source, tags, read, saved, created_at, updated_at, collected_at, raw_json)"
        " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)"
    )
    _UPDATE_SQL = (
        "UPDATE info_items_tb SET url=?, title=?, summary=?, summary_zh=?, category=?,"
        " source=?, tags=?, read=?, saved=?, created_at=?, updated_at=?, collected_at=?,"
        " raw_json=? WHERE id=?"
    )

    def _write_entry(self, conn: sqlite3.Connection, entry: dict[str, Any], existed: bool) -> None:
        cols = self._entry_columns(entry)
        raw = json.dumps(entry, ensure_ascii=False)
        if existed:
            conn.execute(self._UPDATE_SQL, cols[1:] + (raw, cols[0]))
        else:
            conn.execute(self._INSERT_SQL, cols + (raw,))

    def _build_where(self, *, search, read, saved, source, category):
        clauses: list[str] = []
        params: list[Any] = []
        if read is not None:
            clauses.append("read = ?")
            params.append(1 if read else 0)
        if saved is not None:
            clauses.append("saved = ?")
            params.append(1 if saved else 0)
        if source is not None:
            clauses.append("source = ?")
            params.append(str(source))
        if category is not None:
            clauses.append("category = ?")
            params.append(str(category))
        query = str(search or "").strip().lower()
        if query:
            like = "%" + query.replace("%", r"\%").replace("_", r"\_") + "%"
            clauses.append(
                "(COALESCE(title,'') LIKE ? ESCAPE '\\'"
                " OR COALESCE(json_extract(raw_json,'$.title_zh'),'') LIKE ? ESCAPE '\\'"
                " OR COALESCE(summary,'') LIKE ? ESCAPE '\\'"
                " OR COALESCE(summary_zh,'') LIKE ? ESCAPE '\\'"
                " OR COALESCE(source,'') LIKE ? ESCAPE '\\')"
            )
            params.extend([like] * 5)
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        return where, params

    # ── 公共方法（表路径优先，kv 回退）───────────────────────────────

    def list_items(
        self,
        *,
        search: str = "",
        limit: int = 50,
        offset: int = 0,
        read: bool | None = None,
        saved: bool | None = None,
        source: str | None = None,
        category: str | None = None,
        sort_by: str = "collected_at",
        sort_desc: bool = True,
    ) -> tuple[list[dict[str, Any]], int]:
        conn = self._table_conn()
        if conn is None:
            return self._kv_list_items(
                search=search,
                limit=limit,
                offset=offset,
                read=read,
                saved=saved,
                source=source,
                category=category,
                sort_by=sort_by,
                sort_desc=sort_desc,
            )
        where, params = self._build_where(search=search, read=read, saved=saved, source=source, category=category)
        total_row = conn.execute("SELECT count(*) FROM " + self.TABLE + where, params).fetchone()
        total = int(total_row[0]) if total_row else 0
        if sort_by == "score":
            order = "CAST(COALESCE(json_extract(raw_json,'$.score'),0) AS REAL)"
        else:
            order = "COALESCE(NULLIF(collected_at,''), NULLIF(created_at,''), '')"
        order += " DESC" if sort_desc else " ASC"
        rows = conn.execute(
            "SELECT raw_json FROM " + self.TABLE + where + " ORDER BY " + order + " LIMIT ? OFFSET ?",
            params + [max(0, int(limit)), max(0, int(offset))],
        ).fetchall()
        items = []
        for row in rows:
            try:
                loaded = json.loads(row[0])
            except (json.JSONDecodeError, TypeError):
                continue
            if isinstance(loaded, dict):
                items.append(loaded)
        return items, total

    def _kv_list_items(self, *, search, limit, offset=0, read, saved, source, category, sort_by, sort_desc):
        items = self.data_store.load_json(self.STORAGE_KEY, []) if self.data_store else []
        if not isinstance(items, list):
            return [], 0

        query = str(search or "").strip().lower()
        filtered = [
            item
            for item in items
            if isinstance(item, dict)
            and (not query or any(query in str(item.get(field, "")).lower() for field in ("title", "title_zh", "summary", "summary_zh", "source")))
            and (read is None or bool(item.get("read", False)) is read)
            and (saved is None or bool(item.get("saved", False)) is saved)
            and (source is None or str(item.get("source", "")) == source)
            and (category is None or str(item.get("category", "")) == category)
        ]
        reverse = sort_desc
        if sort_by == "score":
            filtered.sort(key=lambda item: float(item.get("score", 0) or 0), reverse=reverse)
        else:
            filtered.sort(
                key=lambda item: str(item.get("collected_at") or item.get("created_at") or ""),
                reverse=reverse,
            )
        total = len(filtered)
        start = max(0, int(offset))
        return filtered[start : start + max(0, int(limit))], total

    def upsert_items(self, records: list[dict[str, Any]]) -> int:
        if self.data_store is None:
            return 0
        conn = self._table_conn()
        if conn is None:
            return self._kv_upsert_items(records)
        now = datetime.now(timezone.utc).isoformat()
        changed = 0
        conn.execute("BEGIN IMMEDIATE")
        try:
            for record in records:
                url = str(record.get("url") or "").strip()
                if not url:
                    continue
                row = conn.execute(
                    "SELECT raw_json FROM " + self.TABLE + " WHERE url = ? ORDER BY id LIMIT 1",
                    (url,),
                ).fetchone()
                existed = row is not None
                if existed:
                    try:
                        item = json.loads(row[0])
                    except (json.JSONDecodeError, TypeError):
                        item = {}
                    if not isinstance(item, dict):
                        item = {}
                else:
                    next_id = conn.execute("SELECT COALESCE(MAX(id), 0) + 1 FROM " + self.TABLE).fetchone()[0]
                    item = {
                        "id": int(next_id),
                        "url": url,
                        "created_at": now,
                        "read": False,
                        "saved": False,
                    }
                for field in self._CONTENT_FIELDS:
                    if field in record:
                        item[field] = record[field]
                item.setdefault("created_at", now)
                item["updated_at"] = now
                self._write_entry(conn, item, existed)
                changed += 1
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
        return changed

    def _kv_upsert_items(self, records: list[dict[str, Any]]) -> int:
        now = datetime.now(timezone.utc).isoformat()
        changed = [0]

        def _apply(items: Any) -> list[dict[str, Any]]:
            if not isinstance(items, list):
                items = []
            by_url = {str(item.get("url")): item for item in items if isinstance(item, dict) and item.get("url")}
            for record in records:
                url = str(record.get("url") or "").strip()
                if not url:
                    continue
                existing = by_url.get(url)
                if existing is None:
                    existing = {
                        "id": self.data_store.next_id(items),
                        "url": url,
                        "created_at": now,
                        "read": False,
                        "saved": False,
                    }
                    items.append(existing)
                    by_url[url] = existing
                for field in self._CONTENT_FIELDS:
                    if field in record:
                        existing[field] = record[field]
                existing.setdefault("created_at", now)
                existing["updated_at"] = now
                changed[0] += 1
            return items

        self.data_store.update_json(self.STORAGE_KEY, [], _apply)
        return changed[0]

    # ── C-40 单条 CRUD（表路径行级；kv 回退走 update_json 事务）──────

    def _rmw_one(
        self,
        item_id: "str | int",
        mutate: "Any",
    ) -> "dict[str, Any] | None":
        """单条 read-modify-write：表存在走单行事务，否则 kv ``update_json``。

        ``mutate(entry)`` 原地修改命中的条目；返回该条目修改后的最终状态，
        找不到 id（或 data_store 缺失）返回 None。id 匹配按字符串比较，兼容
        前端 str|int 两种形状。损坏隔离拒写语义沿用 _chat_session 先例
        （kv：LocalDataStore.update_json 容错三件套；表：单行事务读写）。
        """
        if self.data_store is None:
            return None
        conn = self._table_conn()
        if conn is not None:
            conn.execute("BEGIN IMMEDIATE")
            try:
                raw = self._fetch_raw(conn, item_id)
                if raw is None:
                    conn.execute("COMMIT")
                    return None
                entry = json.loads(raw)
                if not isinstance(entry, dict):
                    conn.execute("COMMIT")
                    return None
                mutate(entry)
                self._write_entry(conn, entry, True)
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise
            return entry

        outcome: dict[str, Any] = {}
        target = str(item_id)

        def _apply(items: Any) -> Any:
            if not isinstance(items, list):
                items = []
            for e in items:
                if isinstance(e, dict) and str(e.get("id")) == target:
                    mutate(e)
                    outcome["item"] = e
                    break
            return items

        self.data_store.update_json(self.STORAGE_KEY, [], _apply)
        return outcome.get("item")

    def get_item(self, item_id: "str | int") -> "dict[str, Any] | None":
        """按 id 取单条条目；未命中返回 None。"""
        if self.data_store is None:
            return None
        conn = self._table_conn()
        if conn is None:
            return self._rmw_one(item_id, lambda entry: None)
        raw = self._fetch_raw(conn, item_id)
        if raw is None:
            return None
        try:
            entry = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return None
        return entry if isinstance(entry, dict) else None

    def update_item(
        self,
        item_id: "str | int",
        patch: "dict[str, Any]",
    ) -> "dict[str, Any] | None":
        """按 id 合并补丁字段（read/saved/notes/tags 等）；未命中返回 None。"""
        if not isinstance(patch, dict) or not patch:
            return None
        return self._rmw_one(item_id, lambda entry: entry.update(patch))

    def toggle_read(self, item_id: "str | int") -> "dict[str, Any] | None":
        """翻转已读状态；返回翻转后的条目，未命中返回 None。"""

        def _flip(entry: "dict[str, Any]") -> None:
            entry["read"] = not bool(entry.get("read", False))

        return self._rmw_one(item_id, _flip)

    def toggle_saved(self, item_id: "str | int") -> "dict[str, Any] | None":
        """翻转收藏状态；返回翻转后的条目，未命中返回 None。"""

        def _flip(entry: "dict[str, Any]") -> None:
            entry["saved"] = not bool(entry.get("saved", False))

        return self._rmw_one(item_id, _flip)

    def get_sources(self) -> "list[str]":
        """source 维度去重列表（与 list_items 的 source 过滤字段同维，首现序）。"""
        if self.data_store is None:
            return []
        conn = self._table_conn()
        if conn is None:
            return self._kv_get_sources()
        rows = conn.execute(
            "SELECT source FROM " + self.TABLE + " WHERE source IS NOT NULL AND source != '' GROUP BY source ORDER BY MIN(rowid)"
        ).fetchall()
        return [str(row[0]) for row in rows]

    def _kv_get_sources(self) -> "list[str]":
        sources: list[str] = []

        def _collect(items: Any) -> Any:
            if not isinstance(items, list):
                return items
            for entry in items:
                if not isinstance(entry, dict):
                    continue
                src = str(entry.get("source", "") or "")
                if src and src not in sources:
                    sources.append(src)
            return items

        self.data_store.update_json(self.STORAGE_KEY, [], _collect)
        return sources

    def get_stats(self) -> dict[str, Any]:
        """资讯条目统计：总数 / 未读 / 收藏 / 按来源分布。

        供 ``info.items.stats`` RPC 调用（handler 返回 {"stats": ...}）。
        """
        conn = self._table_conn()
        if conn is None:
            return self._kv_get_stats()
        row = conn.execute("SELECT count(*), SUM(CASE WHEN read = 0 THEN 1 ELSE 0 END), COALESCE(SUM(saved), 0) FROM " + self.TABLE).fetchone()
        by_source: dict[str, int] = {}
        for label, count in conn.execute(
            "SELECT COALESCE(NULLIF(json_extract(raw_json,'$.source_label'),''),"
            " NULLIF(source,''), 'unknown') AS s, COUNT(*) FROM " + self.TABLE + " GROUP BY s ORDER BY MIN(rowid)"
        ).fetchall():
            by_source[str(label)] = int(count)
        return {
            "total": int(row[0]) if row else 0,
            "unread": int(row[1] or 0) if row else 0,
            "saved": int(row[2] or 0) if row else 0,
            "by_source": by_source,
        }

    def _kv_get_stats(self) -> dict[str, Any]:
        items = self.data_store.load_json(self.STORAGE_KEY, []) if self.data_store else []
        if not isinstance(items, list):
            items = []
        total = 0
        unread = 0
        saved = 0
        by_source: dict[str, int] = {}
        for item in items:
            if not isinstance(item, dict):
                continue
            total += 1
            if not item.get("read", False):
                unread += 1
            if item.get("saved", False):
                saved += 1
            source = str(item.get("source_label") or item.get("source") or "unknown")
            by_source[source] = by_source.get(source, 0) + 1
        return {
            "total": total,
            "unread": unread,
            "saved": saved,
            "by_source": by_source,
        }


__all__ = ["LocalInfoItemRepository"]
