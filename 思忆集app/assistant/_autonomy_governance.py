"""Governance mixin for AutonomousAssistantManager.

Exposes audit, permission, rule, and upgrade-policy operations.
All methods assume `self.audit_trail`, `self.rollback_manager`,
`self.permission_matrix`, and `self.rule_center` exist.
"""
from __future__ import annotations

from typing import Any


class GovernanceMixin:
    # M5: 下列属性由宿主类 AutonomousAssistantManager 经
    # assistant.autonomy_components.lifecycle_builder.build_into 注入，此处仅作类型声明。
    audit_trail: Any
    rollback_manager: Any
    permission_matrix: Any
    rule_center: Any

    def list_audit_entries(self, limit: int = 100, action_id: str = "", status: str = ""):
        return self.audit_trail.list_entries(limit=limit, action_id=action_id, status=status)

    def rollback_entry(self, entry_id: str):
        return self.rollback_manager.rollback(entry_id)

    def list_rollbackable(self, limit: int = 50):
        return self.audit_trail.list_rollbackable(limit=limit)

    def get_permission_matrix(self):
        return self.permission_matrix.get_matrix()

    def update_permission(self, action_id: str, updates: dict[str, object]) -> bool:
        return self.permission_matrix.update_entry(action_id, updates)

    def get_remaining_quota(self, action_id: str) -> int:
        return self.permission_matrix.get_remaining_quota(action_id)

    def list_rules(self):
        return self.rule_center.list_rules()

    def add_rule(self, **kwargs):
        return self.rule_center.add_rule(**kwargs)

    def update_rule(self, rule_id: str, updates: dict[str, object]) -> bool:
        return self.rule_center.update_rule(rule_id, updates)

    def remove_rule(self, rule_id: str) -> bool:
        return self.rule_center.remove_rule(rule_id)

    def toggle_rule(self, rule_id: str, enabled: bool | None = None) -> bool:
        return self.rule_center.toggle_rule(rule_id, enabled)
