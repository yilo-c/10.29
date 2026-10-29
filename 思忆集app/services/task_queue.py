"""PersistentTaskQueue — SQLite 持久任务队列 + 失败重试。

P1.2 目标：
- 把一次性 retryable 工作（资讯抓取、URL 学习、云同步 push、外部 API 调用等）
  从「散落的 asyncio.create_task + 内存状态」收敛到 SQLite 持久队列。
- 进程崩溃后未完成的任务在重启后自动恢复（lease 过期 → 重新入队 pending）。
- 失败按指数退避自动重试，超过 max_retries 进入 dead_letter。
- 与 ActionBroker 兼容：worker 拿到任务后通过 capability_id + payload 调 broker.execute_sync()。

与 CronService 的边界：
- CronService = 周期性调度（cron / daily / weekly），负责"什么时候触发"。
- PersistentTaskQueue = 一次性工作（at-least-once 投递），负责"可靠执行 + 失败重试"。

Schema（独立 SQLite 文件 ``data/task_queue.db``，避免污染 kv_store）：

    CREATE TABLE task_queue (
        task_id         TEXT PRIMARY KEY,
        queue_name      TEXT NOT NULL,              -- 队列名（"default" / "info_brief" / ...）
        capability_id   TEXT NOT NULL,              -- 调用的 action_id（喂给 ActionBroker）
        payload         TEXT NOT NULL,              -- JSON-encoded params
        status          TEXT NOT NULL,              -- pending / running / completed / failed / dead_letter
        priority        INTEGER NOT NULL DEFAULT 0, -- 0=normal, 1=high
        attempts        INTEGER NOT NULL DEFAULT 0,
        max_retries     INTEGER NOT NULL DEFAULT 3,
        backoff_base    REAL NOT NULL DEFAULT 1.0,
        backoff_cap     REAL NOT NULL DEFAULT 300.0,
        next_retry_at   REAL NOT NULL,              -- 墙钟戳（core.clock.wall_ts），到期才可被 dequeue
        leased_until    REAL,                       -- 墙钟戳，运行中租约到期则 reaper 回收
        created_at      REAL NOT NULL,
        updated_at      REAL NOT NULL,
        last_error      TEXT,
        receipt_id      TEXT
    );
    CREATE INDEX idx_tq_status_retry ON task_queue(status, next_retry_at);
    CREATE INDEX idx_tq_queue_status ON task_queue(queue_name, status);
    CREATE INDEX idx_tq_leased_until ON task_queue(leased_until) WHERE status='running';

并发模型：
- enqueue / dequeue / complete / fail / requeue_expired 都是同步 + ``BEGIN IMMEDIATE``
  事务，多线程安全（SQLite WAL + busy_timeout=10s）。
- worker_loop 跑在 asyncio 任务里，每次循环：
  1. dequeue(queue_name, lease_seconds=60)
  2. 若拿到任务 → 通过 executor 调用 broker.execute_sync(ActionRequest(...))
  3. 成功 → complete(task_id, receipt_id)
     失败 → fail(task_id, error) （自动决定重试 / dead_letter）
  4. 若队列空 → asyncio.sleep(poll_interval)
- reaper_loop 跑在另一个 asyncio 任务里，每 30s 把过期 lease 回收到 pending。
"""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
import logging
import os
import sqlite3
import threading
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from core.clock import mono_ts, wall_ts

logger = logging.getLogger("siyi.task_queue")


# ── 常量 ──────────────────────────────────────────────────


class TaskStatus:
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"  # 仅在 dead_letter 前的瞬时态；持久态用 dead_letter
    DEAD_LETTER = "dead_letter"


PRIORITY_NORMAL = 0
PRIORITY_HIGH = 1

DEFAULT_MAX_RETRIES = 3
DEFAULT_BACKOFF_BASE = 1.0  # seconds
DEFAULT_BACKOFF_CAP = 300.0  # 5 minutes
# lease 必须 > executor_timeout（见 TaskQueueWorker 的不变量断言），
# 否则 reaper 会在执行器尚未超时时回收任务导致双重执行。
DEFAULT_LEASE_SECONDS = 600.0
DEFAULT_POLL_INTERVAL = 0.5  # worker 空 poll 间隔
DEFAULT_REAPER_INTERVAL = 30.0  # reaper 扫描间隔
DEFAULT_EXECUTOR_TIMEOUT = 300.0

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS task_queue (
    task_id         TEXT PRIMARY KEY,
    queue_name      TEXT NOT NULL,
    capability_id   TEXT NOT NULL,
    payload         TEXT NOT NULL,
    status          TEXT NOT NULL,
    priority        INTEGER NOT NULL DEFAULT 0,
    attempts        INTEGER NOT NULL DEFAULT 0,
    max_retries     INTEGER NOT NULL DEFAULT 3,
    backoff_base    REAL NOT NULL DEFAULT 1.0,
    backoff_cap     REAL NOT NULL DEFAULT 300.0,
    next_retry_at   REAL NOT NULL,
    leased_until    REAL,
    created_at      REAL NOT NULL,
    updated_at      REAL NOT NULL,
    last_error      TEXT,
    receipt_id      TEXT,
    payload_hash    TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_tq_status_retry ON task_queue(status, next_retry_at);
CREATE INDEX IF NOT EXISTS idx_tq_queue_status ON task_queue(queue_name, status);
CREATE INDEX IF NOT EXISTS idx_tq_leased_until ON task_queue(leased_until) WHERE status='running';
-- P3.4 性能优化：dequeue 是 worker 每秒 poll 的核心查询，原 idx_tq_queue_status 只能
-- 过滤 queue_name+status 两列，next_retry_at 范围过滤 + priority/created_at 排序都
-- 需回表 + 文件排序。新复合索引让 dequeue 走 index range scan，避免每秒全量排序。
CREATE INDEX IF NOT EXISTS idx_tq_dequeue ON task_queue(queue_name, status, next_retry_at, priority DESC, created_at);
-- purge_completed 清理旧 completed 任务时原索引无法覆盖 updated_at 过滤，每次全表扫描。
-- 部分索引只索引 completed 行，大幅减少索引体积和扫描成本。
CREATE INDEX IF NOT EXISTS idx_tq_completed_updated ON task_queue(updated_at) WHERE status='completed';
"""


# ── 数据类 ──────────────────────────────────────────────────


@dataclass
class TaskRecord:
    """队列中一条任务的完整视图。"""

    task_id: str
    queue_name: str
    capability_id: str
    payload: dict[str, Any]
    status: str
    priority: int = PRIORITY_NORMAL
    attempts: int = 0
    max_retries: int = DEFAULT_MAX_RETRIES
    backoff_base: float = DEFAULT_BACKOFF_BASE
    backoff_cap: float = DEFAULT_BACKOFF_CAP
    next_retry_at: float = 0.0
    leased_until: float | None = None
    created_at: float = 0.0
    updated_at: float = 0.0
    last_error: str = ""
    receipt_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "queue_name": self.queue_name,
            "capability_id": self.capability_id,
            "payload": dict(self.payload),
            "status": self.status,
            "priority": self.priority,
            "attempts": self.attempts,
            "max_retries": self.max_retries,
            "backoff_base": self.backoff_base,
            "backoff_cap": self.backoff_cap,
            "next_retry_at": self.next_retry_at,
            "leased_until": self.leased_until,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "last_error": self.last_error,
            "receipt_id": self.receipt_id,
        }

    @classmethod
    def from_row(cls, row: sqlite3.Row | tuple) -> "TaskRecord":
        # 支持按列名或位置取值
        if isinstance(row, sqlite3.Row):

            def _col(name: str) -> Any:
                return row[name]
        else:
            cols = (
                "task_id",
                "queue_name",
                "capability_id",
                "payload",
                "status",
                "priority",
                "attempts",
                "max_retries",
                "backoff_base",
                "backoff_cap",
                "next_retry_at",
                "leased_until",
                "created_at",
                "updated_at",
                "last_error",
                "receipt_id",
            )

            def _col(name: str) -> Any:
                return row[cols.index(name)]

        try:
            payload = json.loads(_col("payload") or "{}")
        except (json.JSONDecodeError, TypeError):
            payload = {}
        return cls(
            task_id=str(_col("task_id")),
            queue_name=str(_col("queue_name")),
            capability_id=str(_col("capability_id")),
            payload=payload,
            status=str(_col("status")),
            priority=int(_col("priority") or 0),
            attempts=int(_col("attempts") or 0),
            max_retries=int(_col("max_retries") or DEFAULT_MAX_RETRIES),
            backoff_base=float(_col("backoff_base") or DEFAULT_BACKOFF_BASE),
            backoff_cap=float(_col("backoff_cap") or DEFAULT_BACKOFF_CAP),
            next_retry_at=float(_col("next_retry_at") or 0.0),
            leased_until=_col("leased_until"),
            created_at=float(_col("created_at") or 0.0),
            updated_at=float(_col("updated_at") or 0.0),
            last_error=str(_col("last_error") or ""),
            receipt_id=str(_col("receipt_id") or ""),
        )


# ── PersistentTaskQueue ──────────────────────────────────


class PersistentTaskQueue:
    """SQLite 持久任务队列。

    用法::

        q = PersistentTaskQueue(db_path="/path/to/task_queue.db")
        q.enqueue(capability_id="info.brief", payload={"topics":["ai"]}, queue_name="info")
        task = q.dequeue(queue_name="info", lease_seconds=60)
        if task:
            try:
                result = executor(task.capability_id, task.payload)
                q.complete(task.task_id, receipt_id=result.get("receipt_id",""))
            except Exception as exc:
                q.fail(task.task_id, error=str(exc))

    进程崩溃恢复：未完成的 ``running`` 任务在 ``leased_until`` 到期后由
    :meth:`requeue_expired` 重新置为 ``pending``，下次 worker poll 时会再次取出。
    """

    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
        parent = os.path.dirname(db_path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        # 线程本地连接（与 SqliteKVStore 一致的设计：sqlite3 默认禁止跨线程）
        self._local = threading.local()
        self._all_conns: list[sqlite3.Connection] = []
        self._conns_lock = threading.Lock()
        # P2-2 治本：**进程内租约单调钟表** task_id → 单调到期时刻。
        # 为什么需要它：``leased_until`` 是持久化墙钟戳（跨进程/重启必需），但
        # 墙钟回拨后 ``leased_until < now`` 恒假 → 崩溃前 running 的任务永不回
        # pending（lease 卡死）。单调钟不受校时影响，是"进程内经过了多久"的
        # 正确主判据；它进程重启归零，故仅覆盖本进程发出的租约，跨进程/重启
        # 场景由 ``_requeue_expired_locked`` 的墙钟+倒退护栏兜底。
        self._lease_deadlines: dict[str, float] = {}
        self._lease_lock = threading.Lock()
        # 启动时建表 + reaper 一次（把崩溃前残留的 running 转回 pending）
        with self._connect() as conn:
            conn.executescript(_SCHEMA_SQL)
            columns = {row[1] for row in conn.execute("PRAGMA table_info(task_queue)").fetchall()}
            if "payload_hash" not in columns:
                conn.execute("ALTER TABLE task_queue ADD COLUMN payload_hash TEXT NOT NULL DEFAULT ''")
            rows = conn.execute("SELECT task_id, payload FROM task_queue WHERE payload_hash=''").fetchall()
            if rows:
                conn.execute("BEGIN IMMEDIATE")
                try:
                    for task_id, payload_text in rows:
                        conn.execute(
                            "UPDATE task_queue SET payload_hash=? WHERE task_id=?",
                            (self._hash_payload_text(payload_text), task_id),
                        )
                    conn.execute("COMMIT")
                except Exception:
                    conn.execute("ROLLBACK")
                    raise
            conn.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS idx_tq_active_dedup
                ON task_queue(queue_name, capability_id, payload_hash)
                WHERE status IN ('pending', 'running')
                """
            )
            conn.commit()
            self._requeue_expired_locked(conn, now=wall_ts())

    # ── 连接管理（参考 SqliteKVStore 实现） ──────────────

    def _connect(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(
                self.db_path,
                timeout=10.0,
                isolation_level=None,  # 自己控事务边界
                check_same_thread=True,
            )
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            conn.execute("PRAGMA foreign_keys=ON")
            conn.execute("PRAGMA busy_timeout=10000")
            self._local.conn = conn
            with self._conns_lock:
                self._all_conns.append(conn)
        return conn

    def close(self) -> None:
        conn = getattr(self._local, "conn", None)
        if conn is not None:
            try:
                conn.close()
            except sqlite3.Error as exc:
                logger.debug("failed to close task_queue connection: %s", exc)
            with self._conns_lock:
                try:
                    self._all_conns.remove(conn)
                except ValueError:
                    pass
            self._local.conn = None

    def close_all(self) -> None:
        with self._conns_lock:
            conns, self._all_conns = list(self._all_conns), []
        for conn in conns:
            try:
                conn.close()
            except sqlite3.Error as exc:
                logger.debug("failed to close task_queue connection (close_all): %s", exc)
        if getattr(self._local, "conn", None) is not None:
            self._local.conn = None

    def __del__(self) -> None:  # pragma: no cover
        try:
            self.close_all()
        except Exception:
            pass

    # ── 入队 ─────────────────────────────────────────────

    @staticmethod
    def _hash_payload_text(payload_text: str) -> str:
        try:
            payload = json.loads(payload_text or "{}")
        except (json.JSONDecodeError, TypeError):
            payload = {}
        canonical = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    @staticmethod
    def _hash_payload(payload: dict[str, Any] | None) -> str:
        canonical = json.dumps(
            payload or {},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def enqueue(
        self,
        *,
        capability_id: str,
        payload: dict[str, Any] | None = None,
        queue_name: str = "default",
        priority: int = PRIORITY_NORMAL,
        max_retries: int = DEFAULT_MAX_RETRIES,
        backoff_base: float = DEFAULT_BACKOFF_BASE,
        backoff_cap: float = DEFAULT_BACKOFF_CAP,
        task_id: str | None = None,
    ) -> str:
        """入队一条任务，返回 task_id。

        - ``priority=PRIORITY_HIGH`` (1) 的任务会被优先 dequeue。
        - ``max_retries`` = 总尝试次数上限（不含首次）。0 表示不重试。
        - ``backoff_base`` 退避基数；第 N 次失败后等待 ``min(base*2^(N-1), cap)`` 秒。
        """
        if not capability_id:
            raise ValueError("capability_id is required")
        tid = task_id or uuid.uuid4().hex[:16]
        now = wall_ts()
        payload_value = payload or {}
        payload_text = json.dumps(payload_value, ensure_ascii=False)
        payload_hash = self._hash_payload(payload_value)
        conn = self._connect()
        conn.execute("BEGIN IMMEDIATE")
        try:
            existing = conn.execute(
                """
                SELECT task_id FROM task_queue
                WHERE queue_name=? AND capability_id=? AND payload_hash=?
                  AND status IN ('pending', 'running')
                ORDER BY created_at ASC
                LIMIT 1
                """,
                (queue_name, capability_id, payload_hash),
            ).fetchone()
            if existing is not None:
                conn.execute("COMMIT")
                logger.debug(
                    "task_queue deduplicated: id=%s queue=%s capability=%s",
                    existing[0],
                    queue_name,
                    capability_id,
                )
                return str(existing[0])
            try:
                conn.execute(
                    """
                    INSERT INTO task_queue(
                        task_id, queue_name, capability_id, payload, status,
                        priority, attempts, max_retries, backoff_base, backoff_cap,
                        next_retry_at, leased_until, created_at, updated_at, last_error, receipt_id,
                        payload_hash
                    ) VALUES (?, ?, ?, ?, 'pending', ?, 0, ?, ?, ?, ?, NULL, ?, ?, '', '', ?)
                    """,
                    (
                        tid,
                        queue_name,
                        capability_id,
                        payload_text,
                        int(priority),
                        int(max_retries),
                        float(backoff_base),
                        float(backoff_cap),
                        now,
                        now,
                        now,
                        payload_hash,
                    ),
                )
            except sqlite3.IntegrityError:
                conn.execute("ROLLBACK")
                existing = conn.execute(
                    """
                    SELECT task_id FROM task_queue
                    WHERE queue_name=? AND capability_id=? AND payload_hash=?
                      AND status IN ('pending', 'running')
                    ORDER BY created_at ASC
                    LIMIT 1
                    """,
                    (queue_name, capability_id, payload_hash),
                ).fetchone()
                if existing is None:
                    raise
                logger.debug("task_queue deduplicated on conflict: id=%s", existing[0])
                return str(existing[0])
            conn.execute("COMMIT")
        except Exception:
            try:
                conn.execute("ROLLBACK")
            except sqlite3.Error:
                pass
            raise
        logger.info(
            "task_queue enqueue: id=%s queue=%s capability=%s priority=%s max_retries=%s",
            tid,
            queue_name,
            capability_id,
            priority,
            max_retries,
        )
        return tid

    def postpone(self, task_id: str, delay_seconds: float) -> bool:
        """把一条 pending 任务的 next_retry_at 推后 delay_seconds（补跑错峰用）。

        仅对 status='pending' 生效；已 running/completed/dead_letter 返回 False
        不误改。dequeue 以 ``next_retry_at <= now`` 为可调度门槛，因此推后
        next_retry_at 即等效延后该任务被 worker 取走的时刻，把冷启动补跑风暴
        里的分钟级网络任务错开，避免霸占单协程队列头饿死后续任务（J3 根因）。
        """
        delay = max(0.0, float(delay_seconds))
        if delay <= 0:
            return False
        now = wall_ts()
        target = now + delay
        conn = self._connect()
        conn.execute("BEGIN IMMEDIATE")
        try:
            cur = conn.execute(
                "UPDATE task_queue SET next_retry_at=?, updated_at=? WHERE task_id=? AND status='pending'",
                (target, now, task_id),
            )
            conn.execute("COMMIT")
            updated = cur.rowcount > 0
        except Exception:
            try:
                conn.execute("ROLLBACK")
            except sqlite3.Error:
                pass
            raise
        if updated:
            logger.debug("task_queue postpone: id=%s +%.0fs", task_id, delay)
        return updated

    def promote(self, task_id: str, priority: int = 1) -> bool:
        """把一条 pending 任务提权并置为即时可调度（cron.run_now 插队用）。

        与 postpone 相反：清掉补跑 stagger 推后的 next_retry_at（设为当前时间），
        并把 priority 抬到 >=PRIORITY_NORMAL，使手动「立即执行」不被排在
        分钟级补跑网络任务之后。仅 status='pending' 生效，不误改 running/终态。
        """
        now = wall_ts()
        conn = self._connect()
        conn.execute("BEGIN IMMEDIATE")
        try:
            cur = conn.execute(
                "UPDATE task_queue SET priority=?, next_retry_at=?, updated_at=? WHERE task_id=? AND status='pending'",
                (int(priority), now, now, task_id),
            )
            conn.execute("COMMIT")
            updated = cur.rowcount > 0
        except Exception:
            try:
                conn.execute("ROLLBACK")
            except sqlite3.Error:
                pass
            raise
        if updated:
            logger.debug("task_queue promote: id=%s priority=%s", task_id, priority)
        return updated

    # ── 出队（visibility timeout / lease） ──────────────

    def dequeue(
        self,
        *,
        queue_name: str = "default",
        lease_seconds: float = DEFAULT_LEASE_SECONDS,
    ) -> TaskRecord | None:
        """原子取一条 pending 且到期的任务，置为 running 并设 leased_until。

        若队列空或所有任务都未到 next_retry_at，返回 None。
        """
        now = wall_ts()
        leased_until = now + max(1.0, float(lease_seconds))
        conn = self._connect()
        conn.execute("BEGIN IMMEDIATE")
        try:
            # 优先取 high priority + next_retry_at 最早的
            row = conn.execute(
                """
                SELECT * FROM task_queue
                WHERE queue_name = ? AND status = 'pending' AND next_retry_at <= ?
                ORDER BY priority DESC, next_retry_at ASC, created_at ASC
                LIMIT 1
                """,
                (queue_name, now),
            ).fetchone()
            if row is None:
                conn.execute("COMMIT")
                return None
            task = TaskRecord.from_row(row)
            conn.execute(
                """
                UPDATE task_queue
                SET status='running', leased_until=?, attempts=attempts+1, updated_at=?
                WHERE task_id=?
                """,
                (leased_until, now, task.task_id),
            )
            conn.execute("COMMIT")
        except Exception:
            try:
                conn.execute("ROLLBACK")
            except sqlite3.Error:
                pass
            raise
        # P2-2：登记单调钟到期时刻（墙钟被回拨时仍能准时回收）。
        self._track_lease(task.task_id, lease_seconds)
        # 返回前给调用方反映 attempts 已 +1
        task.status = TaskStatus.RUNNING
        task.attempts += 1
        task.leased_until = leased_until
        task.updated_at = now
        logger.debug(
            "task_queue dequeue: id=%s capability=%s attempt=%s leased_until=%.0f",
            task.task_id,
            task.capability_id,
            task.attempts,
            leased_until,
        )
        return task

    # ── 完成 / 失败 / 重试 ───────────────────────────────

    def complete(self, task_id: str, *, receipt_id: str = "") -> bool:
        """标记任务成功完成。返回是否更新了记录。"""
        now = wall_ts()
        conn = self._connect()
        conn.execute("BEGIN IMMEDIATE")
        try:
            cur = conn.execute(
                """
                UPDATE task_queue
                SET status='completed', leased_until=NULL, updated_at=?, receipt_id=?, last_error=''
                WHERE task_id=? AND status='running'
                """,
                (now, receipt_id, task_id),
            )
            conn.execute("COMMIT")
            updated = cur.rowcount > 0
        except Exception:
            try:
                conn.execute("ROLLBACK")
            except sqlite3.Error:
                pass
            raise
        if updated:
            self._forget_lease(task_id)
            logger.info("task_queue complete: id=%s receipt=%s", task_id, receipt_id)
        return updated

    def fail(self, task_id: str, *, error: str, immediate_dead_letter: bool = False) -> str:
        """标记任务失败，自动决定重试或进 dead_letter。

        返回值是任务新状态：``'pending'``（已安排重试）或 ``'dead_letter'``（重试耗尽）。
        若任务当前不是 running（例如已被 reaper 回收或已 complete），返回原状态。

        Args:
            immediate_dead_letter: True 时直接进 dead_letter 不安排重试——用于
                被 guard 永久拦截（blocked）的任务，重试无意义且只会制造噪音。
        """
        now = wall_ts()
        conn = self._connect()
        conn.execute("BEGIN IMMEDIATE")
        try:
            row = conn.execute(
                "SELECT attempts, max_retries, backoff_base, backoff_cap, status FROM task_queue WHERE task_id=?",
                (task_id,),
            ).fetchone()
            if row is None:
                conn.execute("COMMIT")
                logger.warning("task_queue fail: task_id=%s not found", task_id)
                return TaskStatus.FAILED
            attempts = int(row["attempts"])
            max_retries = int(row["max_retries"])
            backoff_base = float(row["backoff_base"])
            backoff_cap = float(row["backoff_cap"])
            current_status = str(row["status"])
            if current_status != TaskStatus.RUNNING:
                # 已被 reaper 回收或已被 complete/fail —— 不要覆盖
                conn.execute("COMMIT")
                logger.warning(
                    "task_queue fail: task_id=%s status=%s (expected running) — skip",
                    task_id,
                    current_status,
                )
                return current_status

            # attempts 已经在 dequeue 时 +1，所以这里 attempts 表示"已尝试次数"
            if immediate_dead_letter or attempts > max_retries:
                # 被永久拦截（blocked）或重试耗尽 → dead_letter
                conn.execute(
                    """
                    UPDATE task_queue
                    SET status='dead_letter', leased_until=NULL, updated_at=?, last_error=?
                    WHERE task_id=?
                    """,
                    (now, error[:2000], task_id),
                )
                conn.execute("COMMIT")
                self._forget_lease(task_id)
                logger.warning(
                    "task_queue dead_letter: id=%s attempts=%s max_retries=%s error=%s",
                    task_id,
                    attempts,
                    max_retries,
                    error[:200],
                )
                return TaskStatus.DEAD_LETTER

            # 安排重试：next_retry_at = now + min(base*2^(attempts-1), cap)
            # attempts 已经包含本次失败，所以下次尝试是 attempts+1，但退避按 attempts 计算
            delay = min(backoff_base * (2 ** max(0, attempts - 1)), backoff_cap)
            next_retry_at = now + delay
            conn.execute(
                """
                UPDATE task_queue
                SET status='pending', leased_until=NULL, updated_at=?, next_retry_at=?, last_error=?
                WHERE task_id=?
                """,
                (now, next_retry_at, error[:2000], task_id),
            )
            conn.execute("COMMIT")
            self._forget_lease(task_id)
            logger.info(
                "task_queue retry: id=%s attempt=%s delay=%.1fs next_retry_at=%.0f error=%s",
                task_id,
                attempts,
                delay,
                next_retry_at,
                error[:200],
            )
            return TaskStatus.PENDING
        except Exception:
            try:
                conn.execute("ROLLBACK")
            except sqlite3.Error:
                pass
            raise

    # ── 取消 / 重新入队死信 ──────────────────────────────

    def cancel(self, task_id: str) -> bool:
        """取消任务（仅 pending/running 可取消；completed/dead_letter 不可）。"""
        now = wall_ts()
        conn = self._connect()
        conn.execute("BEGIN IMMEDIATE")
        try:
            cur = conn.execute(
                """
                UPDATE task_queue
                SET status='failed', leased_until=NULL, updated_at=?, last_error='cancelled by user'
                WHERE task_id=? AND status IN ('pending','running')
                """,
                (now, task_id),
            )
            conn.execute("COMMIT")
            cancelled = cur.rowcount > 0
        except Exception:
            try:
                conn.execute("ROLLBACK")
            except sqlite3.Error:
                pass
            raise
        if cancelled:
            self._forget_lease(task_id)
        return cancelled

    def requeue_dead_letter(self, task_id: str) -> bool:
        """把死信任务重新置为 pending 立即可执行（attempts 保留以便观察，next_retry_at=now）。"""
        now = wall_ts()
        conn = self._connect()
        conn.execute("BEGIN IMMEDIATE")
        try:
            cur = conn.execute(
                """
                UPDATE task_queue
                SET status='pending', leased_until=NULL, updated_at=?, next_retry_at=?, last_error=COALESCE(last_error,'') || ' [requeued]'
                WHERE task_id=? AND status='dead_letter'
                """,
                (now, now, task_id),
            )
            conn.execute("COMMIT")
            return cur.rowcount > 0
        except Exception:
            try:
                conn.execute("ROLLBACK")
            except sqlite3.Error:
                pass
            raise

    # ── reaper：回收过期租约 ─────────────────────────────

    # 墙钟倒退容忍窗（与 core.clock.DEFAULT_ROLLBACK_TOLERANCE_S 同源语义）：
    # lease 存续期内 ``updated_at`` 就是租约起始墙钟，若 ``now`` 比它早超过该
    # 窗口，说明期间墙钟被回拨 → 保守判租约已失效并回收。
    _ROLLBACK_TOLERANCE_S = 60.0

    def _track_lease(self, task_id: str, lease_seconds: float) -> None:
        """登记本进程发出的租约的**单调钟到期时刻**（P2-2 主判据）。"""
        with self._lease_lock:
            self._lease_deadlines[str(task_id)] = mono_ts() + max(1.0, float(lease_seconds))

    def _forget_lease(self, task_id: str) -> None:
        """任务离开 running 状态时清除单调租约记录（防字典无界增长）。"""
        with self._lease_lock:
            self._lease_deadlines.pop(str(task_id), None)

    def _expired_mono_leases(self) -> list[str]:
        """本进程内**单调钟已到期**的 running 任务 id（回拨/前进都免疫）。"""
        now_mono = mono_ts()
        with self._lease_lock:
            return [task_id for task_id, deadline in self._lease_deadlines.items() if now_mono >= deadline]

    def requeue_expired(self, now: float | None = None) -> int:
        """把已过期的 running 任务重新置为 pending（保留 attempts，便于追责）。

        判据双轨（P2-2 治本）：
        1. **单调钟为主**——本进程发出的租约按 ``mono_ts`` 到期，墙钟回拨/前进
           都不影响（这是"进程内经过了多久"的正确钟）；
        2. **墙钟 + 倒退护栏**——持久化 ``leased_until`` 覆盖跨进程/重启场景；
           额外用 ``updated_at``（= 租约起始墙钟）做倒退检测，回拨时保守回收，
           否则"崩溃前 running 的任务"会因 ``leased_until < now`` 恒假而永卡。

        返回回收的任务数。供 worker_loop / 启动时调用。
        """
        now = now if now is not None else wall_ts()
        conn = self._connect()
        with self._conns_lock:  # 防止与 _connect 冲突
            return self._requeue_expired_locked(conn, now=now)

    def _requeue_expired_locked(self, conn: sqlite3.Connection, *, now: float) -> int:
        """requeue_expired 的内部实现，要求调用方持有连接（不一定持有事务）。"""
        mono_expired = self._expired_mono_leases()
        conn.execute("BEGIN IMMEDIATE")
        try:
            # next_retry_at 立即生效（让 worker 马上能 dequeue）
            cur = conn.execute(
                """
                UPDATE task_queue
                SET status='pending', leased_until=NULL, updated_at=?, next_retry_at=?,
                    last_error=COALESCE(last_error,'') || ' [lease_expired]'
                WHERE status='running' AND leased_until IS NOT NULL
                  AND (
                        leased_until < ?
                     OR updated_at > ? + ?
                  )
                """,
                (now, now, now, now, self._ROLLBACK_TOLERANCE_S),
            )
            count = cur.rowcount
            if mono_expired:
                # 单调钟主判据：墙钟未到期（甚至被回拨）但进程内确实已超时。
                placeholders = ",".join("?" for _ in mono_expired)
                mono_cur = conn.execute(
                    f"""
                    UPDATE task_queue
                    SET status='pending', leased_until=NULL, updated_at=?, next_retry_at=?,
                        last_error=COALESCE(last_error,'') || ' [lease_expired_mono]'
                    WHERE status='running' AND task_id IN ({placeholders})
                    """,
                    (now, now, *mono_expired),
                )
                count += mono_cur.rowcount
            conn.execute("COMMIT")
        except Exception:
            try:
                conn.execute("ROLLBACK")
            except sqlite3.Error:
                pass
            raise
        if mono_expired:
            with self._lease_lock:
                for task_id in mono_expired:
                    self._lease_deadlines.pop(task_id, None)
        if count > 0:
            logger.warning("task_queue reaper: requeued %d expired leases", count)
        return count

    # ── 查询 ─────────────────────────────────────────────

    def get(self, task_id: str) -> TaskRecord | None:
        row = (
            self._connect()
            .execute(
                "SELECT * FROM task_queue WHERE task_id=? LIMIT 1",
                (task_id,),
            )
            .fetchone()
        )
        return TaskRecord.from_row(row) if row is not None else None

    def list(
        self,
        *,
        queue_name: str | None = None,
        status: str | None = None,
        limit: int = 100,
    ) -> list[TaskRecord]:
        """按 queue_name / status 过滤，按 created_at 倒序返回。"""
        clauses: list[str] = []
        params: list[Any] = []
        if queue_name is not None:
            clauses.append("queue_name = ?")
            params.append(queue_name)
        if status is not None:
            clauses.append("status = ?")
            params.append(status)
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        params.append(int(limit))
        rows = (
            self._connect()
            .execute(
                f"SELECT * FROM task_queue{where} ORDER BY created_at DESC LIMIT ?",
                params,
            )
            .fetchall()
        )
        return [TaskRecord.from_row(r) for r in rows]

    def stats(self, queue_name: str | None = None) -> dict[str, int]:
        """返回各状态计数。queue_name 为 None 时统计所有队列。"""
        where = " WHERE queue_name=?" if queue_name is not None else ""
        params: tuple[Any, ...] = (queue_name,) if queue_name is not None else ()
        rows = (
            self._connect()
            .execute(
                f"SELECT status, COUNT(*) AS cnt FROM task_queue{where} GROUP BY status",
                params,
            )
            .fetchall()
        )
        result = {
            TaskStatus.PENDING: 0,
            TaskStatus.RUNNING: 0,
            TaskStatus.COMPLETED: 0,
            TaskStatus.FAILED: 0,
            TaskStatus.DEAD_LETTER: 0,
        }
        for row in rows:
            result[str(row["status"])] = int(row["cnt"])
        return result

    def purge_completed(self, *, older_than_seconds: float = 86400.0) -> int:
        """清理完成时间超过 older_than_seconds 的 completed 任务。返回删除条数。"""
        cutoff = wall_ts() - older_than_seconds
        conn = self._connect()
        conn.execute("BEGIN IMMEDIATE")
        try:
            cur = conn.execute(
                "DELETE FROM task_queue WHERE status='completed' AND updated_at < ?",
                (cutoff,),
            )
            conn.execute("COMMIT")
            return cur.rowcount
        except Exception:
            try:
                conn.execute("ROLLBACK")
            except sqlite3.Error:
                pass
            raise


# ── Worker ────────────────────────────────────────────────


# executor 签名：(task: TaskRecord) -> dict[str, Any]
#   - 返回 dict 至少包含 {"success": bool, "receipt_id"?: str, "error"?: str}
TaskExecutor = Callable[[TaskRecord], dict[str, Any]]


class TaskQueueWorker:
    """消费 PersistentTaskQueue 的后台 worker。

    - 拿到任务后调用 ``executor(task)``；executor 可以是同步函数或返回 dict 的协程。
    - 成功 → ``complete``；失败 → ``fail``（自动退避重试）。
    - 启动时跑一次 ``requeue_expired`` 把上次崩溃残留的 running 任务回收。
    - 单实例：每个 queue_name 一个 worker 协程。
    """

    def __init__(
        self,
        queue: PersistentTaskQueue,
        *,
        executor: TaskExecutor,
        queue_name: str = "default",
        lease_seconds: float = DEFAULT_LEASE_SECONDS,
        poll_interval: float = DEFAULT_POLL_INTERVAL,
        reaper_interval: float = DEFAULT_REAPER_INTERVAL,
        executor_timeout: float = DEFAULT_EXECUTOR_TIMEOUT,
        on_task_final: "Callable[[str, str, str], None] | None" = None,
    ) -> None:
        self._queue = queue
        self._executor = executor
        self._queue_name = queue_name
        self._lease_seconds = lease_seconds
        self._poll_interval = poll_interval
        self._reaper_interval = reaper_interval
        self._executor_timeout = executor_timeout
        # AC-2（2026-09-16）：任务到达真终态（completed / dead_letter）时的回调
        # ``on_task_final(task_id, final_status, error)``。cron 队列 worker 用它把
        # cron job history 里的 dispatched 占位条目翻转为真实结果（回写链见
        # services/cron_service.py::on_queue_task_final）。同步调用、异常吞掉——
        # 回写属于观测旁路，绝不能把 worker 主循环带崩。
        self._on_task_final = on_task_final
        # P1-fix: lease 必须 > executor_timeout，否则 reaper 会双重执行
        assert self._lease_seconds > self._executor_timeout, (
            f"lease_seconds ({self._lease_seconds}) must be > executor_timeout ({self._executor_timeout}) to prevent reaper double-execution"
        )
        self._worker_task: asyncio.Task | None = None
        self._reaper_task: asyncio.Task | None = None
        self._stop_event = asyncio.Event()
        self._processed = 0
        self._succeeded = 0
        self._failed = 0
        self._retried = 0
        self._dead_lettered = 0

    @property
    def stats(self) -> dict[str, int]:
        return {
            "processed": self._processed,
            "succeeded": self._succeeded,
            "failed": self._failed,
            "retried": self._retried,
            "dead_lettered": self._dead_lettered,
        }

    async def start(self) -> None:
        """启动 worker + reaper 两个协程。"""
        if self._worker_task is not None:
            return
        self._stop_event.clear()
        # 启动时先回收上次崩溃残留的 lease
        try:
            self._queue.requeue_expired()
        except Exception as exc:
            logger.warning("task_queue worker start: requeue_expired failed: %s", exc)
        self._worker_task = asyncio.create_task(self._worker_loop(), name=f"task_queue_worker[{self._queue_name}]")
        self._reaper_task = asyncio.create_task(self._reaper_loop(), name=f"task_queue_reaper[{self._queue_name}]")
        logger.info(
            "task_queue worker started: queue=%s poll=%.2fs lease=%.0fs reaper=%.0fs",
            self._queue_name,
            self._poll_interval,
            self._lease_seconds,
            self._reaper_interval,
        )

    async def stop(self, *, timeout: float = 5.0) -> None:
        """优雅停止：通知 worker 退出，等待当前任务完成。"""
        self._stop_event.set()
        tasks = [t for t in (self._worker_task, self._reaper_task) if t is not None]
        for t in tasks:
            t.cancel()
        if tasks:
            try:
                await asyncio.wait_for(asyncio.gather(*tasks, return_exceptions=True), timeout=timeout)
            except asyncio.TimeoutError:
                logger.warning("task_queue worker stop: timeout=%.1fs", timeout)
        self._worker_task = None
        self._reaper_task = None
        logger.info(
            "task_queue worker stopped: queue=%s stats=%s",
            self._queue_name,
            self.stats,
        )

    async def _worker_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                task = await asyncio.to_thread(
                    self._queue.dequeue,
                    queue_name=self._queue_name,
                    lease_seconds=self._lease_seconds,
                )
                if task is None:
                    try:
                        await asyncio.wait_for(self._stop_event.wait(), timeout=self._poll_interval)
                    except asyncio.TimeoutError:
                        pass
                    continue
                self._processed += 1
                await self._run_task(task)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.exception("task_queue worker loop error: %s", exc)
                await asyncio.sleep(self._poll_interval)

    async def _run_task(self, task: TaskRecord) -> None:
        """执行单条任务：调 executor → complete / fail。"""
        output: Any = None
        try:
            try:
                async with asyncio.timeout(self._executor_timeout):
                    if inspect.iscoroutinefunction(self._executor):
                        output = await self._executor(task)
                    else:
                        output = await asyncio.to_thread(self._executor, task)
                    if inspect.isawaitable(output):
                        output = await output
            except asyncio.TimeoutError:
                if asyncio.iscoroutine(output):
                    output.close()
                raise
            if not isinstance(output, dict):
                output = {"result": output}
            success = bool(output.get("success", True))
            if success:
                # B-03: complete/fail 是同步 SQLite（BEGIN IMMEDIATE + busy_timeout 10s），
                # 与 dequeue 的 to_thread（:858）对齐 —— 直跑事件循环会在写锁竞争时
                # 最多阻塞 10s
                await asyncio.to_thread(self._queue.complete, task.task_id, receipt_id=str(output.get("receipt_id", "")))
                self._succeeded += 1
                self._notify_task_final(task.task_id, "completed", "")
            else:
                # D-2 修复（2026-09-16）：默认值只在"键缺失"时生效；键存在但值空串
                # 仍会写出 last_error='' 的死信（生产实测 12 条）。改为取值后判空白兜底。
                err = str(output.get("error") or "").strip() or (f"executor returned success=False (capability={task.capability_id})")
                # H2: 被 guard 永久拦截（blocked）的任务直接进死信，不安排无意义重试
                blocked = bool(output.get("blocked")) or "blocked" in err.lower()
                new_status = await asyncio.to_thread(self._queue.fail, task.task_id, error=err, immediate_dead_letter=blocked)
                if new_status == TaskStatus.DEAD_LETTER:
                    self._dead_lettered += 1
                    self._notify_task_final(task.task_id, "dead_letter", err)
                else:
                    self._retried += 1
                self._failed += 1
        except Exception as exc:
            err = f"{type(exc).__name__}: {exc}"
            try:
                new_status = await asyncio.to_thread(self._queue.fail, task.task_id, error=err)
                if new_status == TaskStatus.DEAD_LETTER:
                    self._dead_lettered += 1
                    self._notify_task_final(task.task_id, "dead_letter", err)
                else:
                    self._retried += 1
            except Exception:
                logger.exception("task_queue: failed to mark task %s as failed", task.task_id)
            self._failed += 1
            logger.error(
                "task_queue executor error: id=%s capability=%s error=%s",
                task.task_id,
                task.capability_id,
                err,
            )

    def _notify_task_final(self, task_id: str, final_status: str, error: str) -> None:
        """AC-2：任务真终态旁路回调（cron history 回写等）。异常绝不冒泡回 worker。"""
        cb = self._on_task_final
        if cb is None:
            return
        try:
            cb(task_id, final_status, error)
        except Exception:  # noqa: BLE001
            logger.debug("task_queue on_task_final callback failed: id=%s", task_id, exc_info=True)

    async def _reaper_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=self._reaper_interval)
            except asyncio.TimeoutError:
                pass
            if self._stop_event.is_set():
                break
            try:
                await asyncio.to_thread(self._queue.requeue_expired)
            except Exception as exc:
                logger.warning("task_queue reaper error: %s", exc)
