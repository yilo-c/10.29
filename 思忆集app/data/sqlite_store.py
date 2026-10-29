"""SQLite KV 底座（P1-6 / P2-18）。

把 LocalDataStore 原本「一键一 JSON 文件 + .lock + .bak 轮转 + post-verify」的
重 IO 路径折叠到单个 SQLite WAL 数据库 + 一张 ``kv_store`` 表：

    CREATE TABLE kv_store (
        key            TEXT PRIMARY KEY,
        value          TEXT NOT NULL,         -- json.dumps(...) 文本
        updated_at     REAL NOT NULL,         -- time.time()
        schema_version INTEGER NOT NULL DEFAULT 0
    );

设计要点
========
1. **WAL + synchronous=NORMAL**：写性能远好于原 ``mkstemp -> fsync -> replace``，
   且保留崩溃原子性（WAL 日志 + 同步点足以满足思忆集场景）。
2. **线程本地连接**：sqlite3 的 Connection 不能跨线程共享；用 ``threading.local``
   懒分配。每个连接首次创建时跑一次 ``PRAGMA``。
3. **``update(key, default, updater)``** 在 ``BEGIN IMMEDIATE`` 事务里做
   read-modify-write，比原版的「文件锁 + load + write」原子性更强、单次往返：
   updater 抛异常自动回滚。
4. **``value`` 序列化**：交给上层；这里只存/取 ``str``。这样既不绑死 JSON，
   也避免对 ``Any`` 形状做隐式假设（与旧版 ``load_json`` / ``save_json``
   行为对齐——上层一直就是 json.dumps/json.loads）。
"""

from __future__ import annotations

import logging
import os
import sqlite3
import threading
import time
from collections.abc import Callable
from typing import Any

logger = logging.getLogger("siyi.sqlite_store")


_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS kv_store (
    key            TEXT PRIMARY KEY,
    value          TEXT NOT NULL,
    updated_at     REAL NOT NULL,
    schema_version INTEGER NOT NULL DEFAULT 0
);
-- P3.4 清理：idx_kv_store_updated_at 是死索引 — 全代码库无任何
-- WHERE updated_at / ORDER BY updated_at 针对该表的查询，所有访问都按 PK (key)。
-- 该索引白白消耗写入性能（每次 set/update 都需维护），且让 DB 体积膨胀。
-- DROP IF EXISTS 清理已存在的索引。
DROP INDEX IF EXISTS idx_kv_store_updated_at;
-- C-3 / V3-05：主 SQLite schema 演进最小框架。
-- schema_migrations 记录已应用的 schema 版本（升序），由 apply_migrations 维护。
CREATE TABLE IF NOT EXISTS schema_migrations (
    version    INTEGER PRIMARY KEY,
    applied_at INTEGER NOT NULL
);
"""


def _schema_baseline_v1(conn: sqlite3.Connection) -> None:
    """基线迁移 v1：kv_store 表结构已由 :data:`_SCHEMA_SQL` 幂等创建，此处占位。

    未来的结构性变更应以 >1 的独立迁移函数注册进 :data:`_MIGRATIONS`，
    :meth:`SqliteKVStore.apply_migrations` 会按版本号升序在事务内逐条应用并登记。
    """


# 迁移注册表：版本号升序执行。首个迁移登记当前 kv_store 基线版本号 1。
_MIGRATIONS: dict[int, Callable[[sqlite3.Connection], None]] = {
    1: _schema_baseline_v1,
}


class SqliteKVStore:
    """轻量级 SQLite KV 表封装，提供 get / set / update / delete / keys。"""

    def __init__(self, db_path: str, event_bus: Any = None) -> None:
        self.db_path = db_path
        parent = os.path.dirname(db_path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        # 每线程一份连接（sqlite3 默认禁止跨线程）
        self._local = threading.local()
        # 追踪所有已分配的连接，便于 close_all() 在 GC / 测试 teardown 时一并
        # 释放——避免 Windows 下持有 SQLite 句柄阻塞 tempdir 清理。
        self._all_conns: list[sqlite3.Connection] = []
        self._conns_lock = threading.Lock()
        # #15/#24: SQLite 锁等待采集（进程内，best-effort，零外部依赖）
        # 统计 BEGIN IMMEDIATE 获取写锁的等待耗时：次数、累计 ms、峰值 ms、最后时间。
        self._lock_wait = {
            "count": 0,
            "total_ms": 0.0,
            "peak_ms": 0.0,
            "last_ms": 0.0,
            "last_at": 0.0,
        }
        self._lock_wait_lock = threading.Lock()
        # #29: 锁等待自动告警
        # 单次等待超过该阈值（ms）视为「锁等待事件」并上报（同时计入上面的统计）。
        # 超过该倍数（相对告警阈值）视为更严重（severity down 线索）。
        self._lock_wait_threshold_ms = float(os.environ.get("SIYI_SQLITE_LOCK_WAIT_MS", "50"))
        # 两次告警之间的最小间隔（秒），避免重试风暴下刷屏。
        self._lock_wait_alert_cooldown = float(os.environ.get("SIYI_SQLITE_LOCK_ALERT_COOLDOWN", "60"))
        self._lock_wait_alert_last = 0.0
        self._event_bus = event_bus
        # 允许运行时注入 event_bus（某些构造路径无法在 __init__ 传入）。
        self.set_event_bus(event_bus)
        # 初始化 schema：使用一个临时连接（也会落到 _local，但这里显式建表）
        with self._connect() as conn:
            conn.executescript(_SCHEMA_SQL)
            conn.commit()
            # P3-1: 启动时完整性校验
            try:
                result = conn.execute("PRAGMA integrity_check").fetchone()
                if result and result[0] != "ok":
                    logger.warning("SQLite 完整性校验异常: %s", result[0])
                else:
                    logger.debug("SQLite 完整性校验通过")
                # 定期优化查询计划（SQLite 3.32+ 自动管理，但显式调用无害）
                conn.execute("PRAGMA optimize")
            except Exception as exc:
                logger.warning("SQLite 初始化校验失败（非致命）: %s", exc)
        # C-3 / V3-05：建库后执行未完成的 schema 迁移（首个基线版本登记）。
        self.apply_migrations()
        # 空间维护：freelist 超阈值时才 VACUUM，平时零开销顺带检查（见 maintenance_vacuum）。
        try:
            self.maintenance_vacuum()
        except Exception as exc:  # noqa: BLE001 - 维护失败不应阻断启动
            logger.warning("SQLite 启动空间维护失败（非致命）: %s", exc)

    # ── connection management ────────────────────────────────────────────

    # 锁等待判定阈值：BEGIN IMMEDIATE 超过该耗时（ms）视为一次「锁等待」事件。
    # 默认值可在构造时通过 SIYI_SQLITE_LOCK_WAIT_MS 覆盖；超阈值的 N 倍（见
    # _LOCK_WAIT_CRITICAL_MULTIPLE）视为严重（severity down 线索）。
    _LOCK_WAIT_CRITICAL_MULTIPLE = 10.0

    def set_event_bus(self, event_bus: Any) -> None:
        """运行时注入 EventBus（构造后也可设置；None 表示不上报）。"""
        self._event_bus = event_bus

    def _emit_lock_wait_alert(self, elapsed_ms: float) -> None:
        """冷却后向 EventBus 上报锁等待告警（best-effort）。"""
        if self._event_bus is None:
            return
        now = time.time()
        with self._lock_wait_lock:
            if now - self._lock_wait_alert_last < self._lock_wait_alert_cooldown:
                return
            self._lock_wait_alert_last = now
        critical = elapsed_ms >= self._lock_wait_threshold_ms * self._LOCK_WAIT_CRITICAL_MULTIPLE
        try:
            # B5（2026-08-26）：统一 AppEvent 单参契约
            from core.events import AppEvent

            self._event_bus.emit(
                AppEvent(
                    "sqlite.lock_wait",
                    payload={
                        "db_path": self.db_path,
                        "wait_ms": round(elapsed_ms, 1),
                        "threshold_ms": self._lock_wait_threshold_ms,
                        "critical": critical,
                        "message": (
                            f"SQLite 写锁等待 {elapsed_ms:.0f}ms（阈值 {self._lock_wait_threshold_ms:.0f}ms）"
                            + ("，疑似严重争用" if critical else "")
                        ),
                    },
                )
            )
        except Exception as exc:  # noqa: BLE001 - 告警失败不应影响事务
            logger.debug("sqlite.lock_wait emit failed: %s", exc)

    def _begin_immediate(self, conn: sqlite3.Connection) -> float:
        """执行 BEGIN IMMEDIATE 并采集锁等待耗时，返回本次等待毫秒数。

        返回值为本次获取写锁的耗时（正常情况 <1ms；高并发/锁争用时显著升高）。
        仅采集、不抛错；采集失败不影响事务本身。超过阈值时（带冷却）上报告警事件。
        """
        t0 = time.time()
        conn.execute("BEGIN IMMEDIATE")
        elapsed_ms = (time.time() - t0) * 1000.0
        threshold = self._lock_wait_threshold_ms
        if elapsed_ms >= threshold:
            with self._lock_wait_lock:
                s = self._lock_wait
                s["count"] += 1
                s["total_ms"] += elapsed_ms
                s["peak_ms"] = max(s["peak_ms"], elapsed_ms)
                s["last_ms"] = elapsed_ms
                s["last_at"] = time.time()
            logger.warning("SQLite 写锁等待 %.1fms（>=阈值 %.0fms）", elapsed_ms, threshold)
            self._emit_lock_wait_alert(elapsed_ms)
        return elapsed_ms

    def get_lock_wait_stats(self) -> dict[str, float]:
        """返回 SQLite 写锁等待统计快照（供 system.health 透出）。"""
        with self._lock_wait_lock:
            return dict(self._lock_wait)

    def _connect(self) -> sqlite3.Connection:
        """获取当前线程的连接，懒创建。"""
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(
                self.db_path,
                timeout=10.0,
                isolation_level=None,  # 我们自己控事务边界
                check_same_thread=True,
            )
            # WAL：多读单写并发；synchronous=NORMAL 在 WAL 模式下是安全的
            # （提交持久化到 WAL 即可，crash 时通过 wal 日志重放）
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            conn.execute("PRAGMA foreign_keys=ON")
            # 在 BEGIN IMMEDIATE 等待锁的最大毫秒数
            conn.execute("PRAGMA busy_timeout=10000")
            self._local.conn = conn
            with self._conns_lock:
                self._all_conns.append(conn)
        return conn

    def close(self) -> None:
        """关闭当前线程的连接（多线程场景下其它线程的连接由 GC 处理）。"""
        conn = getattr(self._local, "conn", None)
        if conn is not None:
            try:
                conn.close()
            except sqlite3.Error as exc:
                logger.debug("failed to close sqlite connection: %s", exc)
            with self._conns_lock:
                try:
                    self._all_conns.remove(conn)
                except ValueError:
                    pass
            self._local.conn = None

    def close_all(self) -> None:
        """关闭所有线程曾创建的连接（用于 LocalDataStore 销毁 / 测试 teardown）。

        Windows 下若 SQLite 句柄未释放会阻塞 ``shutil.rmtree`` 清理 tmp 目录。
        """
        with self._conns_lock:
            conns, self._all_conns = list(self._all_conns), []
        for conn in conns:
            try:
                conn.close()
            except sqlite3.Error as exc:
                logger.debug("failed to close sqlite connection (close_all): %s", exc)
        # 当前线程的引用也清掉，避免下次 _connect 时拿到僵尸连接
        if getattr(self._local, "conn", None) is not None:
            self._local.conn = None

    def __del__(self) -> None:  # pragma: no cover - GC 兜底
        try:
            self.close_all()
        except Exception:
            pass

    # ── read ────────────────────────────────────────────────────────────

    def get(self, key: str, default: str | None = None) -> str | None:
        """读取单个 key 的 ``value`` 文本；缺失返回 ``default``。"""
        cur = self._connect().execute(
            "SELECT value FROM kv_store WHERE key = ? LIMIT 1",
            (key,),
        )
        row = cur.fetchone()
        if row is None:
            return default
        return row[0]

    def has(self, key: str) -> bool:
        cur = self._connect().execute(
            "SELECT 1 FROM kv_store WHERE key = ? LIMIT 1",
            (key,),
        )
        return cur.fetchone() is not None

    def keys(self) -> list[str]:
        cur = self._connect().execute("SELECT key FROM kv_store")
        return [row[0] for row in cur.fetchall()]

    # ── write ───────────────────────────────────────────────────────────

    def set(self, key: str, value: str, *, schema_version: int = 0) -> None:
        """覆盖写入单个 key。"""
        now = time.time()
        conn = self._connect()
        self._begin_immediate(conn)
        try:
            conn.execute(
                """
                INSERT INTO kv_store(key, value, updated_at, schema_version)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET
                    value=excluded.value,
                    updated_at=excluded.updated_at,
                    schema_version=excluded.schema_version
                """,
                (key, value, now, schema_version),
            )
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise

    def update(
        self,
        key: str,
        default: str,
        updater: Callable[[str], str],
    ) -> str:
        """原子 read-modify-write。

        - 在 ``BEGIN IMMEDIATE`` 事务内取 ``current = get(key, default)``。
        - 调 ``updater(current) -> new_value``。
        - 写回 + COMMIT。
        - 如果 ``updater`` 抛异常，事务回滚，磁盘值保持。

        返回 ``updater`` 的结果。
        """
        conn = self._connect()
        self._begin_immediate(conn)
        try:
            cur = conn.execute(
                "SELECT value FROM kv_store WHERE key = ? LIMIT 1",
                (key,),
            )
            row = cur.fetchone()
            current = row[0] if row is not None else default
            new_value = updater(current)
            now = time.time()
            conn.execute(
                """
                INSERT INTO kv_store(key, value, updated_at, schema_version)
                VALUES (?, ?, ?, 0)
                ON CONFLICT(key) DO UPDATE SET
                    value=excluded.value,
                    updated_at=excluded.updated_at
                """,
                (key, new_value, now),
            )
            conn.execute("COMMIT")
            return new_value
        except Exception:
            try:
                conn.execute("ROLLBACK")
            except sqlite3.Error:
                pass
            raise

    def append_to_list(
        self,
        key: str,
        item: str,
        cap: int | None = None,
    ) -> int:
        """增量追加（T2.2 写放大治理）：在 SQLite 层对 JSON 数组做**原地 append**，
        避免 Python 端把整个 blob 反序列化→追加→再序列化重写。

        - 用 ``json_insert(value, '$[#]', json(?))`` 在数组末尾追加（SQLite 原生
          JSON 变更，无需 Python 端整体 re-dump）。
        - ``cap`` 非空且超长时，用 ``json_each`` + ``json_group_array`` 仅保留最后
          cap 项（按数组下标过滤，保留顺序）——同样在 SQL 层完成，不产生 Python
          级整体序列化。注意数组长度须用显式子查询取得，避免被 json_each 的
          元素 length（对象=0）误替。
        - 全程在 ``BEGIN IMMEDIATE`` 事务内，原子、可回滚。
        - 返回追加后数组的最终长度（便于上层校验）。

        注意：item 须为已 json.dumps 的字符串。调用方负责序列化单项，
        从而把"序列化单个 item"与"序列化整个列表"的成本彻底解耦——
        写放大从 O(列表长度) 降到 O(1) 级 SQL 操作。
        """
        conn = self._connect()
        self._begin_immediate(conn)
        try:
            row = conn.execute(
                "SELECT value FROM kv_store WHERE key = ? LIMIT 1",
                (key,),
            ).fetchone()
            if row is None:
                # 首条：直接构造单元素数组
                new_value = "[" + item + "]"
                now = time.time()
                conn.execute(
                    """
                    INSERT INTO kv_store(key, value, updated_at, schema_version)
                    VALUES (?, ?, ?, 0)
                    ON CONFLICT(key) DO UPDATE SET
                        value=excluded.value,
                        updated_at=excluded.updated_at
                    """,
                    (key, new_value, now),
                )
                conn.execute("COMMIT")
                return 1
            # 已存在该行：原地追加到数组末尾
            new_value = "SELECT json_insert(value, '$[#]', json(?)) FROM kv_store WHERE key = ?"
            # 用参数化执行更新（json_insert 接受 JSON 文本参数）
            conn.execute(
                "UPDATE kv_store SET value = json_insert(value, '$[#]', json(?)), updated_at = ? WHERE key = ?",
                (item, time.time(), key),
            )
            if cap is not None and cap > 0:
                # 超长则只保留最后 cap 项（按数组下标过滤，保留顺序）。
                # 关键：json_each(value) 内 value 指"元素"，故数组长度必须用
                # 显式子查询 (SELECT json_array_length(value) FROM kv_store WHERE key=?)
                # 取得，避免被元素的 json_array_length(=0) 误替。全用关联子查询消除歧义。
                conn.execute(
                    """
                    UPDATE kv_store
                    SET value = (
                        SELECT json_group_array(v)
                        FROM (
                            SELECT value AS v, CAST(key AS INTEGER) AS idx
                            FROM json_each(
                                (SELECT value FROM kv_store WHERE key = ?)
                            )
                        )
                        WHERE idx >= (
                            (SELECT json_array_length(value) FROM kv_store WHERE key = ?)
                            - ?
                        )
                    )
                    WHERE key = ?
                    """,
                    (key, key, cap, key),
                )
            final_len = conn.execute(
                "SELECT json_array_length(value) FROM kv_store WHERE key = ?",
                (key,),
            ).fetchone()[0]
            conn.execute("COMMIT")
            return int(final_len)
        except Exception:
            try:
                conn.execute("ROLLBACK")
            except sqlite3.Error:
                pass
            raise

    def delete(self, key: str) -> None:
        conn = self._connect()
        self._begin_immediate(conn)
        try:
            conn.execute("DELETE FROM kv_store WHERE key = ?", (key,))
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise

    def set_many(self, items: dict[str, str]) -> None:
        """批量写入（用于首启迁移：把 N 个 JSON 文件一次性灌进 SQLite）。"""
        if not items:
            return
        now = time.time()
        rows = [(k, v, now, 0) for k, v in items.items()]
        conn = self._connect()
        self._begin_immediate(conn)
        try:
            conn.executemany(
                """
                INSERT INTO kv_store(key, value, updated_at, schema_version)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET
                    value=excluded.value,
                    updated_at=excluded.updated_at
                """,
                rows,
            )
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise

    # ── schema 演进（C-3 / V3-05）───────────────────────────────────────

    def current_schema_version(self) -> int:
        """返回当前已应用的最高 schema 版本；无任何迁移记录时返回 0。"""
        row = self._connect().execute("SELECT COALESCE(MAX(version), 0) FROM schema_migrations").fetchone()
        return int(row[0])

    def apply_migrations(self) -> int:
        """按版本号升序应用未执行的 schema 迁移，返回本次新应用的迁移数。

        当前仅登记基线版本 1（kv_store 初版）。后续结构性变更在此注册更高
        版本号即可；方法在 ``BEGIN IMMEDIATE`` 事务内逐条应用并写入登记。
        """
        conn = self._connect()
        current = self.current_schema_version()
        applied = 0
        for version in sorted(_MIGRATIONS):
            if version <= current:
                continue
            self._begin_immediate(conn)
            try:
                _MIGRATIONS[version](conn)
                conn.execute(
                    "INSERT OR IGNORE INTO schema_migrations(version, applied_at) VALUES (?, ?)",
                    (version, int(time.time())),
                )
                conn.execute("COMMIT")
            except Exception:
                try:
                    conn.execute("ROLLBACK")
                except sqlite3.Error:
                    pass
                raise
            applied += 1
        return applied

    # ── 空间维护 ──────────────────────────────────────────────────
    def maintenance_vacuum(self, threshold_pct: float = 0.25, min_reclaim_mb: float = 20.0) -> bool:
        """启动/定期空间维护：仅当 freelist（空闲页）占比足够高才执行完整 VACUUM。

        WAL 模式下频繁增删大 blob（如 ``info_items``）会让 DB 文件堆积大量
        未回收页。完整 VACUUM 会重建整个数据库文件从而回收空间，但成本高，
        因此用阈值门控：只有当“可回收空间 ≥ min_reclaim_mb 且占比 ≥ threshold_pct”
        才执行，否则零开销跳过。返回是否执行了 VACUUM。

        注意：调用方不得处于事务中（VACUUM 不能在事务内执行）。本 store 的
        连接使用 ``isolation_level=None``（自动提交），在方法内直接 VACUUM 安全。
        """
        try:
            conn = self._connect()
            page_size = int(conn.execute("PRAGMA page_size").fetchone()[0])
            page_count = int(conn.execute("PRAGMA page_count").fetchone()[0])
            freelist = int(conn.execute("PRAGMA freelist_count").fetchone()[0])
            if page_count <= 0:
                return False
            total_mb = page_count * page_size / 1048576.0
            reclaim_mb = freelist * page_size / 1048576.0
            pct = freelist / page_count
            if reclaim_mb >= min_reclaim_mb and pct >= threshold_pct:
                logger.info(
                    "SQLite VACUUM 回收 %.1f MB（freelist 占 %.1f%%，文件 %.1f MB）",
                    reclaim_mb, pct * 100.0, total_mb,
                )
                conn.execute("VACUUM")
                return True
            return False
        except Exception as exc:  # noqa: BLE001 - 维护失败不应阻断启动
            logger.warning("SQLite 空间维护跳过（非致命）: %s", exc)
            return False

    # ── introspection ───────────────────────────────────────────────────

    def row_count(self) -> int:
        cur = self._connect().execute("SELECT COUNT(*) FROM kv_store")
        return int(cur.fetchone()[0])

    def __repr__(self) -> str:
        return f"SqliteKVStore(db_path={self.db_path!r}, rows={self.row_count()})"
