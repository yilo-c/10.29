# -*- coding: utf-8 -*-
"""SQLAlchemy 2.0 ORM 基础层 - 引擎和会话工厂"""

from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker, Session


# P0-6：SQLite 并发治理 —— WAL + busy_timeout + synchronous=NORMAL
# 这套组合是 SQLite 官方推荐的高并发读写配置：
#   - WAL：多读单写不互斥，写不阻塞读，读不阻塞写
#   - busy_timeout=5000：遇到锁时等 5s 而非立即抛 database is locked
#   - synchronous=NORMAL：WAL 模式下安全且更快（crash-safe, power-loss 可能丢最后一次事务）
def _apply_sqlite_pragmas(dbapi_connection, connection_record) -> None:
    """在每个新 DBAPI 连接上设置 SQLite pragma（由 SQLAlchemy connect 事件触发）。"""
    cursor = dbapi_connection.cursor()
    try:
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=5000")
        cursor.execute("PRAGMA synchronous=NORMAL")
        # foreign_keys 由 session.py 单独控制（默认 OFF 兼容部分迁移）
    finally:
        cursor.close()


def create_engine_from_db_path(db_path: str):
    """从数据库路径创建 SQLAlchemy 引擎和 Session 工厂

    P0-6：在引擎创建时挂 ``connect`` 事件监听器，确保所有连接都启用
    WAL + busy_timeout + synchronous=NORMAL，消除 ``database is locked``。

    Args:
        db_path: SQLite 数据库文件路径

    Returns:
        tuple: (engine, SessionLocal)
    """
    engine = create_engine(
        f"sqlite:///{db_path}",
        echo=False,
        future=True,  # 启用 2.0 模式
        # P0-6：SQLite 连接池配置
        # SQLite 写操作是串行的（即使 WAL 也是单写），pool_size 不宜大；
        # 用默认 QueuePool 即可，连接复用避免每操作新建连接的开销
        connect_args={"timeout": 10.0, "check_same_thread": False},
    )
    event.listen(engine, "connect", _apply_sqlite_pragmas)
    SessionLocal = sessionmaker(engine, class_=Session, expire_on_commit=False)
    return engine, SessionLocal