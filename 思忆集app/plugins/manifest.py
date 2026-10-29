"""Plugin Manifest v1.0 — parsing, validation, and data structures.

Implements the manifest format defined in docs/plugin-sdk-spec.md.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

# ── Plugin Types ─────────────────────────────────────────────────────────────


class PluginType(str, Enum):
    """Five plugin types supported by the system."""

    TOOL = "tool"
    DATA_SOURCE = "datasource"
    CARD = "card"
    ACTION = "action"
    COMPANION = "companion"


VALID_PLUGIN_TYPES = frozenset(t.value for t in PluginType)

# ── Permission categories ────────────────────────────────────────────────────

PERMISSION_CATEGORIES = frozenset({"network", "storage", "db", "notification", "fs", "ui"})

# ── Manifest Data Model ──────────────────────────────────────────────────────


@dataclass(frozen=True)
class PluginManifest:
    """Immutable plugin manifest representing the v1.0 format."""

    manifest_version: str
    id: str
    name: str
    version: str
    type: str
    entry_point: str
    description: str = ""
    author: str = ""
    license: str = ""
    permissions: tuple[str, ...] = field(default_factory=tuple)
    capabilities: tuple[str, ...] = field(default_factory=tuple)
    config_schema: dict[str, Any] = field(default_factory=dict)
    min_app_version: str = ""
    max_app_version: str | None = None
    dependencies: tuple[str, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        """Convert manifest to a JSON-serialisable dictionary."""
        result: dict[str, Any] = {
            "manifest_version": self.manifest_version,
            "id": self.id,
            "name": self.name,
            "version": self.version,
            "type": self.type,
            "entry_point": self.entry_point,
            "description": self.description,
            "author": self.author,
            "license": self.license,
            "permissions": list(self.permissions),
            "capabilities": list(self.capabilities),
            "config_schema": dict(self.config_schema),
            "min_app_version": self.min_app_version,
        }
        if self.max_app_version is not None:
            result["max_app_version"] = self.max_app_version
        if self.dependencies:
            result["dependencies"] = list(self.dependencies)
        return result

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PluginManifest:
        """Create a manifest from a dictionary (deserialised JSON)."""
        return cls(
            manifest_version=str(data.get("manifest_version", "1.0")),
            id=str(data["id"]),
            name=str(data.get("name", "")),
            version=str(data.get("version", "0.1.0")),
            type=str(data["type"]),
            entry_point=str(data["entry_point"]),
            description=str(data.get("description", "")),
            author=str(data.get("author", "")),
            license=str(data.get("license", "")),
            permissions=tuple(str(p) for p in data.get("permissions", [])),
            capabilities=tuple(str(c) for c in data.get("capabilities", [])),
            config_schema=dict(data.get("config_schema", {})),
            min_app_version=str(data.get("min_app_version", "")),
            max_app_version=data.get("max_app_version"),
            dependencies=tuple(str(d) for d in data.get("dependencies", [])),
        )

    @classmethod
    def from_json(cls, json_str: str) -> PluginManifest:
        """Parse a JSON string into a PluginManifest."""
        data = json.loads(json_str)
        return cls.from_dict(data)


# ── Validation ───────────────────────────────────────────────────────────────


class ManifestValidationError(ValueError):
    """Raised when a manifest fails validation."""

    def __init__(self, errors: list[str]) -> None:
        self.errors = errors
        super().__init__("; ".join(errors))


REQUIRED_FIELDS = frozenset({"id", "type", "entry_point", "permissions", "capabilities", "config_schema"})


def validate_manifest_dict(data: dict[str, Any]) -> list[str]:
    """Validate a manifest dictionary.

    Returns a list of error messages. An empty list means the manifest is valid.
    """
    errors: list[str] = []

    # ── Required fields ──────────────────────────────────────────────
    for field_name in REQUIRED_FIELDS:
        if field_name not in data:
            errors.append(f"missing required field: {field_name}")

    if "id" in data:
        plugin_id = str(data["id"])
        if not plugin_id or len(plugin_id) < 3:
            errors.append("id must be at least 3 characters")
        elif "." not in plugin_id:
            errors.append("id should use reverse-domain format (e.g. com.example.plugin)")
        elif len(plugin_id) > 128:
            errors.append("id must be 128 characters or fewer")

    if "type" in data:
        plugin_type = str(data["type"]).lower()
        if plugin_type not in VALID_PLUGIN_TYPES:
            valid = ", ".join(sorted(VALID_PLUGIN_TYPES))
            errors.append(f"invalid type '{plugin_type}'; must be one of: {valid}")

    if "entry_point" in data:
        entry = str(data["entry_point"])
        if ":" not in entry:
            errors.append("entry_point must be in 'module_path:ClassName' format")

    if "version" in data:
        version = str(data.get("version", ""))
        if version and not _is_valid_semver(version):
            errors.append(f"version '{version}' is not a valid semver string")

    # ── Permissions ──────────────────────────────────────────────────
    permissions = data.get("permissions", [])
    if not isinstance(permissions, list):
        errors.append("permissions must be a list")
    else:
        for i, perm in enumerate(permissions):
            if not isinstance(perm, str):
                errors.append(f"permissions[{i}] must be a string")
                continue
            if ":" not in perm:
                errors.append(f"invalid permission format '{perm}'; expected 'category:resource'")
            else:
                category = perm.split(":")[0]
                if category not in PERMISSION_CATEGORIES:
                    valid_cats = ", ".join(sorted(PERMISSION_CATEGORIES))
                    errors.append(f"permissions[{i}]: unknown category '{category}'; valid: {valid_cats}")

    # ── Capabilities ─────────────────────────────────────────────────
    capabilities = data.get("capabilities", [])
    if not isinstance(capabilities, list):
        errors.append("capabilities must be a list")

    # ── Config schema ─────────────────────────────────────────────────
    config_schema = data.get("config_schema", {})
    if not isinstance(config_schema, dict):
        errors.append("config_schema must be a dict")

    return errors


def validate_manifest(manifest: PluginManifest) -> list[str]:
    """Validate a PluginManifest instance."""
    return validate_manifest_dict(manifest.to_dict())


def _is_valid_semver(version: str) -> bool:
    """Basic semver check: X.Y.Z with optional pre-release labels."""
    parts = version.split(".")
    if len(parts) != 3:
        return False
    for part in parts[:2]:
        if not part.isdigit():
            return False
    # Last part may contain pre-release suffix: "1.0.0-alpha"
    main_part = parts[2].split("-")[0]
    if not main_part.isdigit():
        return False
    return True


# ── Helpers ──────────────────────────────────────────────────────────────────


def parse_manifest_file(filepath: str) -> PluginManifest:
    """Read a manifest JSON file and return a validated PluginManifest.

    Raises ManifestValidationError if validation fails.
    """
    with open(filepath, encoding="utf-8") as f:
        data = json.load(f)
    errors = validate_manifest_dict(data)
    if errors:
        raise ManifestValidationError(errors)
    return PluginManifest.from_dict(data)
