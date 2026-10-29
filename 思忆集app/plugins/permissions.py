"""Permission model for the plugin system.

Implements the permission categories and runtime checks specified
in docs/plugin-sdk-spec.md.
"""
from __future__ import annotations

import fnmatch
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class PermissionCategory(str, Enum):
    """Permission categories defined in the plugin spec."""

    NETWORK = "network"
    STORAGE = "storage"
    DB = "db"
    NOTIFICATION = "notification"
    FS = "fs"
    UI = "ui"


# Permissions that require explicit secondary confirmation at install time.
HIGH_RISK_PERMISSIONS = frozenset({
    "network:*",
    "fs:*",
    "network:localhost:*",
})

# All recognised permission categories.
ALL_CATEGORIES = frozenset(c.value for c in PermissionCategory)


class PermissionDeniedError(PermissionError):
    """Raised when a plugin attempts an action without the required permission."""

    def __init__(self, plugin_id: str, permission: str, reason: str = "") -> None:
        self.plugin_id = plugin_id
        self.permission = permission
        self.reason = reason
        msg = f"plugin '{plugin_id}' missing required permission: {permission}"
        if reason:
            msg += f" ({reason})"
        super().__init__(msg)


def parse_permission(perm_str: str) -> tuple[str, str]:
    """Parse a permission string into (category, resource).

    Example: ``"network:api.openweathermap.org" → ("network", "api.openweathermap.org")``
    """
    if ":" not in perm_str:
        raise ValueError(f"invalid permission format '{perm_str}'; expected 'category:resource'")
    category, resource = perm_str.split(":", 1)
    return (category.strip(), resource.strip())


def permission_matches(declared: str, required: str) -> bool:
    """Check if a declared permission covers a required permission.

    Supports glob patterns in the resource part.
    ``network:*`` matches ``network:api.example.com``.
    ``fs:read:/data/*`` matches ``fs:read:/data/notes/``.
    """
    if declared == required:
        return True
    if declared == "*":
        return True
    if ":" not in declared or ":" not in required:
        return False
    cat_a, res_a = declared.split(":", 1)
    cat_b, res_b = required.split(":", 1)
    if cat_a != cat_b:
        return False
    if res_a == "*":
        return True
    return fnmatch.fnmatch(res_b, res_a)


class PermissionManager:
    """Manages plugin permissions: declaration, granting, and runtime checks."""

    def __init__(self) -> None:
        # plugin_id -> {permission_string: is_allowed}
        self._declared: dict[str, dict[str, bool]] = {}
        # plugin_id -> {permission_string: True/False}  (user overrides)
        self._overrides: dict[str, dict[str, bool]] = {}

    # ── Registration ──────────────────────────────────────────────────

    def declare_permissions(self, plugin_id: str, permissions: list[str]) -> None:
        """Register which permissions a plugin declares.

        All permissions start as ``True`` (granted). Call ``revoke()`` to
        override.
        """
        self._declared[plugin_id] = {p: True for p in permissions}
        if plugin_id not in self._overrides:
            self._overrides[plugin_id] = {}

    def revoke_permission(self, plugin_id: str, permission: str) -> None:
        """Revoke a specific permission at runtime."""
        if plugin_id in self._overrides:
            self._overrides[plugin_id][permission] = False

    def grant_permission(self, plugin_id: str, permission: str) -> None:
        """Re-grant a previously revoked permission."""
        if plugin_id in self._overrides:
            self._overrides[plugin_id][permission] = True

    def remove_plugin(self, plugin_id: str) -> None:
        """Remove all permission state for a plugin (on uninstall)."""
        self._declared.pop(plugin_id, None)
        self._overrides.pop(plugin_id, None)

    # ── Runtime checks ────────────────────────────────────────────────

    def check_permission(self, plugin_id: str, permission: str) -> bool:
        """Check if a plugin has a specific permission at runtime.

        Checks user overrides first, then declared permissions.
        """
        # Check user override first
        overrides = self._overrides.get(plugin_id, {})
        for declared_perm, allowed in overrides.items():
            if permission_matches(declared_perm, permission):
                if not allowed:
                    return False
                break

        # Check declared permissions
        declared = self._declared.get(plugin_id, {})
        for declared_perm, allowed in declared.items():
            if permission_matches(declared_perm, permission):
                return allowed

        return False

    def require_permission(self, plugin_id: str, permission: str) -> None:
        """Check permission and raise PermissionDeniedError if not granted."""
        if not self.check_permission(plugin_id, permission):
            raise PermissionDeniedError(plugin_id, permission)

    def require_any_permission(self, plugin_id: str, permissions: list[str]) -> str | None:
        """Check if the plugin has ANY of the given permissions.

        Returns the first matching permission, or ``None`` if none match.
        """
        for perm in permissions:
            if self.check_permission(plugin_id, perm):
                return perm
        return None

    # ── Inspection ────────────────────────────────────────────────────

    def get_declared_permissions(self, plugin_id: str) -> dict[str, bool]:
        """Get all declared permissions for a plugin and whether they are granted."""
        declared = self._declared.get(plugin_id, {})
        result: dict[str, bool] = {}
        for perm in declared:
            result[perm] = self.check_permission(plugin_id, perm)
        return result

    def list_plugins_with_permission(self, permission: str) -> list[str]:
        """List all plugin IDs that have a specific permission."""
        return [
            pid for pid in self._declared
            if self.check_permission(pid, permission)
        ]

    def get_high_risk_permissions(self, plugin_id: str) -> list[str]:
        """Return the subset of declared permissions that are high-risk."""
        declared = self._declared.get(plugin_id, {})
        return [p for p in declared if _is_high_risk(p)]

    def is_high_risk_plugin(self, plugin_id: str) -> bool:
        """Check if a plugin has any high-risk permissions declared."""
        return bool(self.get_high_risk_permissions(plugin_id))


def _is_high_risk(permission: str) -> bool:
    """Check if a permission string matches any high-risk pattern."""
    for pattern in HIGH_RISK_PERMISSIONS:
        if permission_matches(pattern, permission):
            return True
    return False
