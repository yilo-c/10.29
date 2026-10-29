# -*- coding: utf-8 -*-
"""SQLAlchemy 2.0 会话上下文管理器

提供线程安全的数据库会话管理，支持嵌套事务。
"""

from contextlib import contextmanager
from typing import Generator, Optional, List, Dict, Any
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy import event
from datetime import datetime, timezone

from ..config import settings
from ..models.engine import create_engine_from_db_path
from ..models.approvals import ApprovalRequest, ExecutionAuthorization


# 全局会话工厂（懒加载，追踪数据库路径变更以支持测试隔离）
_SessionLocal = None
_SessionLocalDbPath: str | None = None
_SessionLocalEngine = None


def _reset_session_factory() -> None:
    """重置会话工厂（测试隔离用）。

    同时 dispose 旧引擎，释放 SQLite 文件句柄（含 WAL/SHM 侧车文件），
    否则替换数据库文件后旧连接仍持有句柄，回滚不生效。
    """
    global _SessionLocal, _SessionLocalDbPath, _SessionLocalEngine
    if _SessionLocalEngine is not None:
        try:
            _SessionLocalEngine.dispose()
        except Exception:
            pass
    _SessionLocal = None
    _SessionLocalDbPath = None
    _SessionLocalEngine = None


def get_session_factory():
    """获取会话工厂单例，数据库路径变更时自动重建。"""
    global _SessionLocal, _SessionLocalDbPath, _SessionLocalEngine
    current_path = str(settings.DATABASE_PATH)
    if _SessionLocal is None or _SessionLocalDbPath != current_path:
        if _SessionLocal is not None:
            _SessionLocal = None
        from sqlalchemy import text
        engine, _ = create_engine_from_db_path(current_path)
        # 在SQLite中禁用外键约束以避免部分迁移时的外键验证问题
        from sqlalchemy import event
        
        @event.listens_for(engine, "connect")
        def set_sqlite_pragma(dbapi_connection, connection_record):
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=OFF")
            cursor.close()
            
        @event.listens_for(engine, "begin")
        def do_begin(conn):
            # 确保每次事务开始时外键都是关闭的
            conn.execute(text("PRAGMA foreign_keys=OFF"))
            
        _SessionLocal = sessionmaker(bind=engine)
        _SessionLocalDbPath = current_path
        _SessionLocalEngine = engine
    return _SessionLocal


@contextmanager
def get_db_session(autocommit: bool = True) -> Generator[Session, None, None]:
    """获取数据库会话的上下文管理器
    
    Args:
        autocommit: 是否自动提交。设为False可支持嵌套事务。
        
    Yields:
        Session: SQLAlchemy 2.0 会话实例
        
    Usage:
        # 自动提交（默认）
        with get_db_session() as session:
            # 执行数据库操作
            pass
            
        # 手动控制事务（嵌套场景）
        with get_db_session(autocommit=False) as session:
            try:
                # 执行数据库操作
                session.commit()
            except Exception:
                session.rollback()
                raise
    """
    session_factory = get_session_factory()
    session: Session = session_factory()
    
    try:
        yield session
        if autocommit:
            session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


# ==================== 审批相关操作函数 ====================

def create_approval_request(
    session: Session,
    approval_id: str,
    user_id: str,
    device_id: Optional[str],
    resource_type: str,
    resource_id: str,
    action: str,
    risk_level: str,
    reason: str,
    payload: Dict[str, Any]
) -> str:
    """创建审批请求"""
    approval = ApprovalRequest(
        id=approval_id,
        created_by_user_id=user_id,
        created_by_device_id=device_id,
        resource_type=resource_type,
        resource_id=resource_id,
        action=action,
        risk_level=risk_level,
        request_reason=reason,
        payload_json=payload,
        status="pending"
    )
    session.add(approval)
    return approval_id


def get_approval_request_by_id(session: Session, approval_id: str) -> Optional[ApprovalRequest]:
    """根据ID获取审批请求"""
    return session.get(ApprovalRequest, approval_id)


def list_pending_approval_requests(session: Session) -> List[ApprovalRequest]:
    """获取所有待处理的审批请求"""
    from sqlalchemy import select
    stmt = select(ApprovalRequest).where(ApprovalRequest.status == "pending").order_by(ApprovalRequest.created_at.asc())
    return list(session.execute(stmt).scalars())


def update_approval_request_status(
    session: Session,
    approval: ApprovalRequest,
    new_status: str,
    decided_by_user_id: Optional[str],
    decision_note: str
) -> ApprovalRequest:
    """更新审批请求状态"""
    approval.status = new_status
    approval.decided_by_user_id = decided_by_user_id
    approval.decision_note = decision_note
    approval.decided_at = datetime.now(timezone.utc)
    session.add(approval)
    return approval


def create_execution_authorization(
    session: Session,
    auth_id: str,
    node_id: str,
    tool_name: str,
    args: Dict[str, Any],
    risk_level: str,
    decision: str,
    reason: str,
    decided_at: Optional[datetime] = None
) -> str:
    """创建执行授权"""
    auth = ExecutionAuthorization(
        id=auth_id,
        node_id=node_id,
        tool_name=tool_name,
        args_json=args,
        risk_level=risk_level,
        decision=decision,
        reason=reason,
        decided_at=decided_at
    )
    session.add(auth)
    return auth_id


def get_execution_authorization_by_id(session: Session, auth_id: str) -> Optional[ExecutionAuthorization]:
    """根据ID获取执行授权"""
    return session.get(ExecutionAuthorization, auth_id)


# ==================== now_iso 统一入口 ====================

from .connection import now_iso