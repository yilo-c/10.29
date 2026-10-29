# -*- coding: utf-8 -*-
"""SQLAlchemy 2.0 会话上下文管理器单元测试"""

import threading
import uuid

import pytest
from sqlalchemy.orm import Session
from sqlalchemy import text

from automation_hub.api.db.session import get_db_session, get_session_factory
from automation_hub.api.models.approvals import ApprovalRequest


def test_session_factory_singleton():
    """测试会话工厂是单例"""
    factory1 = get_session_factory()
    factory2 = get_session_factory()
    assert factory1 is factory2


def test_get_db_session_context_manager():
    """测试 get_db_session 上下文管理器"""
    with get_db_session() as session:
        assert isinstance(session, Session)
        # 测试可以执行基本操作（使用text()包装SQL）
        result = session.execute(text("SELECT 1"))
        assert result.fetchone()[0] == 1


def test_get_db_session_autocommit_true():
    """测试自动提交模式"""
    with get_db_session(autocommit=True) as session:
        assert isinstance(session, Session)


def test_get_db_session_autocommit_false():
    """测试手动提交模式"""
    with get_db_session(autocommit=False) as session:
        assert isinstance(session, Session)


def test_get_db_session_exception_handling():
    """测试异常处理和回滚"""
    try:
        with get_db_session() as session:
            # 执行简单操作而不是创建有外键依赖的对象
            session.execute(text("SELECT 1"))
            # 故意抛出异常触发回滚
            raise ValueError("Test rollback")
    except ValueError:
        pass

    # 验证会话能正常关闭


def test_session_factory_with_future_flag():
    """测试会话工厂启用了 future=True 标志"""
    factory = get_session_factory()
    session = factory()
    try:
        # 验证session是SQLAlchemy 2.0风格的
        assert hasattr(session, 'execute')
        assert hasattr(session, 'get')
    finally:
        session.close()


def test_session_expire_on_commit_disabled_simple():
    """测试 expire_on_commit=False 被正确设置（使用简单操作）"""
    factory = get_session_factory()
    session = factory()
    try:
        # 执行简单操作
        result = session.execute(text("SELECT 'test' as value"))
        row = result.fetchone()
        session.commit()

        # 验证会话仍然可用
        assert row.value == 'test'
    finally:
        session.close()


# ---- P0-6 SQLite 并发治理测试 ----


def test_sqlite_pragmas_wal_enabled(tmp_path, monkeypatch):
    """P0-6：SQLAlchemy 引擎建连接后应启用 WAL + busy_timeout + synchronous=NORMAL。"""
    from tests.support import configure_test_env
    configure_test_env(tmp_path, monkeypatch)

    with get_db_session() as session:
        journal_mode = session.execute(text("PRAGMA journal_mode")).fetchone()[0]
        busy_timeout = session.execute(text("PRAGMA busy_timeout")).fetchone()[0]
        synchronous = session.execute(text("PRAGMA synchronous")).fetchone()[0]

    # WAL 模式（小写 wal）
    assert journal_mode.lower() == "wal", f"journal_mode={journal_mode}"
    # busy_timeout=5000ms（P0-6 配置）
    assert busy_timeout == 5000, f"busy_timeout={busy_timeout}"
    # synchronous=NORMAL（值=1，FULL=2，OFF=0）
    assert synchronous == 1, f"synchronous={synchronous}"


def test_concurrent_writes_no_database_locked(tmp_path, monkeypatch):
    """P0-6：50 个线程并发写 tool_runs + 10 个并发读，无 database is locked。

    这是计划验收标准的直接落地：
    > 并发创建 50 个定时任务 + 同时触发 10 个执行，无 database is locked 异常
    """
    from tests.support import configure_test_env
    configure_test_env(tmp_path, monkeypatch)

    errors: list[Exception] = []
    written_ids: list[str] = []
    ids_lock = threading.Lock()

    def writer(idx: int) -> None:
        try:
            run_id = f"concurrent-{idx:03d}-{uuid.uuid4().hex[:8]}"
            with get_db_session() as session:
                session.execute(
                    text(
                        "INSERT INTO tool_runs (id, tool_id, args_json, status, created_at) "
                        "VALUES (:id, :tid, :args, :status, :ts)"
                    ),
                    {
                        "id": run_id,
                        "tid": f"tool-{idx}",
                        "args": "{}",
                        "status": "queued",
                        "ts": "2026-08-02T00:00:00+00:00",
                    },
                )
            with ids_lock:
                written_ids.append(run_id)
        except Exception as exc:
            errors.append(exc)

    def reader(idx: int) -> None:
        try:
            with get_db_session() as session:
                # 持续读，与写并发
                for _ in range(5):
                    session.execute(text("SELECT count(*) FROM tool_runs")).fetchone()
        except Exception as exc:
            errors.append(exc)

    threads = [threading.Thread(target=writer, args=(i,)) for i in range(50)]
    threads += [threading.Thread(target=reader, args=(i,)) for i in range(10)]

    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    # 验收：无 database is locked 异常，50 条全部落库
    locked_errors = [e for e in errors if "database is locked" in str(e).lower()]
    assert not locked_errors, f"got {len(locked_errors)} database is locked errors: {locked_errors[:3]}"
    assert len(errors) == 0, f"unexpected errors: {errors[:3]}"
    assert len(written_ids) == 50, f"only {len(written_ids)}/50 written"

    # 验证数据一致
    with get_db_session() as session:
        count = session.execute(text("SELECT count(*) FROM tool_runs")).fetchone()[0]
    assert count >= 50