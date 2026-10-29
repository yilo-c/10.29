"""统一审计写入器（T1.1 根治写路径）。

设计要点
========
* 后端是**独立 SQLite 文件** ``data/audit_trail.db``，与 ``local_storage.db``
  物理隔离，从根上消除 notes 写与审计写共享同一把 SQLite 写锁导致的
  ``database is locked`` 竞争（P0-1 止血点）。
* 采用单写线程 + ``queue.Queue`` 的写入模型：写入路径只做 ``put``，
  不再有「整列表读出 → append → 整列表写回」的读改写，因此不存在锁竞争、
  也不存在整列表 ``json.dumps`` 膨胀。
* 两张表分别承载两类审计：
  - ``audit_trail``：AuditTrail 的主轨迹（写意图/完成/失败）。
  - ``integration_audit``：integration_hub 的集成审计日志。
* 崩溃安全：WAL + ``synchronous=NORMAL`` 保证已 commit 的条目落盘；
  进程退出时经 ``atexit`` 及 SIGTERM/SIGINT 处理器做一次尽力 flush。
* 内存合并：队列中尚未落盘的条目经 ``pending_entries()`` 暴露，供回滚查询
  合并（刚完成的写必须立即可回滚）。
"""

from __future__ import annotations

import atexit
import json
import logging
import os
import queue
import signal
import sqlite3
import threading
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger("siyi.audit_writer")

DB_FILENAME = "audit_trail.db"
QUEUE_MAXSIZE = 10000
QUEUE_WARN_RATIO = 0.8  # 队列深度超 80% 上报告警
TRAIL_KEEP = 400  # audit_trail 表保留最近条数（对应 AuditTrail.MAX_TRAIL_SIZE 语义）
INTEGRATION_KEEP = 5000  # integration_audit 表保留最近条数（对应 ApiIntegrationHub.MAX_AUDIT_LOGS）
PRUNE_INTERVAL_SECONDS = 600  # 后台清理线程间隔


class AuditWriter:
    """单写线程消费队列的审计写入器（独立 SQLite 库）。"""

    def __init__(self, data_dir: str, observability_service: Any | None = None) -> None:
        os.makedirs(data_dir, exist_ok=True)
        self.data_dir = data_dir
        self.db_path = os.path.join(data_dir, DB_FILENAME)
        self.observability_service = observability_service

        self._queue: "queue.Queue[dict[str, Any]]" = queue.Queue(maxsize=QUEUE_MAXSIZE)
        self._stop_event = threading.Event()
        self._conn: sqlite3.Connection | None = None
        self._lock = threading.Lock()  # 保护 _conn 与 pending 字典
        # 内存待刷盘条目：entry_id -> 归一化后的 dict（仅 audit_trail 需要合并）
        self._pending: dict[str, dict[str, Any]] = {}
        # 集成日志的内存态（仅用于查询可见性，落盘即从这里移除）
        self._pending_integration: list[dict[str, Any]] = []
        self._warned_full = False

        self._init_db()
        self._migrate_legacy()  # 存量迁移：kv_store 旧键 → 新表

        self._writer_thread = threading.Thread(target=self._run, name="audit-writer", daemon=True)
        self._writer_thread.start()

        # 注册退出 flush（仅注册一次）
        self._register_exit_handlers()

        # 后台清理线程：物理删除超龄行，保持「保留最近 N 条」语义
        self._start_prune_thread()

    # ── 初始化 ──

    def _connect(self) -> sqlite3.Connection:
        # 写线程用独占长连接；查询用短连接。WAL 下读写互不阻塞，busy_timeout 仅作兜底。
        conn = sqlite3.connect(self.db_path, timeout=10.0, check_same_thread=False)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA busy_timeout=5000")
        return conn

    def _init_db(self) -> None:
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS audit_trail (
                        entry_id TEXT PRIMARY KEY,
                        ts REAL,
                        actor TEXT,
                        action_id TEXT,
                        target_type TEXT,
                        target_id TEXT,
                        operation TEXT,
                        status TEXT,
                        before_json TEXT,
                        after_json TEXT,
                        error TEXT,
                        metadata_json TEXT
                    )
                    """
                )
                conn.execute("CREATE INDEX IF NOT EXISTS idx_audit_trail_ts ON audit_trail(ts)")
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS integration_audit (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        provider TEXT,
                        event TEXT,
                        status TEXT,
                        payload_json TEXT,
                        time TEXT
                    )
                    """
                )
                conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_integration_audit_time ON integration_audit(time)"
                )
                conn.commit()
            finally:
                conn.close()

    # ── 存量迁移（幂等、可重入）──

    def _migrate_legacy(self) -> None:
        """把 kv_store 中的旧键 ``audit_trail`` / ``integration_audit_logs``
        一次性导入新表并删除旧键。若已迁移过（旧键不存在）则跳过。"""
        data_store = _legacy_data_store(self.data_dir)
        if data_store is None:
            return
        try:
            old_trail = data_store.load_json("audit_trail", None)
            if isinstance(old_trail, list) and old_trail:
                self._import_trail_list(old_trail)
                data_store.save_json("audit_trail", [])  # 标记已迁移（清空旧键内容）
                logger.info("migrated %d legacy audit_trail entries", len(old_trail))

            old_logs = data_store.load_json("integration_audit_logs", None)
            if isinstance(old_logs, list) and old_logs:
                self._import_integration_list(old_logs)
                data_store.save_json("integration_audit_logs", [])
                logger.info("migrated %d legacy integration_audit entries", len(old_logs))
        except Exception:
            logger.warning("legacy audit migration skipped (non-fatal)", exc_info=True)

    def _import_trail_list(self, trail: list[dict[str, Any]]) -> None:
        rows = []
        for e in trail:
            if not isinstance(e, dict):
                continue
            ts = _iso_to_ts(e.get("timestamp"))
            rows.append(
                (
                    str(e.get("entry_id", "")),
                    ts,
                    str(e.get("actor", "")),
                    str(e.get("action_id", "")),
                    str(e.get("target_type", "")),
                    str(e.get("target_id", "")),
                    str(e.get("operation", "write")),
                    str(e.get("status", "")),
                    _json_or_none(e.get("before_snapshot")),
                    _json_or_none(e.get("after_snapshot")),
                    str(e.get("error", "")) if e.get("error") else None,
                    _json_or_none(e.get("metadata")),
                )
            )
        if not rows:
            return
        with self._lock:
            conn = self._connect()
            try:
                conn.executemany(
                    "INSERT OR IGNORE INTO audit_trail "
                    "(entry_id, ts, actor, action_id, target_type, target_id, "
                    " operation, status, before_json, after_json, error, metadata_json) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                    rows,
                )
                conn.commit()
            finally:
                conn.close()

    def _import_integration_list(self, logs: list[dict[str, Any]]) -> None:
        rows = []
        for item in logs:
            if not isinstance(item, dict):
                continue
            rows.append(
                (
                    str(item.get("provider", "")),
                    str(item.get("event", "")),
                    str(item.get("status", "")),
                    _json_or_none(item.get("payload")),
                    str(item.get("time", "")),
                )
            )
        if not rows:
            return
        with self._lock:
            conn = self._connect()
            try:
                conn.executemany(
                    "INSERT INTO integration_audit (provider, event, status, payload_json, time) "
                    "VALUES (?,?,?,?,?)",
                    rows,
                )
                conn.commit()
            finally:
                conn.close()

    # ── 公共写入 API ──

    def submit_intent(self, entry_dict: dict[str, Any]) -> None:
        """提交一条写意图（INSERT 新行）。"""
        self._enqueue({"op": "intent", "entry": entry_dict})

    def submit_status(
        self,
        entry_id: str,
        status: str,
        after_snapshot: Any = None,
        error: str | None = None,
    ) -> None:
        """提交一条状态更新（UPDATE 已存在的行）。"""
        self._enqueue(
            {
                "op": "status",
                "entry_id": entry_id,
                "status": status,
                "after_json": _json_or_none(after_snapshot),
                "error": error,
            }
        )

    def submit_integration(self, provider: str, event: str, status: str, payload: Any) -> None:
        """提交一条集成审计日志。"""
        self._enqueue(
            {
                "op": "integration",
                "provider": provider,
                "event": event,
                "status": status,
                "payload_json": _json_or_none(payload),
                "time": datetime.now(timezone.utc).isoformat(),
            }
        )

    def _enqueue(self, item: dict[str, Any]) -> None:
        try:
            self._queue.put_nowait(item)
        except queue.Full:
            # 队列满：尽力而为，丢弃并告警（不阻塞业务写路径）
            self._report_queue_full()
            try:
                self._queue.get_nowait()
                self._queue.put_nowait(item)
            except Exception:
                logger.warning("audit queue full, dropping item op=%s", item.get("op"))
                return
        # 内存合并：intent 入 pending；status 命中则就地更新
        if item["op"] == "intent":
            entry = item["entry"]
            eid = str(entry.get("entry_id", ""))
            if eid:
                with self._lock:
                    self._pending[eid] = entry
        elif item["op"] == "integration":
            # 记录内存态，落盘后移除，保证查询可见性
            item = dict(item)
            item["_key"] = (item.get("provider", ""), item.get("event", ""), item.get("time", ""))
            with self._lock:
                self._pending_integration.append(item)
        elif item["op"] == "status":
            eid = str(item["entry_id"])
            with self._lock:
                pe = self._pending.get(eid)
                if pe is not None:
                    pe = dict(pe)
                    pe["status"] = item["status"]
                    if item.get("after_json") is not None:
                        try:
                            pe["after_snapshot"] = json.loads(item["after_json"])
                        except Exception:
                            pass
                    if item.get("error"):
                        pe["error"] = item["error"]
                    self._pending[eid] = pe

    # ── 内存合并：供回滚查询可见未落盘条目 ──

    def pending_entries(self) -> dict[str, dict[str, Any]]:
        """返回当前在队列中尚未落盘的审计条目（深拷贝，避免外部篡改）。"""
        with self._lock:
            return {k: dict(v) for k, v in self._pending.items()}

    def pending_integration(self) -> list[dict[str, Any]]:
        """返回尚未落盘的集成审计日志（仅内存态，落盘后不再保留）。"""
        with self._lock:
            return [
                {k: v for k, v in item.items() if k != "_key"}
                for item in self._pending_integration
            ]

    # ── 查询 API（读路径走 SQL，未落盘部分由调用方合并 pending_*）──

    def fetch_entries(self, action_id: str = "", status: str = "", limit: int = TRAIL_KEEP * 2) -> list[dict[str, Any]]:
        """查询 audit_trail，返回升序条目（最近的在后，兼容 AuditTrail 旧读路径）。"""
        rows = self._query_audit_rows(action_id=action_id, status=status, limit=limit)
        return [self._row_to_entry(r) for r in rows]

    def fetch_entry(self, entry_id: str) -> dict[str, Any] | None:
        """按 entry_id 查询单条（先查内存 pending，命中直接返回最新状态）。"""
        key = str(entry_id)
        with self._lock:
            pe = self._pending.get(key)
            if pe is not None:
                return dict(pe)
        conn = self._connect()
        try:
            cur = conn.execute(
                "SELECT entry_id, ts, actor, action_id, target_type, target_id, operation, status, "
                "before_json, after_json, error, metadata_json FROM audit_trail WHERE entry_id=?",
                (key,),
            )
            row = cur.fetchone()
        finally:
            conn.close()
        return self._row_to_entry(row) if row else None

    def fetch_integration(self, provider: str = "", limit: int = INTEGRATION_KEEP) -> list[dict[str, Any]]:
        """查询集成审计日志（按 time 倒序，最近的在前）。provider 传空串查全部。"""
        provider_key = str(provider or "").strip().lower()
        conn = self._connect()
        try:
            if provider_key:
                cur = conn.execute(
                    "SELECT provider, event, status, payload_json, time FROM integration_audit "
                    "WHERE provider=? ORDER BY time DESC, rowid DESC LIMIT ?",
                    (provider_key, int(limit)),
                )
            else:
                cur = conn.execute(
                    "SELECT provider, event, status, payload_json, time FROM integration_audit "
                    "ORDER BY time DESC, rowid DESC LIMIT ?",
                    (int(limit),),
                )
            rows = cur.fetchall()
        finally:
            conn.close()
        return [
            {
                "provider": r[0],
                "event": r[1],
                "status": r[2],
                "payload": _json_load(r[3]),
                "time": r[4],
            }
            for r in rows
        ]

    def _query_audit_rows(self, action_id: str = "", status: str = "", limit: int = TRAIL_KEEP * 2) -> list[tuple]:
        conn = self._connect()
        try:
            sql = (
                "SELECT entry_id, ts, actor, action_id, target_type, target_id, operation, status, "
                "before_json, after_json, error, metadata_json FROM audit_trail"
            )
            conds, params = [], []
            if action_id:
                conds.append("action_id = ?")
                params.append(action_id)
            if status:
                conds.append("status = ?")
                params.append(status)
            if conds:
                sql += " WHERE " + " AND ".join(conds)
            sql += " ORDER BY ts ASC, rowid ASC"
            rows = conn.execute(sql, params).fetchall()
        finally:
            conn.close()
        if limit and len(rows) > int(limit):
            rows = rows[-int(limit):]
        return rows

    @staticmethod
    def _row_to_entry(row: tuple) -> dict[str, Any]:
        entry_id, ts, actor, action_id, target_type, target_id, operation, status, before_json, after_json, error, metadata_json = row
        entry: dict[str, Any] = {
            "entry_id": entry_id,
            "timestamp": _ts_to_iso(ts),
            "actor": actor or "",
            "action_id": action_id or "",
            "target_type": target_type or "",
            "target_id": target_id or "",
            "operation": operation or "",
            "status": status or "",
            "before_snapshot": _json_load(before_json),
            "after_snapshot": _json_load(after_json),
            "metadata": _json_load(metadata_json) or {},
        }
        if error:
            entry["error"] = error
        return entry

    # ── 写线程 ──

    def _run(self) -> None:
        # 写线程专用长连接：整个生命周期复用，避免每条都 connect/close 的反复加锁竞争。
        conn = self._connect()
        try:
            while not self._stop_event.is_set() or not self._queue.empty():
                try:
                    item = self._queue.get(timeout=0.5)
                except queue.Empty:
                    continue
                try:
                    self._process(conn, item)
                except Exception:
                    logger.warning("audit writer process failed op=%s", item.get("op"), exc_info=True)
                finally:
                    self._queue.task_done()
        finally:
            try:
                conn.close()
            except Exception:
                pass

    def _process(self, conn: sqlite3.Connection, item: dict[str, Any]) -> None:
        op = item["op"]
        # 注意：SQLite I/O 不在 self._lock 保护下进行，避免长事务阻塞业务线程的 pending 更新。
        if op == "intent":
            e = item["entry"]
            conn.execute(
                "INSERT OR REPLACE INTO audit_trail "
                "(entry_id, ts, actor, action_id, target_type, target_id, "
                " operation, status, before_json, after_json, error, metadata_json) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    str(e.get("entry_id", "")),
                    _iso_to_ts(e.get("ts", e.get("timestamp", _now_ts()))),
                    str(e.get("actor", "")),
                    str(e.get("action_id", "")),
                    str(e.get("target_type", "")),
                    str(e.get("target_id", "")),
                    str(e.get("operation", "write")),
                    str(e.get("status", "intent")),
                    _json_or_none(e.get("before_snapshot")),
                    _json_or_none(e.get("after_snapshot")),
                    str(e.get("error", "")) if e.get("error") else None,
                    _json_or_none(e.get("metadata")),
                ),
            )
            conn.commit()
            with self._lock:
                self._pending.pop(str(e.get("entry_id", "")), None)
        elif op == "status":
            conn.execute(
                "UPDATE audit_trail SET status=?, after_json=COALESCE(?, after_json), "
                "error=? WHERE entry_id=?",
                (
                    str(item["status"]),
                    item.get("after_json"),
                    item.get("error") or None,
                    str(item["entry_id"]),
                ),
            )
            conn.commit()
            with self._lock:
                self._pending.pop(str(item["entry_id"]), None)
        elif op == "integration":
            conn.execute(
                "INSERT INTO integration_audit (provider, event, status, payload_json, time) "
                "VALUES (?,?,?,?,?)",
                (
                    str(item.get("provider", "")),
                    str(item.get("event", "")),
                    str(item.get("status", "")),
                    item.get("payload_json"),
                    str(item.get("time", "")),
                ),
            )
            conn.commit()
            with self._lock:
                self._pending_integration = [
                    p for p in self._pending_integration if p.get("_key") != item.get("_key")
                ]

    def _report_queue_full(self) -> None:
        if self._warned_full:
            return
        self._warned_full = True
        logger.warning("audit writer queue near/at full (%d)", QUEUE_MAXSIZE)
        if self.observability_service is not None:
            try:
                from core.observability import SEVERITY_DEGRADED

                self.observability_service.track_event(
                    "audit.writer.queue_full",
                    severity=SEVERITY_DEGRADED,
                    payload={"queue_size": QUEUE_MAXSIZE},
                )
            except Exception:
                pass

    # ── 退出 / flush ──

    def drain(self) -> None:
        """阻塞直到队列清空（用于显式 flush）。"""
        self._queue.join()

    def shutdown(self) -> None:
        """停止写线程并 flush 剩余条目。"""
        self._stop_event.set()
        if self._writer_thread.is_alive():
            try:
                self._writer_thread.join(timeout=5.0)
            except Exception:
                pass
        # 兜底：直接消费残留
        self._drain_remaining()

    def _drain_remaining(self) -> None:
        # shutdown 时 writer 线程可能已退出，这里用独立连接兜底消费残留队列
        conn = self._connect()
        try:
            while True:
                try:
                    item = self._queue.get_nowait()
                except queue.Empty:
                    break
                try:
                    self._process(conn, item)
                except Exception:
                    logger.warning("audit drain process failed", exc_info=True)
                finally:
                    self._queue.task_done()
        finally:
            try:
                conn.close()
            except Exception:
                pass

    def _start_prune_thread(self) -> None:
        """后台清理线程：定期物理删除超龄行（daemon，不阻塞退出）。"""

        def _loop() -> None:
            while not self._stop_event.wait(PRUNE_INTERVAL_SECONDS):
                try:
                    self._prune()
                except Exception:
                    logger.warning("audit prune failed", exc_info=True)

        threading.Thread(target=_loop, name="audit-prune", daemon=True).start()

    def _prune(self) -> None:
        """物理清理超龄行：audit_trail 保留最近 TRAIL_KEEP 条、integration_audit 保留最近 INTEGRATION_KEEP 条。"""
        conn = self._connect()
        try:
            conn.execute(
                "DELETE FROM audit_trail WHERE rowid NOT IN "
                "(SELECT rowid FROM audit_trail ORDER BY ts DESC, rowid DESC LIMIT ?)",
                (TRAIL_KEEP,),
            )
            conn.execute(
                "DELETE FROM integration_audit WHERE rowid NOT IN "
                "(SELECT rowid FROM integration_audit ORDER BY rowid DESC LIMIT ?)",
                (INTEGRATION_KEEP,),
            )
            conn.commit()
        finally:
            conn.close()

    def _register_exit_handlers(self) -> None:
        atexit.register(self._atexit_flush)
        # 仅在主线程注册信号处理器（Windows 下非主线程注册会抛 ValueError）。
        # 测试框架下（如 pytest 接管 SIGINT）跳过，避免干扰其信号处理。
        if threading.current_thread() is not threading.main_thread():
            return
        try:
            signal.signal(signal.SIGTERM, self._signal_flush)
            signal.signal(signal.SIGINT, self._signal_flush)
        except (ValueError, OSError):
            # Windows 下某些信号不可注册；尽力而为
            pass

    def _atexit_flush(self) -> None:
        try:
            self.shutdown()
        except Exception:
            pass

    def _signal_flush(self, signum, frame) -> None:
        try:
            self.shutdown()
        except Exception:
            pass


# ── 模块级单例工厂（保证同进程共享一个队列）──

_WRITERS: dict[str, AuditWriter] = {}
_WRITERS_LOCK = threading.Lock()


def get_audit_writer(data_dir: str | None = None, observability_service: Any | None = None) -> AuditWriter:
    """获取（或创建）与 data_dir 绑定的审计写入器单例。

    不同模块（AuditTrail / integration_hub）通过同一 data_dir 拿到同一个
    实例，避免各开一个队列、重复消费。
    """
    if data_dir is None:
        data_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data")
    data_dir = os.path.abspath(data_dir)
    with _WRITERS_LOCK:
        writer = _WRITERS.get(data_dir)
        if writer is None:
            writer = AuditWriter(data_dir, observability_service=observability_service)
            _WRITERS[data_dir] = writer
        elif observability_service is not None and writer.observability_service is None:
            writer.observability_service = observability_service
        return writer


# ── 内部工具 ──

def _now_ts() -> float:
    return datetime.now(timezone.utc).timestamp()


def _ts_to_iso(ts: Any) -> str:
    if not ts:
        return ""
    try:
        return datetime.fromtimestamp(float(ts), tz=timezone.utc).isoformat()
    except Exception:
        return ""


def _json_load(text: str | None) -> Any:
    if not text:
        return None
    try:
        return json.loads(text)
    except Exception:
        return None


def _iso_to_ts(value: Any) -> float:
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value).timestamp()
        except Exception:
            try:
                return datetime.strptime(value[:19], "%Y-%m-%dT%H:%M:%S").timestamp()
            except Exception:
                return _now_ts()
    return _now_ts()


def _json_or_none(obj: Any) -> str | None:
    if obj is None:
        return None
    try:
        return json.dumps(obj, ensure_ascii=False)
    except (TypeError, ValueError):
        try:
            return json.dumps(str(obj), ensure_ascii=False)
        except Exception:
            return None


def _legacy_data_store(data_dir: str) -> Any | None:
    """延迟导入 LocalDataStore 以读旧键（避免顶层环形依赖）。

    若 data_dir 下没有 local_storage.db 则无可迁移内容，返回 None。
    """
    db_path = os.path.join(data_dir, "local_storage.db")
    if not os.path.exists(db_path):
        return None
    try:
        from data.local_storage import LocalDataStore

        return LocalDataStore(base_dir=data_dir)
    except Exception:
        return None
