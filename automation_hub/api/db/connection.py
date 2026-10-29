# -*- coding: utf-8 -*-
"""数据库连接与初始化。

所有新代码应使用 ``db.session.get_db_session()`` 获取 SQLAlchemy 2.0 会话。
遗留 ``get_db_connection()`` 保留供旧脚本过渡，不再用于核心 CRUD。
"""

from __future__ import annotations

import warnings
from contextlib import contextmanager
from datetime import datetime, timezone

from ..config import settings


def _get_database_path() -> str:
    """获取当前数据库路径（每次都读取 settings，支持测试 monkeypatch）。"""
    return str(settings.DATABASE_PATH)


DATABASE_PATH = _get_database_path()


def now_iso() -> str:
    """返回当前 UTC 时间的 ISO 格式字符串。"""
    return datetime.now(timezone.utc).isoformat()


@contextmanager
def get_db_connection():
    """遗留接口：获取原生 sqlite3 连接。

    Deprecated: 请使用 ``db.session.get_db_session()`` 替代。
    此函数保留供旧版脚本过渡，将在未来版本中移除。

    P0-6：保留 SQLite 并发治理 pragmas（WAL + busy_timeout + synchronous=NORMAL）
    与 SQLAlchemy 引擎配置一致，避免遗留路径在并发场景触发 ``database is locked``。
    """
    import sqlite3

    warnings.warn(
        "get_db_connection() is deprecated, use db.session.get_db_session() instead",
        DeprecationWarning,
        stacklevel=2,
    )
    conn = sqlite3.connect(_get_database_path(), timeout=10.0)
    conn.row_factory = sqlite3.Row
    # P0-6：与 engine.py 的 _apply_sqlite_pragmas 保持一致
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
    finally:
        conn.close()


def init_db() -> None:
    """通过 ORM 模型创建所有表（替代旧的 schema.sql 方式）。

    表定义由 api.models 中的 SQLAlchemy ORM 模型统一管理。
    生产环境推荐使用 ``alembic upgrade head``；此函数供脚本调用。
    """
    from api.models.base import Base
    from api.models.engine import create_engine_from_db_path

    engine, _ = create_engine_from_db_path(_get_database_path())
    try:
        Base.metadata.create_all(bind=engine)
    finally:
        # 释放连接池句柄（Windows 下未 dispose 会锁住 WAL/SHM 侧车文件，
        # 导致后续替换数据库文件的回滚操作无法删除侧车文件）
        engine.dispose()
