# -*- coding: utf-8 -*-
"""简化的审批数据库操作测试（避免外键问题）"""

import pytest
from datetime import datetime, timezone

from automation_hub.api.db.session import get_db_session
from automation_hub.api.models.approvals import ApprovalRequest


def test_approval_model_direct(tmp_path, monkeypatch):
    """直接测试ApprovalRequest模型（绕过外键验证）"""
    from tests.support import configure_test_env
    configure_test_env(tmp_path, monkeypatch)

    # 创建审批请求
    with get_db_session() as session:
        approval = ApprovalRequest(
            id="simple-test-1",
            created_by_user_id="test-user",  # 这些字段现在只是String类型
            created_by_device_id="test-device",
            resource_type="test-resource",
            resource_id="test-id",
            action="test-action",
            risk_level="low",
            request_reason="simple test",
            payload_json={"simple": "test"},
            status="pending"
        )
        session.add(approval)
    
    # 验证审批请求已创建
    with get_db_session() as session:
        result = session.get(ApprovalRequest, "simple-test-1")
        assert result is not None
        assert result.id == "simple-test-1"
        assert result.created_by_user_id == "test-user"
        assert result.status == "pending"


def test_approval_status_update(tmp_path, monkeypatch):
    """测试审批状态更新"""
    from tests.support import configure_test_env
    configure_test_env(tmp_path, monkeypatch)

    approval_id = "simple-test-2"
    
    # 创建审批请求
    with get_db_session() as session:
        approval = ApprovalRequest(
            id=approval_id,
            created_by_user_id="test-user-2",
            created_by_device_id=None,
            resource_type="test-resource-2",
            resource_id="test-id-2",
            action="test-action-2",
            risk_level="high",
            request_reason="status update test",
            payload_json={},
            status="pending"
        )
        session.add(approval)
    
    # 更新状态
    with get_db_session() as session:
        approval = session.get(ApprovalRequest, approval_id)
        assert approval is not None
        assert approval.status == "pending"
        
        approval.status = "approved"
        approval.decided_by_user_id = "approver-user"
        approval.decision_note = "approved in simple test"
        approval.decided_at = datetime.now(timezone.utc)
    
    # 验证更新
    with get_db_session() as session:
        updated = session.get(ApprovalRequest, approval_id)
        assert updated is not None
        assert updated.status == "approved"
        assert updated.decided_by_user_id == "approver-user"