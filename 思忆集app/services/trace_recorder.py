"""TraceRecorder — 统一执行观测（P1-2）。

跨执行路径（butler/cron/task_queue）统一步骤级追踪：
- SQLite 持久化 task_runs + exec_steps（与 assistant/execution_history 并存，
  新数据以本记录器为源；前端迁移后 execution_history 可废弃）
- 订阅 EventBus ``exec.step`` 事件自动记录（各执行路径在进度点发射）
- 提供 list/get/stats 供 trace.* RPC 与运行分析面板

线程安全：单连接 check_same_thread=False + RLock（cron 在工作线程写，
handler 在事件循环线程读）。
"""

from __future__ import annotations

import logging
import sqlite3
import threading
import time
from typing import Any

logger = logging.getLogger("siyi.trace_recorder")

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS task_runs (
    task_id     TEXT PRIMARY KEY,
    task_type   TEXT NOT NULL,
    title       TEXT NOT NULL DEFAULT '',
    status      TEXT NOT NULL DEFAULT 'running',
    source      TEXT NOT NULL DEFAULT '',
    started_at  REAL NOT NULL,
    finished_at REAL,
    cost_tokens INTEGER NOT NULL DEFAULT 0,
    error       TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS exec_steps (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id   TEXT NOT NULL,
    step_no   INTEGER NOT NULL,
    step_type TEXT NOT NULL,
    tool      TEXT NOT NULL DEFAULT '',
    summary   TEXT NOT NULL DEFAULT '',
    ts        REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_exec_steps_task ON exec_steps(task_id);
CREATE INDEX IF NOT EXISTS idx_task_runs_started ON task_runs(started_at);
"""

# exec.step 事件 payload 约定：
# {task_id?, task_type?, title?, source?, step_type, tool?, summary?, status?}
# status="started" 开 run；status ∈ _TERMINAL_STATUSES 结束 run；
# status ∈ _ACTIVE_STATUSES 是**合法非终态**，落到 run.status（不结束 run）；
# 其余视为步骤记录。task_id 缺失时按 source 生成临时 run id。

# AG-C1①：悬挂根因——chat_paths/pydantic_agent_path.py 挂起时发 status="waiting"
# （发送侧今日已定稿，不改），而本记录器过去只认 completed/failed/interrupted，
# waiting 既不进终态也不进开 run 分支，被末尾的 record_step 当步骤吞掉，
# run 因此永久停在 running（观测面板/统计全程失真，现库存 9 条悬挂）。
# 故把「非终态但改变 run 生命周期」的状态显式列全：
#   waiting → run.status='waiting'（等用户审批，资源已释放）
#   resumed → run.status='running'（chat.resume 从断点续跑，重新回到执行中）
# cancelled 同属悬挂来源（同文件两处终态出口发 status="cancelled"），一并归终态。
_TERMINAL_STATUSES = ("completed", "failed", "interrupted", "cancelled")
_ACTIVE_STATUSES = {"waiting": "waiting", "resumed": "running"}

# reconcile_stale_runs 默认超龄阈值（小时）：正常 run 由 finish_run/终态事件收尾，
# 只有进程被杀/崩溃导致收尾事件丢失才会滞留 running，6h 足以覆盖最长合法任务。
_DEFAULT_STALE_MAX_AGE_H = 6.0


class TraceRecorder:
    """统一执行追踪记录器。"""

    def __init__(self, db_path: str) -> None:
        self._db_path = db_path
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.executescript(_SCHEMA_SQL)
            self._conn.commit()

    def close(self) -> None:
        with self._lock:
            try:
                self._conn.close()
            except Exception:  # noqa: BLE001
                pass

    # ── 写入 API ──

    def start_run(
        self,
        task_id: str,
        task_type: str,
        *,
        title: str = "",
        source: str = "",
    ) -> None:
        with self._lock:
            self._conn.execute(
                """INSERT OR REPLACE INTO task_runs
                   (task_id, task_type, title, status, source, started_at)
                   VALUES (?, ?, ?, 'running', ?, ?)""",
                (task_id, task_type, title[:200], source, time.time()),
            )
            self._conn.commit()

    def finish_run(
        self,
        task_id: str,
        status: str,
        *,
        error: str = "",
        cost_tokens: int = 0,
    ) -> None:
        with self._lock:
            cur = self._conn.execute("SELECT task_id FROM task_runs WHERE task_id = ?", (task_id,))
            if cur.fetchone() is None:
                return  # 未登记的 run 不补写，避免脏数据
            self._conn.execute(
                """UPDATE task_runs
                   SET status = ?, finished_at = ?, error = ?, cost_tokens = ?
                   WHERE task_id = ?""",
                (status, time.time(), str(error)[:500], int(cost_tokens), task_id),
            )
            self._conn.commit()

    def set_run_status(self, task_id: str, status: str) -> None:
        """把 run 置为某个**非终态**（waiting / running），不动 ``finished_at``。

        run 不存在时按 chat_tool 就地登记，避免挂起事件早于 started 时丢观测。
        """
        with self._lock:
            exists = self._conn.execute(
                "SELECT task_id FROM task_runs WHERE task_id = ?", (task_id,)
            ).fetchone()
            if exists is None:
                self._conn.execute(
                    """INSERT INTO task_runs
                       (task_id, task_type, title, status, source, started_at)
                       VALUES (?, 'chat_tool', '', ?, '', ?)""",
                    (task_id, status, time.time()),
                )
            else:
                self._conn.execute(
                    "UPDATE task_runs SET status = ? WHERE task_id = ?", (status, task_id)
                )
            self._conn.commit()

    def record_step(
        self,
        task_id: str,
        step_type: str,
        *,
        tool: str = "",
        summary: str = "",
    ) -> int:
        """记录一步，返回 step_no。run 不存在时自动以 running 登记。"""
        with self._lock:
            cur = self._conn.execute(
                "SELECT COALESCE(MAX(step_no), 0) FROM exec_steps WHERE task_id = ?",
                (task_id,),
            )
            step_no = int(cur.fetchone()[0]) + 1
            exists = self._conn.execute("SELECT task_id FROM task_runs WHERE task_id = ?", (task_id,)).fetchone()
            if exists is None:
                self._conn.execute(
                    """INSERT INTO task_runs
                       (task_id, task_type, title, status, source, started_at)
                       VALUES (?, 'chat_tool', '', 'running', '', ?)""",
                    (task_id, time.time()),
                )
            self._conn.execute(
                """INSERT INTO exec_steps (task_id, step_no, step_type, tool, summary, ts)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (task_id, step_no, step_type, str(tool)[:80], str(summary)[:400], time.time()),
            )
            self._conn.commit()
            return step_no

    # ── 悬挂收敛 ──

    def reconcile_stale_runs(self, *, max_age_h: float = _DEFAULT_STALE_MAX_AGE_H) -> int:
        """把超龄仍 ``running`` 的 run 归正为 ``interrupted``，返回收敛条数。

        为什么要收敛器：run 的终态完全依赖 exec.step 收尾事件，进程被强杀 /
        崩溃 / 热重载时收尾事件永久丢失，这些 run 会以 running 姿态无限悬挂
        （trace.stats 的进行中数、前端"正在执行"面板全被污染）。

        只收敛 ``running``：``waiting`` 是**有意义的**等待态（等用户审批），
        其超时由审批侧策略负责，不由本收敛器代持（AG-C1① 已把 waiting 收进
        合法非终态，不再伪装成 running）。

        启动时调用一次即可（backend/headless_main.py 2.6 段），不做后台周期扫描
        —— 长任务与收敛器抢同一张表只会制造新的误伤。
        """
        max_age_s = max(0.0, float(max_age_h)) * 3600.0
        cutoff = time.time() - max_age_s
        with self._lock:
            cur = self._conn.execute(
                """UPDATE task_runs
                   SET status = 'interrupted', finished_at = ?, error = ?
                   WHERE status = 'running' AND started_at < ?""",
                (time.time(), f"reconciled: stale running over {max_age_h:g}h", cutoff),
            )
            self._conn.commit()
            return int(cur.rowcount or 0)

    # ── EventBus 接入 ──

    def on_exec_step(self, event: Any) -> None:
        """exec.step 事件回调（由 EventBus 触发，同步执行）。"""
        try:
            payload = getattr(event, "payload", None) or {}
            if not isinstance(payload, dict):
                return
            self.record_from_payload(payload)
        except Exception:  # noqa: BLE001
            logger.debug("exec.step record failed", exc_info=True)

    def record_from_payload(self, payload: dict[str, Any]) -> None:
        """按 exec.step 约定 payload 记录（供事件回调与直接调用共用）。"""
        task_id = str(payload.get("task_id") or "")
        if not task_id:
            task_id = f"adhoc-{int(time.time() * 1000)}"
        status = str(payload.get("status") or "")
        if status == "started":
            self.start_run(
                task_id,
                str(payload.get("task_type") or "chat_tool"),
                title=str(payload.get("title") or ""),
                source=str(payload.get("source") or ""),
            )
            return
        if status in _TERMINAL_STATUSES:
            self.finish_run(
                task_id,
                status,
                error=str(payload.get("error") or ""),
                cost_tokens=int(payload.get("cost_tokens") or 0),
            )
            return
        if status in _ACTIVE_STATUSES:
            # AG-C1①：合法非终态必须落进 run.status，不得下落到步骤记录
            self.set_run_status(task_id, _ACTIVE_STATUSES[status])
            return
        self.record_step(
            task_id,
            str(payload.get("step_type") or "step"),
            tool=str(payload.get("tool") or ""),
            summary=str(payload.get("summary") or ""),
        )

    def subscribe(self, event_bus: Any) -> None:
        """订阅 exec.step 事件。"""
        try:
            event_bus.on("exec.step", self.on_exec_step)
        except Exception as exc:  # noqa: BLE001
            logger.warning("trace recorder subscribe failed: %s", exc)

    # ── 查询 API ──

    def list_runs(
        self,
        *,
        limit: int = 50,
        task_type: str = "",
        status: str = "",
    ) -> list[dict[str, Any]]:
        sql = "SELECT * FROM task_runs"
        clauses: list[str] = []
        args: list[Any] = []
        if task_type:
            clauses.append("task_type = ?")
            args.append(task_type)
        if status:
            clauses.append("status = ?")
            args.append(status)
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY started_at DESC LIMIT ?"
        args.append(max(1, min(int(limit), 500)))
        with self._lock:
            rows = self._conn.execute(sql, args).fetchall()
        return [dict(r) for r in rows]

    def get_run(self, task_id: str) -> dict[str, Any] | None:
        with self._lock:
            run = self._conn.execute("SELECT * FROM task_runs WHERE task_id = ?", (task_id,)).fetchone()
            if run is None:
                return None
            steps = self._conn.execute(
                "SELECT step_no, step_type, tool, summary, ts FROM exec_steps WHERE task_id = ? ORDER BY step_no",
                (task_id,),
            ).fetchall()
        result = dict(run)
        result["steps"] = [dict(s) for s in steps]
        return result

    def get_stats(self, *, days: int = 7) -> dict[str, Any]:
        """近 N 天按类型/状态统计 + 成功率。"""
        since = time.time() - max(1, int(days)) * 86400
        with self._lock:
            by_type = self._conn.execute(
                "SELECT task_type, COUNT(*) AS n FROM task_runs WHERE started_at >= ? GROUP BY task_type",
                (since,),
            ).fetchall()
            by_status = self._conn.execute(
                "SELECT status, COUNT(*) AS n FROM task_runs WHERE started_at >= ? GROUP BY status",
                (since,),
            ).fetchall()
        types = {r["task_type"]: r["n"] for r in by_type}
        statuses = {r["status"]: r["n"] for r in by_status}
        total = sum(statuses.values())
        finished = statuses.get("completed", 0) + statuses.get("failed", 0)
        return {
            "days": int(days),
            "total_runs": total,
            "by_type": types,
            "by_status": statuses,
            "success_rate": round(statuses.get("completed", 0) / finished, 3) if finished else None,
        }
