"""P1.2 持久任务队列 + 失败重试 — 测试套件。

覆盖：
- 入队 / 出队 / 完成 / 失败 基础流程
- visibility timeout (lease)：dequeue 后任务进入 running，complete/fail 才结束
- 指数退避：第 N 次失败后 next_retry_at - now ≈ min(base*2^(N-1), cap)
- max_retries 耗尽 → dead_letter
- requeue_expired：lease 过期的 running → pending
- 优先级：high priority 优先被取
- 队列隔离：queue_a 拿不到 queue_b 的任务
- cancel / requeue_dead_letter / purge_completed
- 启动时 reaper 一次：构造 PersistentTaskQueue 后残留 running 自动回收
- TaskQueueWorker：end-to-end 跑几条任务，验证 success/fail/dead_letter 计数
- 崩溃恢复：模拟 worker 取到任务后崩溃（不 complete/fail），reaper 回收后再次 dequeue 到
"""
from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
import os
import sqlite3
import tempfile
import time
from pathlib import Path

import pytest

from services.task_queue import (
    DEFAULT_BACKOFF_BASE,
    DEFAULT_BACKOFF_CAP,
    DEFAULT_MAX_RETRIES,
    PRIORITY_HIGH,
    PRIORITY_NORMAL,
    TaskQueueWorker,
    TaskRecord,
    TaskStatus,
    PersistentTaskQueue,
)


@pytest.fixture
def tmp_db_path(tmp_path: Path) -> str:
    return str(tmp_path / "task_queue.db")


@pytest.fixture
def queue(tmp_db_path: str) -> PersistentTaskQueue:
    q = PersistentTaskQueue(db_path=tmp_db_path)
    yield q
    q.close_all()


def _force_task_ready(queue: PersistentTaskQueue, task_id: str) -> None:
    """测试辅助：把 next_retry_at 提前到过去，让 dequeue 立即能拿到。

    用于在 backoff 测试中跳过实际的 sleep 等待。
    """
    conn = sqlite3.connect(queue.db_path)
    conn.execute("UPDATE task_queue SET next_retry_at=0 WHERE task_id=?", (task_id,))
    conn.commit()
    conn.close()


# ── 基础入队 / 出队 ────────────────────────────────────────


class TestEnqueueDequeue:
    def test_enqueue_returns_task_id(self, queue: PersistentTaskQueue):
        tid = queue.enqueue(capability_id="info.brief", payload={"topics": ["ai"]})
        assert isinstance(tid, str) and len(tid) > 0

    def test_enqueue_deduplicates_active_identical_payload(self, queue: PersistentTaskQueue):
        first = queue.enqueue(
            capability_id="info.brief",
            payload={"topics": ["ai"], "limit": 10},
            queue_name="info",
        )
        second = queue.enqueue(
            capability_id="info.brief",
            payload={"limit": 10, "topics": ["ai"]},
            queue_name="info",
        )

        assert second == first
        assert len(queue.list(queue_name="info")) == 1

    def test_enqueue_deduplicates_running_task(self, queue: PersistentTaskQueue):
        first = queue.enqueue(capability_id="cap.x", payload={"value": 1})
        assert queue.dequeue(queue_name="default") is not None

        second = queue.enqueue(capability_id="cap.x", payload={"value": 1})

        assert second == first
        assert len(queue.list(queue_name="default")) == 1

    def test_enqueue_allows_identical_payload_after_completion(self, queue: PersistentTaskQueue):
        first = queue.enqueue(capability_id="cap.x", payload={"value": 1})
        assert queue.dequeue(queue_name="default") is not None
        assert queue.complete(first) is True

        second = queue.enqueue(capability_id="cap.x", payload={"value": 1})

        assert second != first
        assert len(queue.list(queue_name="default")) == 2

    def test_concurrent_enqueue_deduplicates_active_task(self, queue: PersistentTaskQueue):
        def enqueue_one() -> str:
            return queue.enqueue(capability_id="cap.concurrent", payload={"value": 42})

        with ThreadPoolExecutor(max_workers=8) as executor:
            task_ids = list(executor.map(lambda _: enqueue_one(), range(8)))

        assert len(set(task_ids)) == 1
        assert len(queue.list(queue_name="default")) == 1

    def test_enqueue_persists_to_db(self, queue: PersistentTaskQueue, tmp_db_path: str):
        tid = queue.enqueue(capability_id="info.brief", payload={"k": "v"}, queue_name="info")
        # 直接查 SQLite 验证持久化
        conn = sqlite3.connect(tmp_db_path)
        row = conn.execute("SELECT * FROM task_queue WHERE task_id=?", (tid,)).fetchone()
        conn.close()
        assert row is not None
        cols = [d[0] for d in sqlite3.connect(tmp_db_path).execute("SELECT * FROM task_queue WHERE task_id=?", (tid,)).description]
        idx_status = cols.index("status")
        idx_payload = cols.index("payload")
        idx_queue = cols.index("queue_name")
        assert row[idx_status] == TaskStatus.PENDING
        assert row[idx_queue] == "info"
        assert "v" in row[idx_payload]  # payload 是 JSON 文本

    def test_dequeue_empty_returns_none(self, queue: PersistentTaskQueue):
        assert queue.dequeue(queue_name="default") is None

    def test_dequeue_returns_task_and_marks_running(self, queue: PersistentTaskQueue):
        tid = queue.enqueue(capability_id="web.fetch", payload={"url": "http://x"})
        task = queue.dequeue(queue_name="default")
        assert task is not None
        assert task.task_id == tid
        assert task.status == TaskStatus.RUNNING
        assert task.attempts == 1
        assert task.leased_until is not None
        assert task.leased_until > time.time()

    def test_dequeue_sets_lease(self, queue: PersistentTaskQueue):
        queue.enqueue(capability_id="cap.x")
        task = queue.dequeue(queue_name="default", lease_seconds=120.0)
        assert task is not None
        assert task.leased_until is not None
        # lease 至少在 60s 后
        assert task.leased_until >= time.time() + 60.0

    def test_dequeue_payload_roundtrip(self, queue: PersistentTaskQueue):
        payload = {"topics": ["ai", "rust"], "limit": 5, "nested": {"k": "v"}}
        queue.enqueue(capability_id="cap.x", payload=payload)
        task = queue.dequeue(queue_name="default")
        assert task is not None
        assert task.payload == payload

    def test_dequeue_invalid_payload_fallback_empty(self, queue: PersistentTaskQueue, tmp_db_path: str):
        """payload 文本被破坏时 from_row 应返回空 dict 而非抛异常。"""
        tid = queue.enqueue(capability_id="cap.x", payload={"ok": 1})
        # 直接破坏 payload 文本
        conn = sqlite3.connect(tmp_db_path)
        conn.execute("UPDATE task_queue SET payload='[INVALID JSON' WHERE task_id=?", (tid,))
        conn.commit()
        conn.close()
        # 重新打开 queue（强制从磁盘读）
        queue.close_all()
        q2 = PersistentTaskQueue(db_path=tmp_db_path)
        try:
            task = q2.get(tid)
            assert task is not None
            assert task.payload == {}
        finally:
            q2.close_all()


# ── 完成 / 失败 / 重试 ────────────────────────────────────


class TestCompleteFail:
    def test_complete_marks_completed(self, queue: PersistentTaskQueue):
        tid = queue.enqueue(capability_id="cap.x")
        task = queue.dequeue(queue_name="default")
        assert task is not None
        assert queue.complete(tid, receipt_id="rec-123") is True
        rec = queue.get(tid)
        assert rec is not None
        assert rec.status == TaskStatus.COMPLETED
        assert rec.receipt_id == "rec-123"
        assert rec.leased_until is None

    def test_complete_only_running(self, queue: PersistentTaskQueue):
        """complete 一个 pending（未 dequeue）任务应失败。"""
        tid = queue.enqueue(capability_id="cap.x")
        # 未 dequeue → status=pending，complete 应返回 False
        assert queue.complete(tid) is False

    def test_complete_twice_second_returns_false(self, queue: PersistentTaskQueue):
        tid = queue.enqueue(capability_id="cap.x")
        queue.dequeue(queue_name="default")
        assert queue.complete(tid) is True
        assert queue.complete(tid) is False  # 已 completed

    def test_fail_first_attempt_schedules_retry(self, queue: PersistentTaskQueue):
        tid = queue.enqueue(
            capability_id="cap.x", max_retries=3,
            backoff_base=2.0, backoff_cap=60.0,
        )
        queue.dequeue(queue_name="default")
        new_status = queue.fail(tid, error="boom")
        assert new_status == TaskStatus.PENDING
        rec = queue.get(tid)
        assert rec is not None
        assert rec.status == TaskStatus.PENDING
        assert rec.attempts == 1
        # 第 1 次失败后 delay = base * 2^(1-1) = base = 2.0
        assert rec.next_retry_at >= time.time() + 1.5  # 允许 ±0.5s 误差
        assert "boom" in rec.last_error

    def test_fail_exponential_backoff(self, queue: PersistentTaskQueue):
        """连续失败 3 次，next_retry_at 应按 1, 2, 4 秒递增（base=1）。"""
        tid = queue.enqueue(
            capability_id="cap.x", max_retries=10,
            backoff_base=1.0, backoff_cap=60.0,
        )
        expected_delays = [1.0, 2.0, 4.0]
        for i, expected_delay in enumerate(expected_delays, start=1):
            # 上一次 fail 后 next_retry_at 在未来，dequeue 拿不到；
            # 测试中通过直接改库把 next_retry_at 提前到过去以快速推进。
            _force_task_ready(queue, tid)
            task = queue.dequeue(queue_name="default")
            assert task is not None, f"iter {i}: dequeue returned None"
            t_before = time.time()
            new_status = queue.fail(tid, error=f"err-{i}")
            assert new_status == TaskStatus.PENDING, f"iteration {i}"
            rec = queue.get(tid)
            assert rec is not None
            actual_delay = rec.next_retry_at - t_before
            # 允许 ±0.3s 误差（sqlite + time.time() 抖动）
            assert abs(actual_delay - expected_delay) < 0.3, (
                f"iteration {i}: expected delay={expected_delay}, got {actual_delay}"
            )

    def test_fail_backoff_capped(self, queue: PersistentTaskQueue):
        """退避应被 backoff_cap 封顶。"""
        tid = queue.enqueue(
            capability_id="cap.x", max_retries=10,
            backoff_base=10.0, backoff_cap=25.0,
        )
        # 第 1 次：delay = 10*2^0 = 10
        # 第 2 次：delay = 10*2^1 = 20
        # 第 3 次：delay = 10*2^2 = 40 → cap=25
        # 第 4 次：delay = 10*2^3 = 80 → cap=25
        expected = [10.0, 20.0, 25.0, 25.0]
        for i, exp in enumerate(expected, start=1):
            _force_task_ready(queue, tid)
            task = queue.dequeue(queue_name="default")
            assert task is not None, f"iter {i}: dequeue returned None"
            t_before = time.time()
            queue.fail(tid, error=f"err-{i}")
            rec = queue.get(tid)
            assert rec is not None
            actual = rec.next_retry_at - t_before
            assert abs(actual - exp) < 0.5, f"iter {i}: expected {exp}, got {actual}"

    def test_fail_max_retries_to_dead_letter(self, queue: PersistentTaskQueue):
        """超过 max_retries 进入 dead_letter。"""
        tid = queue.enqueue(capability_id="cap.x", max_retries=2, backoff_base=0.01)
        # max_retries=2 表示总尝试 3 次（首次 + 2 次重试）
        for i in range(3):
            _force_task_ready(queue, tid)
            task = queue.dequeue(queue_name="default")
            assert task is not None, f"iter {i}: dequeue returned None"
            new_status = queue.fail(tid, error=f"err-{i}")
            if i < 2:
                assert new_status == TaskStatus.PENDING, f"iter {i}"
        # 第 3 次失败（attempts=3, max_retries=2, 3>2）→ dead_letter
        assert new_status == TaskStatus.DEAD_LETTER
        rec = queue.get(tid)
        assert rec is not None
        assert rec.status == TaskStatus.DEAD_LETTER
        assert "err-2" in rec.last_error

    def test_fail_max_retries_zero_no_retry(self, queue: PersistentTaskQueue):
        """max_retries=0 → 首次失败立即 dead_letter。"""
        tid = queue.enqueue(capability_id="cap.x", max_retries=0)
        queue.dequeue(queue_name="default")
        new_status = queue.fail(tid, error="first fail")
        assert new_status == TaskStatus.DEAD_LETTER
        rec = queue.get(tid)
        assert rec is not None
        assert rec.status == TaskStatus.DEAD_LETTER
        assert rec.attempts == 1

    def test_fail_not_running_returns_current_status(self, queue: PersistentTaskQueue):
        """对一个 completed 任务调 fail 应返回原状态而不覆盖。"""
        tid = queue.enqueue(capability_id="cap.x", max_retries=2)
        queue.dequeue(queue_name="default")
        queue.complete(tid)
        ret = queue.fail(tid, error="late")
        assert ret == TaskStatus.COMPLETED
        rec = queue.get(tid)
        assert rec is not None
        assert rec.status == TaskStatus.COMPLETED
        assert rec.last_error == ""  # 未被 fail 覆盖


# ── 租约 / reaper ────────────────────────────────────────


class TestLeaseAndReaper:
    def test_dequeue_does_not_return_running(self, queue: PersistentTaskQueue):
        """一条任务被 dequeue 后再次 dequeue 应返回 None（同一 queue）。"""
        queue.enqueue(capability_id="cap.x")
        t1 = queue.dequeue(queue_name="default")
        assert t1 is not None
        t2 = queue.dequeue(queue_name="default")
        assert t2 is None

    def test_dequeue_does_not_return_future_retry(self, queue: PersistentTaskQueue):
        """next_retry_at 在未来的 pending 任务不应被 dequeue。"""
        tid = queue.enqueue(capability_id="cap.x", max_retries=3, backoff_base=10.0)
        queue.dequeue(queue_name="default")
        queue.fail(tid, error="x")  # 进入 pending，next_retry_at = now+10
        # 立即 dequeue 不应拿到（还在退避期）
        task = queue.dequeue(queue_name="default")
        assert task is None

    def test_requeue_expired_recovers_running(self, queue: PersistentTaskQueue):
        """ leased_until 已过期的 running 任务被 reaper 回收为 pending。"""
        tid = queue.enqueue(capability_id="cap.x")
        task = queue.dequeue(queue_name="default", lease_seconds=1.0)
        assert task is not None
        # 模拟租约过期：直接更新 leased_until 到过去
        # 用一个独立连接改库，绕过线程本地连接
        conn = sqlite3.connect(queue.db_path)
        conn.execute("UPDATE task_queue SET leased_until=? WHERE task_id=?", (time.time() - 5, tid))
        conn.commit()
        conn.close()

        count = queue.requeue_expired()
        assert count == 1
        rec = queue.get(tid)
        assert rec is not None
        assert rec.status == TaskStatus.PENDING
        assert rec.leased_until is None
        assert "lease_expired" in rec.last_error

    def test_requeue_expired_does_not_touch_completed(self, queue: PersistentTaskQueue):
        tid = queue.enqueue(capability_id="cap.x")
        queue.dequeue(queue_name="default")
        queue.complete(tid)
        count = queue.requeue_expired()
        assert count == 0
        rec = queue.get(tid)
        assert rec is not None
        assert rec.status == TaskStatus.COMPLETED

    def test_constructor_reaps_on_startup(self, tmp_db_path: str):
        """构造 PersistentTaskQueue 时应自动 requeue 上次崩溃残留的 running。"""
        # 第一次构造：入队 + dequeue（不 complete），模拟崩溃
        q1 = PersistentTaskQueue(db_path=tmp_db_path)
        tid = q1.enqueue(capability_id="cap.x")
        q1.dequeue(queue_name="default", lease_seconds=1.0)
        # 让 lease 过期
        conn = sqlite3.connect(tmp_db_path)
        conn.execute("UPDATE task_queue SET leased_until=? WHERE task_id=?", (time.time() - 5, tid))
        conn.commit()
        conn.close()
        q1.close_all()

        # 第二次构造：应该自动 reaper 一次
        q2 = PersistentTaskQueue(db_path=tmp_db_path)
        try:
            rec = q2.get(tid)
            assert rec is not None
            assert rec.status == TaskStatus.PENDING
        finally:
            q2.close_all()


# ── 优先级 / 队列隔离 ────────────────────────────────────


class TestPriorityAndIsolation:
    def test_high_priority_dequeued_first(self, queue: PersistentTaskQueue):
        """入队顺序 normal → high，但 dequeue 应先取 high。"""
        queue.enqueue(capability_id="cap.normal_first", priority=PRIORITY_NORMAL)
        queue.enqueue(capability_id="cap.high", priority=PRIORITY_HIGH)
        queue.enqueue(capability_id="cap.normal_second", priority=PRIORITY_NORMAL)
        t1 = queue.dequeue(queue_name="default")
        t2 = queue.dequeue(queue_name="default")
        t3 = queue.dequeue(queue_name="default")
        assert t1 is not None and t2 is not None and t3 is not None
        assert t1.capability_id == "cap.high"
        # 两个 normal 按 next_retry_at / created_at 早的先
        assert t2.capability_id == "cap.normal_first"
        assert t3.capability_id == "cap.normal_second"

    def test_queue_isolation(self, queue: PersistentTaskQueue):
        """queue_a 的任务不会被 queue_b 的 dequeue 取到。"""
        queue.enqueue(capability_id="cap.a", queue_name="queue_a")
        queue.enqueue(capability_id="cap.b", queue_name="queue_b")
        task = queue.dequeue(queue_name="queue_a")
        assert task is not None
        assert task.capability_id == "cap.a"
        # queue_b 仍有 1 条 pending
        assert queue.dequeue(queue_name="queue_a") is None
        task_b = queue.dequeue(queue_name="queue_b")
        assert task_b is not None
        assert task_b.capability_id == "cap.b"


# ── cancel / requeue_dead_letter / purge ────────────────


class TestCancelAndPurge:
    def test_cancel_pending(self, queue: PersistentTaskQueue):
        tid = queue.enqueue(capability_id="cap.x")
        assert queue.cancel(tid) is True
        rec = queue.get(tid)
        assert rec is not None
        assert rec.status == TaskStatus.FAILED
        assert "cancelled" in rec.last_error

    def test_cancel_running(self, queue: PersistentTaskQueue):
        tid = queue.enqueue(capability_id="cap.x")
        queue.dequeue(queue_name="default")
        assert queue.cancel(tid) is True
        rec = queue.get(tid)
        assert rec is not None
        assert rec.status == TaskStatus.FAILED

    def test_cancel_completed_returns_false(self, queue: PersistentTaskQueue):
        tid = queue.enqueue(capability_id="cap.x")
        queue.dequeue(queue_name="default")
        queue.complete(tid)
        assert queue.cancel(tid) is False

    def test_requeue_dead_letter(self, queue: PersistentTaskQueue):
        tid = queue.enqueue(capability_id="cap.x", max_retries=0)
        queue.dequeue(queue_name="default")
        queue.fail(tid, error="x")
        rec = queue.get(tid)
        assert rec is not None
        assert rec.status == TaskStatus.DEAD_LETTER

        assert queue.requeue_dead_letter(tid) is True
        rec = queue.get(tid)
        assert rec is not None
        assert rec.status == TaskStatus.PENDING
        # 立即可被 dequeue
        task = queue.dequeue(queue_name="default")
        assert task is not None
        assert task.task_id == tid

    def test_requeue_dead_letter_only_dead_letter(self, queue: PersistentTaskQueue):
        """requeue_dead_letter 对 pending/completed 应返回 False。"""
        tid = queue.enqueue(capability_id="cap.x")
        assert queue.requeue_dead_letter(tid) is False

    def test_purge_completed(self, queue: PersistentTaskQueue):
        tid = queue.enqueue(capability_id="cap.x")
        queue.dequeue(queue_name="default")
        queue.complete(tid)
        # 把 updated_at 改到很久以前，触发清理
        conn = sqlite3.connect(queue.db_path)
        conn.execute("UPDATE task_queue SET updated_at=? WHERE task_id=?", (time.time() - 100000, tid))
        conn.commit()
        conn.close()

        count = queue.purge_completed(older_than_seconds=86400.0)
        assert count == 1
        assert queue.get(tid) is None

    def test_purge_completed_keeps_recent(self, queue: PersistentTaskQueue):
        tid = queue.enqueue(capability_id="cap.x")
        queue.dequeue(queue_name="default")
        queue.complete(tid)
        count = queue.purge_completed(older_than_seconds=86400.0)
        assert count == 0
        assert queue.get(tid) is not None


# ── 查询 ────────────────────────────────────────────────


class TestQuery:
    def test_list_filters_by_queue(self, queue: PersistentTaskQueue):
        queue.enqueue(capability_id="cap.a1", queue_name="qa")
        queue.enqueue(capability_id="cap.a2", queue_name="qa")
        queue.enqueue(capability_id="cap.b1", queue_name="qb")
        items = queue.list(queue_name="qa")
        assert len(items) == 2
        assert all(i.queue_name == "qa" for i in items)

    def test_list_filters_by_status(self, queue: PersistentTaskQueue):
        queue.enqueue(capability_id="cap.x")
        queue.enqueue(capability_id="cap.y")
        # dequeue 一个 → running
        queue.dequeue(queue_name="default")
        pending_items = queue.list(status=TaskStatus.PENDING)
        assert len(pending_items) == 1
        running_items = queue.list(status=TaskStatus.RUNNING)
        assert len(running_items) == 1

    def test_stats_counts(self, queue: PersistentTaskQueue):
        # qa: 1 pending, 2 running, 1 dead_letter, 1 completed
        queue.enqueue(capability_id="cap.pending", queue_name="qa")
        tid_run1 = queue.enqueue(capability_id="cap.run1", queue_name="qa")
        tid_run2 = queue.enqueue(capability_id="cap.run2", queue_name="qa")
        tid_dl = queue.enqueue(capability_id="cap.dl", queue_name="qa", max_retries=0)
        tid_done = queue.enqueue(capability_id="cap.done", queue_name="qa")
        # qb: 1 pending
        queue.enqueue(capability_id="cap.qb1", queue_name="qb")

        # 顺序 dequeue：按 created_at 顺序拿到 cap.pending → cap.run1 → cap.run2 → cap.dl → cap.done
        t1 = queue.dequeue(queue_name="qa"); assert t1.capability_id == "cap.pending"
        t2 = queue.dequeue(queue_name="qa"); assert t2.capability_id == "cap.run1"
        t3 = queue.dequeue(queue_name="qa"); assert t3.capability_id == "cap.run2"
        t4 = queue.dequeue(queue_name="qa"); assert t4.capability_id == "cap.dl"
        t5 = queue.dequeue(queue_name="qa"); assert t5.capability_id == "cap.done"

        # cap.dl 立即失败（max_retries=0）→ dead_letter
        queue.fail(tid_dl, error="x")
        # cap.done 完成
        queue.complete(tid_done)

        stats_qa = queue.stats(queue_name="qa")
        assert stats_qa[TaskStatus.PENDING] == 0  # cap.pending 已被 dequeue 转 running
        assert stats_qa[TaskStatus.RUNNING] == 3  # cap.pending + cap.run1 + cap.run2 仍 running
        assert stats_qa[TaskStatus.DEAD_LETTER] == 1
        assert stats_qa[TaskStatus.COMPLETED] == 1

        stats_all = queue.stats()
        # qb 还有 1 条 pending
        assert stats_all[TaskStatus.PENDING] == 1
        assert stats_all[TaskStatus.RUNNING] == 3
        assert stats_all[TaskStatus.DEAD_LETTER] == 1
        assert stats_all[TaskStatus.COMPLETED] == 1


# ── Worker end-to-end ───────────────────────────────────


class TestTaskQueueWorker:
    @pytest.mark.asyncio
    async def test_executor_timeout_covers_async_result(self, tmp_path):
        queue = PersistentTaskQueue(db_path=str(tmp_path / "queue.db"))
        task_id = queue.enqueue(
            capability_id="async.action",
            payload={},
            queue_name="default",
        )

        async def async_executor(task):
            await asyncio.sleep(0.1)
            return {"success": True}

        worker = TaskQueueWorker(
            queue,
            executor=async_executor,
            poll_interval=0.01,
            executor_timeout=0.01,
        )
        await worker.start()
        # 轮询等待超时失败落地，避免固定 sleep 在 Windows 定时器粒度（~15ms）下抖动
        for _ in range(100):
            if worker.stats["failed"] >= 1:
                break
            await asyncio.sleep(0.02)
        await worker.stop()

        assert queue.get(task_id).status == TaskStatus.PENDING
        assert worker.stats["failed"] == 1
        queue.close_all()

    @pytest.mark.asyncio
    async def test_sync_executor_does_not_block_event_loop(self, tmp_path):
        queue = PersistentTaskQueue(db_path=str(tmp_path / "queue.db"))
        started = asyncio.Event()

        def blocking_executor(task):
            started.set()
            time.sleep(0.5)
            return {"success": True}

        task_id = queue.enqueue(
            capability_id="blocking.action",
            payload={},
            queue_name="default",
        )
        worker = TaskQueueWorker(queue, executor=blocking_executor, poll_interval=0.01)
        await worker.start()

        await asyncio.wait_for(started.wait(), timeout=5)
        ticks = 0

        async def ticker():
            nonlocal ticks
            for _ in range(3):
                await asyncio.sleep(0.02)
                ticks += 1

        # 若事件循环被同步 executor 阻塞，ticker 在 0.5s 内无法推进；
        # 0.3s 预算对 3×20ms 的 tick 留足高负载余量
        await asyncio.wait_for(ticker(), timeout=0.3)

        # 轮询等待任务完成，避免固定 sleep 在高负载下不足
        for _ in range(250):
            if queue.get(task_id).status == TaskStatus.COMPLETED:
                break
            await asyncio.sleep(0.02)
        await worker.stop()
        assert ticks == 3
        assert queue.get(task_id).status == TaskStatus.COMPLETED
        queue.close_all()

    def test_worker_processes_success(self, queue: PersistentTaskQueue):
        """成功执行：complete + stats.succeeded +1。"""
        queue.enqueue(capability_id="cap.x")

        def executor(task: TaskRecord) -> dict:
            return {"success": True, "receipt_id": "rec-1"}

        async def _run():
            worker = TaskQueueWorker(
                queue, executor=executor, queue_name="default",
                poll_interval=0.05, reaper_interval=0.5,
            )
            await worker.start()
            # 等待处理
            for _ in range(50):
                if worker.stats["succeeded"] >= 1:
                    break
                await asyncio.sleep(0.05)
            await worker.stop(timeout=2.0)
            return worker.stats

        stats = asyncio.run(_run())
        assert stats["succeeded"] == 1
        assert stats["processed"] == 1

    def test_worker_retries_then_succeeds(self, queue: PersistentTaskQueue):
        """前 N 次失败，第 N+1 次成功。"""
        queue.enqueue(
            capability_id="cap.x", max_retries=3,
            backoff_base=0.01, backoff_cap=0.1,
        )

        call_count = {"n": 0}

        def executor(task: TaskRecord) -> dict:
            call_count["n"] += 1
            if call_count["n"] < 3:
                return {"success": False, "error": "transient"}
            return {"success": True, "receipt_id": "rec-final"}

        async def _run():
            worker = TaskQueueWorker(
                queue, executor=executor, queue_name="default",
                poll_interval=0.02, reaper_interval=0.5,
                lease_seconds=10.0,
                # 不变量：lease_seconds 必须 > executor_timeout
                executor_timeout=5.0,
            )
            await worker.start()
            for _ in range(200):
                if worker.stats["succeeded"] >= 1:
                    break
                await asyncio.sleep(0.02)
            await worker.stop(timeout=2.0)
            return worker.stats

        stats = asyncio.run(_run())
        assert stats["succeeded"] == 1
        assert stats["retried"] == 2  # 2 次失败被重试
        assert stats["failed"] == 2
        assert call_count["n"] == 3

    def test_worker_dead_letter_after_max_retries(self, queue: PersistentTaskQueue):
        """max_retries=2 → 3 次失败后 dead_letter。"""
        queue.enqueue(
            capability_id="cap.x", max_retries=2,
            backoff_base=0.01, backoff_cap=0.1,
        )

        def executor(task: TaskRecord) -> dict:
            return {"success": False, "error": "always fails"}

        async def _run():
            worker = TaskQueueWorker(
                queue, executor=executor, queue_name="default",
                poll_interval=0.02, reaper_interval=0.5,
            )
            await worker.start()
            for _ in range(200):
                if worker.stats["dead_lettered"] >= 1:
                    break
                await asyncio.sleep(0.02)
            await worker.stop(timeout=2.0)
            return worker.stats

        stats = asyncio.run(_run())
        assert stats["dead_lettered"] == 1
        assert stats["failed"] == 3
        # 验证 DB 中任务已是 dead_letter
        items = queue.list(status=TaskStatus.DEAD_LETTER)
        assert len(items) == 1
        assert "always fails" in items[0].last_error

    def test_worker_blocked_task_goes_directly_to_dead_letter(self, queue: PersistentTaskQueue):
        """guard 阻断任务直接进入死信，不安排无意义重试。"""
        queue.enqueue(
            capability_id="cap.blocked", max_retries=3,
            backoff_base=0.01, backoff_cap=0.1,
        )

        def executor(task: TaskRecord) -> dict:
            return {"success": False, "blocked": True, "error": "blocked by guard"}

        async def _run():
            worker = TaskQueueWorker(
                queue, executor=executor, queue_name="default",
                poll_interval=0.02, reaper_interval=0.5,
            )
            await worker.start()
            for _ in range(100):
                if worker.stats["dead_lettered"] >= 1:
                    break
                await asyncio.sleep(0.02)
            await worker.stop(timeout=2.0)
            return worker.stats

        stats = asyncio.run(_run())
        assert stats["dead_lettered"] == 1
        assert stats["failed"] == 1
        assert stats["retried"] == 0

    def test_worker_executor_exception_treated_as_failure(self, queue: PersistentTaskQueue):
        """executor 抛异常 → 视为失败，按 max_retries 退避。"""
        queue.enqueue(
            capability_id="cap.x", max_retries=1,
            backoff_base=0.01, backoff_cap=0.1,
        )

        def executor(task: TaskRecord) -> dict:
            raise RuntimeError("boom")

        async def _run():
            worker = TaskQueueWorker(
                queue, executor=executor, queue_name="default",
                poll_interval=0.02, reaper_interval=0.5,
            )
            await worker.start()
            for _ in range(200):
                if worker.stats["dead_lettered"] >= 1:
                    break
                await asyncio.sleep(0.02)
            await worker.stop(timeout=2.0)
            return worker.stats

        stats = asyncio.run(_run())
        # max_retries=1 → 2 次尝试都失败 → dead_letter
        assert stats["dead_lettered"] == 1
        assert stats["failed"] == 2

    def test_worker_async_executor(self, queue: PersistentTaskQueue):
        """executor 可以是 async 函数。"""
        queue.enqueue(capability_id="cap.x")

        async def executor(task: TaskRecord) -> dict:
            await asyncio.sleep(0.01)
            return {"success": True, "receipt_id": "async-rec"}

        async def _run():
            worker = TaskQueueWorker(
                queue, executor=executor, queue_name="default",
                poll_interval=0.05,
            )
            await worker.start()
            for _ in range(50):
                if worker.stats["succeeded"] >= 1:
                    break
                await asyncio.sleep(0.05)
            await worker.stop(timeout=2.0)
            return worker.stats

        stats = asyncio.run(_run())
        assert stats["succeeded"] == 1

    def test_worker_crash_recovery(self, tmp_db_path: str):
        """模拟 worker 崩溃：取到任务后未 complete → 重启后 reaper 回收 → 再次执行。"""
        # 第一次启动：入队 + dequeue 后"崩溃"（不 complete）
        q1 = PersistentTaskQueue(db_path=tmp_db_path)
        tid = q1.enqueue(capability_id="cap.x", max_retries=2, backoff_base=0.01)
        # 手动 dequeue 模拟 worker 取到
        task = q1.dequeue(queue_name="default", lease_seconds=1.0)
        assert task is not None
        # 让 lease 过期
        conn = sqlite3.connect(tmp_db_path)
        conn.execute("UPDATE task_queue SET leased_until=? WHERE task_id=?", (time.time() - 5, tid))
        conn.commit()
        conn.close()
        q1.close_all()

        # 第二次启动：新 worker 应通过 reaper 回收 + 再次执行
        q2 = PersistentTaskQueue(db_path=tmp_db_path)
        try:
            call_count = {"n": 0}

            def executor(task: TaskRecord) -> dict:
                call_count["n"] += 1
                return {"success": True}

            async def _run():
                worker = TaskQueueWorker(
                    q2, executor=executor, queue_name="default",
                    poll_interval=0.02, reaper_interval=0.3,
                )
                await worker.start()
                for _ in range(100):
                    if worker.stats["succeeded"] >= 1:
                        break
                    await asyncio.sleep(0.02)
                await worker.stop(timeout=2.0)
                return worker.stats

            stats = asyncio.run(_run())
            assert stats["succeeded"] == 1
            assert call_count["n"] == 1
            # 任务最终是 completed
            rec = q2.get(tid)
            assert rec is not None
            assert rec.status == TaskStatus.COMPLETED
        finally:
            q2.close_all()


# ── TaskRecord 序列化 ───────────────────────────────────


class TestTaskRecord:
    def test_to_dict_roundtrip_keys(self, queue: PersistentTaskQueue):
        tid = queue.enqueue(
            capability_id="cap.x", payload={"a": 1},
            queue_name="qa", priority=PRIORITY_HIGH,
            max_retries=5, backoff_base=2.0, backoff_cap=60.0,
        )
        rec = queue.get(tid)
        assert rec is not None
        d = rec.to_dict()
        assert d["task_id"] == tid
        assert d["queue_name"] == "qa"
        assert d["capability_id"] == "cap.x"
        assert d["payload"] == {"a": 1}
        assert d["status"] == TaskStatus.PENDING
        assert d["priority"] == PRIORITY_HIGH
        assert d["max_retries"] == 5
        assert d["backoff_base"] == 2.0
        assert d["backoff_cap"] == 60.0
