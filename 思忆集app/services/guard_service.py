"""GuardService — 包装 core.action_guard 供 UI 层使用。"""

from __future__ import annotations

from typing import Any

from core.action_guard import ActionCheckResult, ActionGuard


class GuardService:
    """动作治理服务，封装 ActionGuard 供 UI 层调用。"""

    def __init__(self, settings_repository=None, observability_service=None):
        self._guard = ActionGuard(
            settings_repository=settings_repository,
            observability_service=observability_service,
        )

    def validate(self, action_id: str, payload: dict[str, Any] | None = None, source: str = "unknown") -> ActionCheckResult:
        return self._guard.validate(action_id, payload=payload, source=source)

    def validate_command(self, command_id: str, payload: dict[str, Any] | None = None, source: str = "unknown") -> ActionCheckResult:
        return self._guard.validate_command(command_id, payload=payload, source=source)

    def validate_shell_command(self, command: str, payload: dict[str, Any] | None = None, source: str = "unknown") -> ActionCheckResult:
        return self._guard.validate_shell_command(command, payload=payload, source=source)

    @property
    def action_guard(self) -> ActionGuard:
        """返回底层 ActionGuard 实例，供需要直接传递 ActionGuard 的场景使用。"""
        return self._guard
