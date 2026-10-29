# -*- coding: utf-8 -*-
"""审批数据库操作集成测试（验证向后兼容性）"""

import pytest
from datetime import datetime, timezone

from automation_hub.api.approvals.service import create_approval, get_approval_by_id, list_pending_approvals, update_approval_status


def test_approval_integration_with_existing_data():
    """测试新审批函数与现有数据的兼容性"""
    # 创建审批请求
    approval_id = create_approval(
        user_id="integration-test-user",
        device_id="integration-test-device",
        resource_type="integration-test-resource",
        resource_id="integration-test-id",
        action="integration-test-action",
        risk_level="low",
        reason="integration test",
        payload={"integration": "test"}
    )
    
    assert approval_id is not None
    
    # 使用新函数获取审批请求
    approval = get_approval_by_id(approval_id)
    assert approval is not None
    assert approval["id"] == approval_id
    assert approval["created_by_user_id"] == "integration-test-user"
    assert approval["status"] == "pending"
    
    # 更新审批状态
    updated = update_approval_status(
        approval_id=approval_id,
        new_status="approved",
        decided_by_user_id="integration-test-approver",
        decision_note="integration test approved"
    )
    
    assert updated is not None
    assert updated["status"] == "approved"
    assert updated["decided_by_user_id"] == "integration-test-approver"
    
    # 验证不再出现在待处理列表中
    pending = list_pending_approvals()
    pending_ids = [a["id"] for a in pending]
    assert approval_id not in pending_ids