# -*- coding: utf-8 -*-
"""SQLAlchemy 2.0 ORM 基础层单元测试"""

import pytest
import tempfile
import os
from sqlalchemy import create_engine, inspect
from sqlalchemy.orm import sessionmaker, DeclarativeBase

from automation_hub.api.models.base import Base, TimestampMixin
from automation_hub.api.models.engine import create_engine_from_db_path


def test_base_class():
    """测试 Base 类可以被继承"""
    from sqlalchemy.orm import DeclarativeBase
    
    assert issubclass(Base, DeclarativeBase)


def test_timestamp_mixin():
    """测试 TimestampMixin 提供正确的字段定义"""
    from sqlalchemy.orm import Mapped, mapped_column
    from sqlalchemy import DateTime
    
    # 检查字段类型
    assert hasattr(TimestampMixin, 'created_at')
    assert hasattr(TimestampMixin, 'updated_at')


def test_create_engine_from_db_path():
    """测试从数据库路径创建引擎和Session工厂"""
    with tempfile.NamedTemporaryFile(delete=False) as tmp:
        tmp_path = tmp.name
    
    try:
        engine, SessionLocal = create_engine_from_db_path(tmp_path)
        
        # 验证引擎类型
        assert engine is not None
        assert callable(SessionLocal)
        
        # 验证可以创建会话
        session = SessionLocal()
        assert session is not None
        session.close()
        
    finally:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)


def test_engine_can_create_simple_table():
    """测试引擎可以创建简单表（不包含外键依赖）"""
    with tempfile.NamedTemporaryFile(delete=False) as tmp:
        tmp_path = tmp.name
    
    try:
        engine, _ = create_engine_from_db_path(tmp_path)
        
        # 创建独立的测试基类（避免导入其他模型的外键依赖）
        class TestBase(DeclarativeBase):
            pass
        
        # 创建一个简单的测试模型（无外键）
        from sqlalchemy.orm import Mapped, mapped_column
        from sqlalchemy import String
        
        class SimpleTestModel(TestBase, TimestampMixin):
            __tablename__ = "simple_test_table"
            id: Mapped[int] = mapped_column(primary_key=True)
            name: Mapped[str] = mapped_column(String(50))
        
        # 创建表
        TestBase.metadata.create_all(engine)
        
        # 验证表存在 - 使用 inspect 替代 has_table
        inspector = inspect(engine)
        tables = inspector.get_table_names()
        assert "simple_test_table" in tables
        
    finally:
        # 确保引擎连接已关闭
        engine.dispose()
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)