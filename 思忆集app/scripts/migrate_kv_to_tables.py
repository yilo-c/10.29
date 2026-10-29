# -*- coding: utf-8 -*-
"""D22-4/D22-6 迁移脚本：kv_store 热点键 → info_items_tb / inbox_entries_tb。

用法（cwd 任意，路径自解析）：
    python scripts/migrate_kv_to_tables.py --data-dir <dir>            # 建表+迁移+全量比对
    python scripts/migrate_kv_to_tables.py --data-dir <dir> --rollback # 表 raw_json 反向重组回 kv
    python scripts/migrate_kv_to_tables.py --snapshot-check <src> <dst># C-3① 快照核对（文件数+总字节）

退出码：0=成功且比对 PASS；1=比对 FAIL/前置不满足。
幂等：迁移用 INSERT OR IGNORE（url / entry_id 冲突跳过），可安全重跑。
安全：本脚本对 --data-dir 指向的库做写操作；生产执行属 D22-6 🔒，
须人工批准并按 C-3 三步（快照→staging 演练→回滚演练）后进行。
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from data._hot_tables import ensure_hot_tables  # noqa: E402

DB_FILENAME = "local_storage.db"

_MIGRATE_INFO_ITEMS = """
INSERT OR IGNORE INTO info_items_tb
    (id, url, title, summary, summary_zh, category, source, tags,
     read, saved, created_at, updated_at, collected_at, raw_json)
SELECT
     json_extract(j.value, '$.id'),
     json_extract(j.value, '$.url'),
     json_extract(j.value, '$.title'),
     json_extract(j.value, '$.summary'),
     json_extract(j.value, '$.summary_zh'),
     json_extract(j.value, '$.category'),
     json_extract(j.value, '$.source'),
     coalesce(json_extract(j.value, '$.tags'), '[]'),
     coalesce(json_extract(j.value, '$.read'),  0),
     coalesce(json_extract(j.value, '$.saved'), 0),
     json_extract(j.value, '$.created_at'),
     json_extract(j.value, '$.updated_at'),
     json_extract(j.value, '$.collected_at'),
     j.value
FROM json_each((SELECT value FROM kv_store WHERE key = ?)) AS j
WHERE json_extract(j.value, '$.id') IS NOT NULL
"""

_MIGRATE_INBOX_ENTRIES = """
INSERT OR IGNORE INTO inbox_entries_tb
    (entry_id, title, content, source, status, tags, metadata, kind,
     created_at, updated_at, raw_json)
SELECT
     json_extract(j.value, '$.id'),
     json_extract(j.value, '$.title'),
     json_extract(j.value, '$.content'),
     json_extract(j.value, '$.source'),
     coalesce(json_extract(j.value, '$.status'), 'new'),
     coalesce(json_extract(j.value, '$.tags'), '[]'),
     coalesce(json_extract(j.value, '$.metadata'), '{}'),
     json_extract(j.value, '$.kind'),
     json_extract(j.value, '$.created_at'),
     json_extract(j.value, '$.updated_at'),
     j.value
FROM json_each((SELECT value FROM kv_store WHERE key = ?)) AS j
WHERE json_extract(j.value, '$.id') IS NOT NULL
"""

# (kv 键, 表名, 主键列, 迁移 SQL)
_SPECS = (
    ("info_items", "info_items_tb", "id", _MIGRATE_INFO_ITEMS),
    ("inbox_entries", "inbox_entries_tb", "entry_id", _MIGRATE_INBOX_ENTRIES),
)


def _load_kv_array(conn: sqlite3.Connection, key: str) -> list | None:
    row = conn.execute(
        "SELECT value FROM kv_store WHERE key = ? LIMIT 1", (key,)
    ).fetchone()
    if row is None:
        return None
    try:
        data = json.loads(row[0])
    except (json.JSONDecodeError, TypeError):
        return None
    return data if isinstance(data, list) else None


def _migrate(conn: sqlite3.Connection) -> None:
    ensure_hot_tables(conn)
    for key, _table, _pk, sql in _SPECS:
        conn.execute("BEGIN IMMEDIATE")
        try:
            conn.execute(sql, (key,))
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise


def _verify(conn: sqlite3.Connection) -> tuple[bool, list[str]]:
    """全量比对：源数组条数==表行数 且 逐条 json.loads(raw_json)==原条目 dict。"""
    lines: list[str] = []
    ok = True
    for key, table, pk, _sql in _SPECS:
        src = _load_kv_array(conn, key)
        if src is None:
            lines.append(f"{key}: 源键缺失或非 JSON 数组 → 跳过（视为 0 条）")
            src = []
        src_items = [it for it in src if isinstance(it, dict)]
        rows = conn.execute(
            f"SELECT {pk}, raw_json FROM {table}"  # noqa: S608 - 列名来自内部常量
        ).fetchall()
        if len(src_items) != len(rows):
            ok = False
            lines.append(f"{table}: 条数不符 源={len(src_items)} 表={len(rows)}")
            continue
        by_key = {str(r[0]): r[1] for r in rows}
        mismatches = 0
        for it in src_items:
            raw = by_key.get(str(it.get("id")))
            if raw is None:
                mismatches += 1
                continue
            try:
                if json.loads(raw) != it:
                    mismatches += 1
            except json.JSONDecodeError:
                mismatches += 1
        if mismatches:
            ok = False
            lines.append(f"{table}: 逐条深比较失败 {mismatches} 条")
        else:
            lines.append(f"{table}: 条数 {len(rows)} 逐条深比较 PASS")
    return ok, lines


def _rollback(conn: sqlite3.Connection) -> None:
    """按设计稿 §4：表 raw_json 反向重组（按主键序）回灌 kv_store。"""
    conn.execute("BEGIN IMMEDIATE")
    try:
        for key, table, pk, _sql in _SPECS:
            conn.execute(
                "UPDATE kv_store"
                " SET value = ("
                "     SELECT '[' || group_concat(raw_json, ',') || ']'"
                f"     FROM (SELECT raw_json FROM {table} ORDER BY {pk})"
                " ), updated_at = ?"
                " WHERE key = ?"
                "   AND (SELECT count(*) FROM " + table + ") > 0",
                (time.time(), key),
            )
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise


def _snapshot_check(src: str, dst: str) -> tuple[bool, list[str]]:
    def _stat(root: str) -> tuple[int, int]:
        files = 0
        total = 0
        for dirpath, dirnames, names in os.walk(root):
            # 剪掉 backups 子树：快照习惯性嵌在 data/backups/ 内，dst（数据目录）
            # 永远多出这棵子树 → 结构性 FAIL 假警报（2026-09-13 实测事故）。
            # 两侧对称排除，语义 = 「比较活数据，不比较备份」。
            dirnames[:] = [d for d in dirnames if d != "backups"]
            for n in names:
                p = os.path.join(dirpath, n)
                files += 1
                total += os.path.getsize(p)
        return files, total

    a = _stat(src)
    b = _stat(dst)
    ok = a == b
    return ok, [f"src files={a[0]} bytes={a[1]}", f"dst files={b[0]} bytes={b[1]}"]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", help="含 local_storage.db 的库目录")
    parser.add_argument("--rollback", action="store_true", help="表→kv 反向重组回滚")
    parser.add_argument("--snapshot-check", nargs=2, metavar=("SRC", "DST"))
    args = parser.parse_args(argv)

    if args.snapshot_check:
        ok, lines = _snapshot_check(*args.snapshot_check)
        for line in lines:
            print(line)
        print("SNAPSHOT", "PASS" if ok else "FAIL")
        return 0 if ok else 1

    if not args.data_dir:
        parser.error("--data-dir 必填（或使用 --snapshot-check）")
    db_path = os.path.join(args.data_dir, DB_FILENAME)
    if not os.path.exists(db_path):
        print(f"库不存在: {db_path}", file=sys.stderr)
        return 1

    conn = sqlite3.connect(db_path)
    try:
        if args.rollback:
            _rollback(conn)
            ok, lines = _verify(conn)
            print("ROLLBACK 完成，回灌后 kv↔表 比对：")
            for line in lines:
                print(" ", line)
            print("RESULT", "PASS" if ok else "FAIL")
            return 0 if ok else 1
        _migrate(conn)
        ok, lines = _verify(conn)
        for line in lines:
            print(line)
        print("MIGRATE", "PASS" if ok else "FAIL")
        return 0 if ok else 1
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
