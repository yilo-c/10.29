# -*- coding: utf-8 -*-
"""审批数据库操作单元测试"""

import pytest
from datetime import datetime, timezone

from automation_hub.api.approvals.service import create_approval, get_approval_by_id, list_pending_approvals, update_approval_status


def test_create_approval():
    """测试创建审批请求"""
    user_id = "test-user"
    device_id = "test-device"
    resource_type = "test-resource"
    resource_id = "test-id"
    action = "test-action"
    risk_level = "low"
    reason = "Test approval reason"
    payload = {"key": "value"}
    
    approval_id = create_approval(
        user_id=user_id,
        device_id=device_id,
        resource_type=resource_type,
        resource_id=resource_id,
        action=action,
        risk_level=risk_level,
        reason=reason,
        payload=payload
    )
    
    assert approval_id is not None
    assert isinstance(approval_id, str)
    
    # 验证可以获取到创建的审批
    approval = get_approval_by_id(approval_id)
    assert approval is not None
    assert approval["id"] == approval_id
    assert approval["created_by_user_id"] == user_id
    assert approval["resource_type"] == resource_type
    assert approval["status"] == "pending"


def test_list_pending_approvals():
    """测试获取待处理审批列表"""
    # 创建一个审批
    approval_id = create_approval(
        user_id="test-user-2",
        device_id=None,
        resource_type="test-list",
        resource_id="test-list-id",
        action="list-action",
        risk_level="medium",
        reason="Test list pending",
        payload={"test": "list"}
    )
    
    # 获取待处理列表
    pending_approvals = list_pending_approvals()
    assert len(pending_approvals) >= 1
    
    # 找到我们创建的审批
    found = False
    for approval in pending_approvals:
        if approval["id"] == approval_id:
            found = True
            assert approval["status"] == "pending"
            break
    
    assert found


def test_update_approval_status():
    """测试更新审批状态"""
    # 创建一个审批
    approval_id = create_approval(
        user_id="test-user-3",
        device_id="test-device-3",
        resource_type="test-update",
        resource_id="test-update-id",
        action="update-action",
        risk_level="high",
        reason="Test update status",
        payload={"test": "update"}
    )
    
    # 更新状态为approved
    updated_approval = update_approval_status(
        approval_id=approval_id,
        new_status="approved",
        decided_by_user_id="approver-user",
        decision_note="Approved for testing"
    )
    
    assert updated_approval is not None
    assert updated_approval["status"] == "approved"
    assert updated_approval["decided_by_user_id"] == "approver-user"
    assert updated_approval["decision_note"] == "Approved for testing"
    assert updated_approval["decided_at"] is not None
    
    # 验证不能再更新已处理的审批
    with pytest.raises(ValueError, match="not pending"):
        update_approval_status(
            approval_id=approval_id,
            new_status="rejected",
            decided_by_user_id="another-user",
            decision_note="Should not work"
        )


def test_non_existent_approval_update():
    """测试更新不存在的审批"""
    result = update_approval_status(
        approval_id="non-existent",
        new_status="approved",
        decided_by_user_id="user",
        decision_note="test"
    )
    assert result is None