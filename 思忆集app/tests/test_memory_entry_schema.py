"""Tests for memory_entry_schema — supertag schema definitions, validation, and registry."""
from __future__ import annotations

import sys
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

import pytest
from memory.entry_schema import (
    BUILTIN_SCHEMAS,
    FIELD_TYPE_ENUM,
    FIELD_TYPE_DATE,
    FIELD_TYPE_STRING,
    FIELD_TYPE_NUMBER,
    FIELD_TYPE_BOOLEAN,
    FIELD_TYPE_REFERENCE,
    VALID_FIELD_TYPES,
    SupertagSchemaRegistry,
    validate_field_value,
    validate_supertag_data,
)


class TestBuiltinSchemas:
    """Built-in supertag schemas are correctly defined."""

    def test_has_three_builtin_schemas(self):
        """Three built-in supertag schemas exist."""
        assert "@任务" in BUILTIN_SCHEMAS
        assert "@联系人" in BUILTIN_SCHEMAS
        assert "@项目" in BUILTIN_SCHEMAS

    def test_task_schema_fields(self):
        """@任务 schema has correct fields."""
        schema = BUILTIN_SCHEMAS["@任务"]
        fields = {f["name"]: f for f in schema["fields"]}

        assert fields["deadline"]["type"] == FIELD_TYPE_DATE
        assert fields["deadline"]["required"] is False

        assert fields["priority"]["type"] == FIELD_TYPE_ENUM
        assert fields["priority"]["validation"]["options"] == ["low", "medium", "high", "urgent"]

        assert fields["status"]["type"] == FIELD_TYPE_ENUM
        assert fields["status"]["validation"]["options"] == ["todo", "in_progress", "done", "blocked"]

    def test_contact_schema_fields(self):
        """@联系人 schema has correct fields."""
        schema = BUILTIN_SCHEMAS["@联系人"]
        fields = {f["name"]: f for f in schema["fields"]}

        assert fields["phone"]["type"] == FIELD_TYPE_STRING
        assert fields["email"]["type"] == FIELD_TYPE_STRING
        assert fields["relation"]["type"] == FIELD_TYPE_STRING
        assert fields["notes"]["type"] == FIELD_TYPE_STRING

    def test_project_schema_fields(self):
        """@项目 schema has correct fields."""
        schema = BUILTIN_SCHEMAS["@项目"]
        fields = {f["name"]: f for f in schema["fields"]}

        assert fields["status"]["type"] == FIELD_TYPE_ENUM
        assert fields["status"]["validation"]["options"] == ["active", "paused", "completed", "archived"]
        assert fields["owner"]["type"] == FIELD_TYPE_STRING
        assert fields["deadline"]["type"] == FIELD_TYPE_DATE


class TestFieldValidation:
    """Field value validation against schema definitions."""

    def test_string_field_valid(self):
        """String field accepts valid string."""
        field_def = {"name": "name", "type": "string"}
        is_valid, msg = validate_field_value(field_def, "张三")
        assert is_valid

    def test_string_field_empty_optional(self):
        """Empty string for optional field is valid."""
        field_def = {"name": "name", "type": "string", "required": False}
        is_valid, msg = validate_field_value(field_def, "")
        assert is_valid

    def test_string_field_required_empty(self):
        """Empty string for required field is invalid."""
        field_def = {"name": "name", "type": "string", "required": True}
        is_valid, msg = validate_field_value(field_def, "")
        assert not is_valid

    def test_number_field_valid(self):
        """Number field accepts int and float."""
        field_def = {"name": "count", "type": "number"}
        assert validate_field_value(field_def, 42)[0]
        assert validate_field_value(field_def, 3.14)[0]

    def test_number_field_invalid(self):
        """Number field rejects non-numbers."""
        field_def = {"name": "count", "type": "number"}
        is_valid, msg = validate_field_value(field_def, "not_a_number")
        assert not is_valid

    def test_boolean_field_valid(self):
        """Boolean field accepts bool only."""
        field_def = {"name": "flag", "type": "boolean"}
        assert validate_field_value(field_def, True)[0]
        assert validate_field_value(field_def, False)[0]

    def test_boolean_field_invalid(self):
        """Boolean field rejects strings."""
        field_def = {"name": "flag", "type": "boolean"}
        is_valid, msg = validate_field_value(field_def, "true")
        assert not is_valid

    def test_date_field_valid(self):
        """Date field accepts YYYY-MM-DD strings."""
        field_def = {"name": "deadline", "type": "date"}
        is_valid, msg = validate_field_value(field_def, "2026-05-15")
        assert is_valid

    def test_date_field_invalid(self):
        """Date field rejects invalid date strings."""
        field_def = {"name": "deadline", "type": "date"}
        is_valid, msg = validate_field_value(field_def, "not-a-date")
        assert not is_valid

    def test_enum_field_valid(self):
        """Enum field accepts valid options."""
        field_def = {
            "name": "priority",
            "type": "enum",
            "validation": {"options": ["low", "medium", "high", "urgent"]},
        }
        for option in ["low", "medium", "high", "urgent"]:
            assert validate_field_value(field_def, option)[0]

    def test_enum_field_invalid(self):
        """Enum field rejects invalid options."""
        field_def = {
            "name": "priority",
            "type": "enum",
            "validation": {"options": ["low", "medium", "high", "urgent"]},
        }
        is_valid, msg = validate_field_value(field_def, "urgenttt")
        assert not is_valid
        assert "urgenttt" in msg

    def test_reference_field_valid(self):
        """Reference field accepts non-empty string."""
        field_def = {"name": "ref", "type": "reference"}
        is_valid, msg = validate_field_value(field_def, "entry_123")
        assert is_valid


class TestValidateSupertagData:
    """Full supertag data validation."""

    def test_valid_task_data(self):
        """Complete valid task data passes validation."""
        schema = BUILTIN_SCHEMAS["@任务"]
        data = {
            "deadline": "2026-06-01",
            "priority": "high",
            "status": "todo",
        }
        errors = validate_supertag_data(schema, data)
        assert errors == []

    def test_partial_task_data(self):
        """Partial task data (only some fields) passes validation."""
        schema = BUILTIN_SCHEMAS["@任务"]
        data = {"priority": "urgent"}
        errors = validate_supertag_data(schema, data)
        assert errors == []

    def test_empty_task_data(self):
        """Empty task data passes validation (all fields optional)."""
        schema = BUILTIN_SCHEMAS["@任务"]
        errors = validate_supertag_data(schema, {})
        assert errors == []

    def test_invalid_priority(self):
        """Invalid enum value is rejected."""
        schema = BUILTIN_SCHEMAS["@任务"]
        data = {"priority": "urgenttt"}
        errors = validate_supertag_data(schema, data)
        assert len(errors) >= 1
        assert errors[0]["field"] == "priority"

    def test_unknown_field_ignored(self):
        """Unknown field in data is silently ignored (no errors)."""
        schema = BUILTIN_SCHEMAS["@任务"]
        data = {"nonexistent_field": "value", "priority": "high"}
        errors = validate_supertag_data(schema, data)
        assert errors == []  # unknown fields don't cause validation errors

    def test_valid_contact_data(self):
        """Valid contact data passes validation."""
        schema = BUILTIN_SCHEMAS["@联系人"]
        data = {"phone": "13800138000", "email": "test@example.com", "relation": "朋友"}
        errors = validate_supertag_data(schema, data)
        assert errors == []

    def test_valid_project_data(self):
        """Valid project data passes validation."""
        schema = BUILTIN_SCHEMAS["@项目"]
        data = {"status": "active", "owner": "张三", "deadline": "2026-12-31"}
        errors = validate_supertag_data(schema, data)
        assert errors == []

    def test_invalid_project_status(self):
        """Invalid project status is rejected."""
        schema = BUILTIN_SCHEMAS["@项目"]
        data = {"status": "nonexistent_status"}
        errors = validate_supertag_data(schema, data)
        assert len(errors) >= 1


class TestSupertagSchemaRegistry:
    """Supertag schema registry."""

    def test_list_schemas(self):
        """Registry lists all built-in schemas."""
        registry = SupertagSchemaRegistry()
        schemas = registry.list_schemas()
        assert "@任务" in schemas
        assert "@联系人" in schemas
        assert "@项目" in schemas

    def test_get_schema(self):
        """Get schema by type."""
        registry = SupertagSchemaRegistry()
        schema = registry.get_schema("@任务")
        assert schema is not None
        assert schema["type"] == "@任务"

    def test_get_unknown_schema(self):
        """Get unknown schema returns None."""
        registry = SupertagSchemaRegistry()
        assert registry.get_schema("@未知") is None

    def test_is_supertag(self):
        """is_supertag checks correctly."""
        registry = SupertagSchemaRegistry()
        assert registry.is_supertag("@任务")
        assert registry.is_supertag("@联系人")
        assert registry.is_supertag("@项目")
        assert not registry.is_supertag("普通标签")
        assert not registry.is_supertag("work")

    def test_get_supertag_type_from_tag(self):
        """Extract supertag type from tag."""
        registry = SupertagSchemaRegistry()
        assert registry.get_supertag_type_from_tag("@任务") == "@任务"
        # Prefix matching
        assert registry.get_supertag_type_from_tag("@任务:work") == "@任务"

    def test_extract_supertags_from_tags(self):
        """Extract supertag pairs from tag list."""
        registry = SupertagSchemaRegistry()
        tags = ["普通标签", "@任务", "@联系人:friend", "无关"]
        pairs = registry.extract_supertags_from_tags(tags)
        assert len(pairs) == 2
        assert ("@任务", "@任务") in pairs
        assert ("@联系人", "@联系人:friend") in pairs

    def test_register_custom_schema(self):
        """Register a custom schema."""
        registry = SupertagSchemaRegistry()
        schema = {
            "type": "@读书笔记",
            "description": "阅读记录",
            "fields": [
                {"name": "author", "type": "string", "required": False, "validation": {}},
                {"name": "rating", "type": "enum", "required": False,
                 "validation": {"options": ["1", "2", "3", "4", "5"]}},
            ],
        }
        result = registry.register_schema(schema)
        assert result["success"] is True
        assert registry.is_supertag("@读书笔记")

    def test_register_invalid_schema(self):
        """Register schema with missing fields is rejected."""
        registry = SupertagSchemaRegistry()
        schema = {"type": "@invalid"}
        result = registry.register_schema(schema)
        assert result["success"] is False
        assert "errors" in result
