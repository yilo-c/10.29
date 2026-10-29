# -*- coding: utf-8 -*-
"""SQLAlchemy 2.0 ORM 模型 - 审批和工具相关单元测试"""

import pytest
from sqlalchemy.orm import registry


def test_approval_models():
    """测试审批相关模型"""
    # 验证表名
    assert ApprovalRequest.__tablename__ == "approval_requests"
    assert ExecutionAuthorization.__tablename__ == "execution_authorizations"
    
    # 验证字段存在
    assert hasattr(ApprovalRequest, 'id')
    assert hasattr(ApprovalRequest, 'created_by_user_id')
    assert hasattr(ApprovalRequest, 'status')
    assert hasattr(ApprovalRequest, 'payload_json')
    
    assert hasattr(ExecutionAuthorization, 'id')
    assert hasattr(ExecutionAuthorization, 'node_id')
    assert hasattr(ExecutionAuthorization, 'tool_name')
    assert hasattr(ExecutionAuthorization, 'decision')


def test_tool_models():
    """测试工具相关模型"""
    # 验证表名
    assert Tool.__tablename__ == "tools"
    assert ToolVersion.__tablename__ == "tool_versions"
    assert ToolRun.__tablename__ == "tool_runs"
    assert RunEvent.__tablename__ == "run_events"
    assert RunArtifact.__tablename__ == "run_artifacts"
    
    # 验证字段存在
    assert hasattr(Tool, 'id')
    assert hasattr(Tool, 'name')
    assert hasattr(Tool, 'description')
    assert hasattr(Tool, 'args_schema_json')
    assert hasattr(Tool, 'is_enabled')
    
    assert hasattr(ToolVersion, 'tool_id')
    assert hasattr(ToolVersion, 'version')
    assert hasattr(ToolVersion, 'spec_json')
    
    assert hasattr(ToolRun, 'tool_id')
    assert hasattr(ToolRun, 'status')
    assert hasattr(ToolRun, 'args_json')
    assert hasattr(ToolRun, 'result_json')
    
    assert hasattr(RunEvent, 'run_id')
    assert hasattr(RunEvent, 'event_type')
    assert hasattr(RunEvent, 'data')
    
    assert hasattr(RunArtifact, 'run_id')
    assert hasattr(RunArtifact, 'filename')
    assert hasattr(RunArtifact, 'data')


def test_models_inherit_base():
    """测试模型正确继承Base类"""
    from automation_hub.api.models.base import Base
    
    # 验证所有模型都继承自Base
    assert issubclass(ApprovalRequest, Base)
    assert issubclass(ExecutionAuthorization, Base)
    assert issubclass(Tool, Base)
    assert issubclass(ToolVersion, Base)
    assert issubclass(ToolRun, Base)
    assert issubclass(RunEvent, Base)
    assert issubclass(RunArtifact, Base)


def test_models_have_timestamp_mixin():
    """测试模型包含时间戳混入"""
    # 验证可变更模型都有 created_at 和 updated_at
    # 只追加不可变表（RunEvent / RunArtifact / ToolVersion）仅需 created_at
    mutable_models = [ApprovalRequest, ExecutionAuthorization, Tool, ToolRun]
    append_only_models = [ToolVersion, RunEvent, RunArtifact]
    for model_class in mutable_models:
        assert hasattr(model_class, 'created_at')
        assert hasattr(model_class, 'updated_at')
    for model_class in append_only_models:
        assert hasattr(model_class, 'created_at')


# 导入模型类
from automation_hub.api.models import ApprovalRequest, ExecutionAuthorization, Tool, ToolVersion, ToolRun, RunEvent, RunArtifact

