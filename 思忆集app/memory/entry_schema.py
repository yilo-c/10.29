"""Supertag schema system — structured type definitions for memory tags.

Inspired by Tana Supertags, this module lets tags carry structured fields:
- @任务: deadline (date), priority (enum), status (enum)
- @联系人: phone (string), email (string), relation (string)
- @项目: status (enum), owner (string), deadline (date), tags (string[])

Schema format:
    {
        "type": str,            # supertag type name, e.g. "@任务"
        "description": str,
        "fields": [             # list of field definitions
            {
                "name": str,
                "type": str,    # string | number | boolean | date | enum | reference
                "required": bool,
                "validation": dict,  # type-specific validation rules
            }
        ]
    }

Structured memory fields available on all entries:
- entity_type: person | task | project | event | note | contact | custom
- relations: list of {type, target_id, label} relationship descriptors
- source: origin of the memory (conversation, manual, import, sync, system)
- created_at / updated_at: ISO-format timestamps
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger("siyi.supertag.schema")

# ── Field type constants ───────────────────────────────────────────────

FIELD_TYPE_STRING = "string"
FIELD_TYPE_NUMBER = "number"
FIELD_TYPE_BOOLEAN = "boolean"
FIELD_TYPE_DATE = "date"
FIELD_TYPE_ENUM = "enum"
FIELD_TYPE_REFERENCE = "reference"

VALID_FIELD_TYPES = frozenset({
    FIELD_TYPE_STRING,
    FIELD_TYPE_NUMBER,
    FIELD_TYPE_BOOLEAN,
    FIELD_TYPE_DATE,
    FIELD_TYPE_ENUM,
    FIELD_TYPE_REFERENCE,
})

# ── Entity type constants ──────────────────────────────────────────────

ENTITY_TYPE_PERSON = "person"
ENTITY_TYPE_TASK = "task"
ENTITY_TYPE_PROJECT = "project"
ENTITY_TYPE_EVENT = "event"
ENTITY_TYPE_NOTE = "note"
ENTITY_TYPE_CONTACT = "contact"
ENTITY_TYPE_CUSTOM = "custom"

VALID_ENTITY_TYPES = frozenset({
    ENTITY_TYPE_PERSON,
    ENTITY_TYPE_TASK,
    ENTITY_TYPE_PROJECT,
    ENTITY_TYPE_EVENT,
    ENTITY_TYPE_NOTE,
    ENTITY_TYPE_CONTACT,
    ENTITY_TYPE_CUSTOM,
})

# ── Relation type constants ────────────────────────────────────────────

RELATION_RELATES_TO = "relates_to"
RELATION_DEPENDS_ON = "depends_on"
RELATION_REFERENCES = "references"
RELATION_PARENT_OF = "parent_of"
RELATION_CHILD_OF = "child_of"
RELATION_PART_OF = "part_of"
RELATION_SIBLING_OF = "sibling_of"
RELATION_FOLLOWS = "follows"

VALID_RELATION_TYPES = frozenset({
    RELATION_RELATES_TO,
    RELATION_DEPENDS_ON,
    RELATION_REFERENCES,
    RELATION_PARENT_OF,
    RELATION_CHILD_OF,
    RELATION_PART_OF,
    RELATION_SIBLING_OF,
    RELATION_FOLLOWS,
})

# ── Source type constants ──────────────────────────────────────────────

SOURCE_CONVERSATION = "conversation"
SOURCE_MANUAL = "manual"
SOURCE_IMPORT = "import"
SOURCE_SYNC = "sync"
SOURCE_SYSTEM = "system"

# ── Supertag hierarchy / auto-tag constants ────────────────────────────

SUPERTAG_SEPARATOR = ":"
SUPERTAG_GROUP_PREFIX = "@group:"
SUPERTAG_RULE_PREFIX = "@rule:"

# ── 3 built-in supertag schemas ────────────────────────────────────────

BUILTIN_SCHEMAS: dict[str, dict[str, Any]] = {}

def _register_builtin_schemas():
    """Register the 3 built-in supertag schemas with structured memory fields."""
    _ST = _structured_fields  # shorthand
    schemas = {
        "@任务": {
            "type": "@任务",
            "description": "任务跟踪：截止日期、优先级、状态",
            "entity_type": ENTITY_TYPE_TASK,
            "fields": _ST([
                {
                    "name": "deadline",
                    "type": FIELD_TYPE_DATE,
                    "required": False,
                    "validation": {},
                },
                {
                    "name": "priority",
                    "type": FIELD_TYPE_ENUM,
                    "required": False,
                    "validation": {
                        "options": ["low", "medium", "high", "urgent"],
                    },
                },
                {
                    "name": "status",
                    "type": FIELD_TYPE_ENUM,
                    "required": False,
                    "validation": {
                        "options": ["todo", "in_progress", "done", "blocked"],
                    },
                },
            ]),
        },
        "@联系人": {
            "type": "@联系人",
            "description": "联系人信息：电话、邮箱、关系、备注",
            "entity_type": ENTITY_TYPE_CONTACT,
            "fields": _ST([
                {
                    "name": "phone",
                    "type": FIELD_TYPE_STRING,
                    "required": False,
                    "validation": {},
                },
                {
                    "name": "email",
                    "type": FIELD_TYPE_STRING,
                    "required": False,
                    "validation": {},
                },
                {
                    "name": "relation",
                    "type": FIELD_TYPE_STRING,
                    "required": False,
                    "validation": {},
                },
                {
                    "name": "notes",
                    "type": FIELD_TYPE_STRING,
                    "required": False,
                    "validation": {},
                },
            ]),
        },
        "@项目": {
            "type": "@项目",
            "description": "项目管理：状态、负责人、截止日期、标签",
            "entity_type": ENTITY_TYPE_PROJECT,
            "fields": _ST([
                {
                    "name": "status",
                    "type": FIELD_TYPE_ENUM,
                    "required": False,
                    "validation": {
                        "options": ["active", "paused", "completed", "archived"],
                    },
                },
                {
                    "name": "owner",
                    "type": FIELD_TYPE_STRING,
                    "required": False,
                    "validation": {},
                },
                {
                    "name": "deadline",
                    "type": FIELD_TYPE_DATE,
                    "required": False,
                    "validation": {},
                },
                {
                    "name": "tags",
                    "type": FIELD_TYPE_STRING,  # stored as JSON string array
                    "required": False,
                    "validation": {"is_array": True},
                },
            ]),
        },
        "@知识领域": {
            "type": "@知识领域",
            "description": "知识体系中的领域节点，用于组织记忆与学习路径",
            "entity_type": ENTITY_TYPE_NOTE,
            "fields": _ST([
                {
                    "name": "name",
                    "type": FIELD_TYPE_STRING,
                    "required": True,
                    "validation": {"max_length": 120},
                },
                {
                    "name": "description",
                    "type": FIELD_TYPE_STRING,
                    "required": False,
                    "validation": {"max_length": 2000},
                },
                {
                    "name": "parent_domain",
                    "type": FIELD_TYPE_REFERENCE,
                    "required": False,
                    "validation": {},
                },
                {
                    "name": "maturity",
                    "type": FIELD_TYPE_ENUM,
                    "required": False,
                    "validation": {
                        "options": ["exploring", "learning", "proficient", "expert"],
                    },
                },
                {
                    "name": "area",
                    "type": FIELD_TYPE_ENUM,
                    "required": False,
                    "validation": {
                        "options": ["tech", "science", "business", "life", "other"],
                    },
                },
                {
                    "name": "tags",
                    "type": FIELD_TYPE_STRING,
                    "required": False,
                    "validation": {"is_array": True},
                },
            ]),
        },
        "@个人档案": {
            "type": "@个人档案",
            "description": "用户个人信息画像：事实、偏好、技能、目标等",
            "entity_type": ENTITY_TYPE_PERSON,
            "fields": _ST([
                {
                    "name": "key",
                    "type": FIELD_TYPE_STRING,
                    "required": True,
                    "validation": {"max_length": 120},
                },
                {
                    "name": "value",
                    "type": FIELD_TYPE_STRING,
                    "required": True,
                    "validation": {"max_length": 2000},
                },
                {
                    "name": "category",
                    "type": FIELD_TYPE_ENUM,
                    "required": False,
                    "validation": {
                        "options": ["basic", "skill", "interest", "goal", "value", "relationship"],
                    },
                },
                {
                    "name": "confidence",
                    "type": FIELD_TYPE_NUMBER,
                    "required": False,
                    "validation": {"min": 0.0, "max": 1.0},
                },
                {
                    "name": "source_evidence",
                    "type": FIELD_TYPE_STRING,
                    "required": False,
                    "validation": {"max_length": 2000},
                },
                {
                    "name": "tags",
                    "type": FIELD_TYPE_STRING,
                    "required": False,
                    "validation": {"is_array": True},
                },
            ]),
        },
    }
    BUILTIN_SCHEMAS.update(schemas)


def _structured_fields(custom_fields: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Prepend structured memory fields (entity_type, relations, source, timestamps)
    to custom field definitions. Every supertag schema shares these base fields."""
    base = [
        {
            "name": "entity_type",
            "type": FIELD_TYPE_STRING,
            "required": False,
            "validation": {},
        },
        {
            "name": "relations",
            "type": FIELD_TYPE_STRING,  # JSON array of {type, target_id, label}
            "required": False,
            "validation": {"is_array": True},
        },
        {
            "name": "source",
            "type": FIELD_TYPE_STRING,
            "required": False,
            "validation": {},
        },
        {
            "name": "created_at",
            "type": FIELD_TYPE_STRING,
            "required": False,
            "validation": {},
        },
        {
            "name": "updated_at",
            "type": FIELD_TYPE_STRING,
            "required": False,
            "validation": {},
        },
    ]
    return base + list(custom_fields)


_register_builtin_schemas()


def make_structured_entry(
    *,
    supertag_type: str,
    field_values: dict[str, Any] | None = None,
    entity_type: str = "",
    relations: list[dict[str, str]] | None = None,
    source: str = SOURCE_MANUAL,
) -> dict[str, Any]:
    """Build a structured memory entry dict with ISO timestamps.

    Args:
        supertag_type: The supertag type (e.g. "@任务").
        field_values: Schema field values keyed by field name.
        entity_type: Optional entity type override.
        relations: List of {type, target_id, label} dicts.
        source: Origin of this memory entry.

    Returns:
        Dict with structured fields + user field values.
    """
    now = datetime.now(timezone.utc).isoformat()
    entry: dict[str, Any] = {
        "entity_type": entity_type or "",
        "relations": relations or [],
        "source": source,
        "created_at": now,
        "updated_at": now,
        "supertag_type": supertag_type,
    }
    if field_values:
        entry.update(field_values)
    return entry


def update_structured_entry(
    entry: dict[str, Any],
    updates: dict[str, Any],
) -> dict[str, Any]:
    """Produce a new entry dict with updated fields and refreshed updated_at.

    Args:
        entry: Existing structured entry dict.
        updates: Fields to update.

    Returns:
        New dict (immutable — original is not mutated).
    """
    result = dict(entry)
    result.update(updates)
    result["updated_at"] = datetime.now(timezone.utc).isoformat()
    return result


def get_supertag_entity_type(schema: dict[str, Any]) -> str:
    """Get the default entity type for a supertag schema."""
    return str(schema.get("entity_type") or "")


def validate_relation(rel: Any) -> tuple[bool, str]:
    """Validate a single relation dict."""
    if not isinstance(rel, dict):
        return False, "Relation must be a dict"
    rel_type = rel.get("type", "")
    if rel_type not in VALID_RELATION_TYPES:
        return False, f"Invalid relation type: {rel_type!r}"
    if not rel.get("target_id"):
        return False, "Relation requires target_id"
    return True, ""

# ── Validation ─────────────────────────────────────────────────────────

def validate_field_value(
    field_def: dict[str, Any],
    value: Any,
) -> tuple[bool, str]:
    """Validate a single field value against its schema definition.

    Args:
        field_def: Field definition dict (name, type, required, validation).
        value: Value to validate (can be None for non-required fields).

    Returns:
        (is_valid, error_message)
    """
    field_name = field_def.get("name", "")
    field_type = field_def.get("type", FIELD_TYPE_STRING)
    required = field_def.get("required", False)
    validation = field_def.get("validation", {}) or {}

    # None check
    if value is None or (isinstance(value, str) and not value.strip()):
        if required:
            return False, f"字段 '{field_name}' 是必填的"
        return True, ""

    # Type-specific validation
    if field_type == FIELD_TYPE_STRING:
        if not isinstance(value, str):
            return False, f"字段 '{field_name}' 应为字符串"
        max_len = validation.get("max_length")
        if max_len is not None and len(value) > max_len:
            return False, f"字段 '{field_name}' 最大长度 {max_len}"
        is_array = validation.get("is_array", False)
        if is_array:
            try:
                parsed = json.loads(value) if isinstance(value, str) else value
                if not isinstance(parsed, list):
                    return False, f"字段 '{field_name}' 应为数组"
            except (json.JSONDecodeError, TypeError):
                return False, f"字段 '{field_name}' 应为 JSON 数组字符串"
        return True, ""

    elif field_type == FIELD_TYPE_NUMBER:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return False, f"字段 '{field_name}' 应为数字"
        min_val = validation.get("min")
        max_val = validation.get("max")
        if min_val is not None and value < min_val:
            return False, f"字段 '{field_name}' 最小值为 {min_val}"
        if max_val is not None and value > max_val:
            return False, f"字段 '{field_name}' 最大值为 {max_val}"
        return True, ""

    elif field_type == FIELD_TYPE_BOOLEAN:
        if not isinstance(value, bool):
            return False, f"字段 '{field_name}' 应为布尔值"
        return True, ""

    elif field_type == FIELD_TYPE_DATE:
        if isinstance(value, str):
            if not _is_valid_date(value):
                return False, f"字段 '{field_name}' 日期格式无效 (YYYY-MM-DD)"
            return True, ""
        if isinstance(value, datetime):
            return True, ""
        return False, f"字段 '{field_name}' 应为日期 (YYYY-MM-DD)"

    elif field_type == FIELD_TYPE_ENUM:
        options = validation.get("options", [])
        if value not in options:
            opts_str = ", ".join(options)
            return False, f"字段 '{field_name}' 必须是 [{opts_str}] 之一，收到 '{value}'"
        return True, ""

    elif field_type == FIELD_TYPE_REFERENCE:
        if not isinstance(value, str) or not value.strip():
            return False, f"字段 '{field_name}' 应为引用 ID 字符串"
        return True, ""

    return True, ""


def _is_valid_date(value: str) -> bool:
    """Check if a string is a valid date (YYYY-MM-DD or ISO format)."""
    if not value or not isinstance(value, str):
        return False
    # Try YYYY-MM-DD first (most common)
    if re.match(r"^\d{4}-\d{2}-\d{2}$", value):
        try:
            datetime.strptime(value, "%Y-%m-%d")
            return True
        except ValueError:
            return False
    # Try full ISO format
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
        return True
    except (ValueError, TypeError):
        return False


def validate_supertag_data(
    schema: dict[str, Any],
    data: dict[str, Any],
) -> list[dict[str, Any]]:
    """Validate all fields of a supertag data dict against schema.

    Args:
        schema: Supertag schema dict.
        data: Field values dict {field_name: value}.

    Returns:
        List of error dicts {field, message} (empty if valid).
    """
    errors: list[dict[str, Any]] = []
    fields = schema.get("fields", [])

    # Check all defined fields
    for field_def in fields:
        field_name = field_def.get("name", "")
        value = data.get(field_name)
        is_valid, msg = validate_field_value(field_def, value)
        if not is_valid:
            errors.append({"field": field_name, "message": msg})

    # Unknown fields are silently ignored (caller handles filtering)

    return errors


# ── Schema registry ────────────────────────────────────────────────────

class SupertagSchemaRegistry:
    """Registry for supertag schemas with built-in + custom support."""

    def __init__(self, data_store=None):
        self._data_store = data_store
        self._custom_schemas: dict[str, dict[str, Any]] = {}
        self._load_custom()

    # ── Public API ──────────────────────────────────────────────────────

    def get_schema(self, supertag_type: str) -> dict[str, Any] | None:
        """Get schema by supertag type (e.g. '@任务')."""
        if supertag_type in BUILTIN_SCHEMAS:
            return dict(BUILTIN_SCHEMAS[supertag_type])
        if supertag_type in self._custom_schemas:
            return dict(self._custom_schemas[supertag_type])
        return None

    def list_schemas(self) -> dict[str, dict[str, Any]]:
        """Return all schemas (built-in + custom) keyed by type."""
        result = {}
        result.update(BUILTIN_SCHEMAS)
        result.update(self._custom_schemas)
        return result

    def register_schema(self, schema: dict[str, Any]) -> dict[str, Any]:
        """Register a custom supertag schema. Returns result dict."""
        errors = self._validate_schema(schema)
        if errors:
            return {"success": False, "errors": errors}

        supertag_type = schema["type"]
        self._custom_schemas[supertag_type] = schema
        self._save_custom()
        logger.info("Registered custom supertag schema: %s", supertag_type)
        return {"success": True}

    def unregister_schema(self, supertag_type: str) -> bool:
        """Remove a custom schema (built-in schemas cannot be removed)."""
        if supertag_type in BUILTIN_SCHEMAS:
            logger.warning("Cannot remove built-in supertag schema: %s", supertag_type)
            return False
        if supertag_type in self._custom_schemas:
            del self._custom_schemas[supertag_type]
            self._save_custom()
            logger.info("Removed custom supertag schema: %s", supertag_type)
            return True
        return False

    def is_supertag(self, tag: str) -> bool:
        """Check if a tag string is a known supertag type."""
        return tag in BUILTIN_SCHEMAS or tag in self._custom_schemas

    def get_supertag_type_from_tag(self, tag: str) -> str | None:
        """If a tag maps to a supertag, return the supertag type. Otherwise None."""
        if tag in BUILTIN_SCHEMAS:
            return tag
        if tag in self._custom_schemas:
            return tag
        # Try prefix matching: e.g. "@任务" matches "@任务:work"
        for st_type in list(BUILTIN_SCHEMAS) + list(self._custom_schemas):
            if tag.startswith(st_type):
                return st_type
        return None

    def extract_supertags_from_tags(self, tags: list[str]) -> list[tuple[str, str]]:
        """From a list of tags, extract (supertag_type, original_tag) pairs."""
        result: list[tuple[str, str]] = []
        for tag in tags:
            st_type = self.get_supertag_type_from_tag(tag)
            if st_type:
                result.append((st_type, tag))
        return result

    # ── Internal ────────────────────────────────────────────────────────

    @staticmethod
    def _validate_schema(schema: dict[str, Any]) -> list[str]:
        """Validate a schema definition."""
        errors: list[str] = []
        if not isinstance(schema, dict):
            return ["Schema must be a dict"]

        stype = schema.get("type")
        if not stype or not isinstance(stype, str) or not stype.startswith("@"):
            errors.append("Schema 'type' must be a non-empty string starting with @")

        fields = schema.get("fields")
        if not isinstance(fields, list):
            errors.append("Schema 'fields' must be a list")
            return errors

        seen_names: set[str] = set()
        for i, field in enumerate(fields):
            if not isinstance(field, dict):
                errors.append(f"Field[{i}] must be a dict")
                continue
            fname = field.get("name")
            if not fname or not isinstance(fname, str):
                errors.append(f"Field[{i}]: 'name' is required")
                continue
            if fname in seen_names:
                errors.append(f"Duplicate field name: '{fname}'")
            seen_names.add(fname)

            ftype = field.get("type")
            if ftype not in VALID_FIELD_TYPES:
                errors.append(f"Field '{fname}': invalid type '{ftype}'")

            if ftype == FIELD_TYPE_ENUM:
                options = (field.get("validation") or {}).get("options")
                if not options or not isinstance(options, list) or len(options) < 2:
                    errors.append(f"Field '{fname}': enum type requires 'validation.options' with >=2 items")

        return errors

    def _load_custom(self):
        """Load custom schemas from data store."""
        if self._data_store is None:
            return
        try:
            data = self._data_store.load_json("supertag_schemas", [])
            if isinstance(data, list):
                for schema in data:
                    if isinstance(schema, dict) and schema.get("type"):
                        self._custom_schemas[schema["type"]] = schema
        except Exception as exc:
            logger.warning("Failed to load custom schemas: %s", exc, exc_info=True)

    def _save_custom(self):
        """Save custom schemas to data store."""
        if self._data_store is None:
            return
        try:
            schemas = list(self._custom_schemas.values())
            self._data_store.save_json("supertag_schemas", schemas)
        except Exception as exc:
            logger.warning("Failed to save custom schemas: %s", exc, exc_info=True)


# ── Supertag hierarchy ────────────────────────────────────────────────


class SupertagHierarchy:
    """Manages parent/child relationships between supertags.

    Hierarchy is stored as a flat list of edges: [{parent, child, label}].
    Supports tree traversal (children, parents, siblings, descendants, ancestors).
    """

    def __init__(self, data_store=None):
        self._data_store = data_store
        self._edges: list[dict[str, str]] = []
        self._load()

    # ── Public API ──────────────────────────────────────────────────────

    def add_edge(self, parent: str, child: str, label: str = "") -> None:
        """Add a parent-child relationship."""
        if not parent or not child:
            return
        # Deduplicate
        self._edges = [e for e in self._edges if not (e["parent"] == parent and e["child"] == child)]
        self._edges.append({"parent": parent, "child": child, "label": label or child})
        self._save()

    def remove_edge(self, parent: str, child: str) -> bool:
        """Remove a parent-child relationship. Returns True if removed."""
        before = len(self._edges)
        self._edges = [e for e in self._edges if not (e["parent"] == parent and e["child"] == child)]
        changed = len(self._edges) < before
        if changed:
            self._save()
        return changed

    def get_children(self, parent: str) -> list[dict[str, str]]:
        """Get direct children of a supertag."""
        return [dict(e) for e in self._edges if e["parent"] == parent]

    def get_parents(self, child: str) -> list[dict[str, str]]:
        """Get direct parents of a supertag."""
        return [dict(e) for e in self._edges if e["child"] == child]

    def get_siblings(self, tag: str) -> list[str]:
        """Get sibling tags (same parent, excluding self)."""
        parents = [e["parent"] for e in self._edges if e["child"] == tag]
        siblings: set[str] = set()
        for p in parents:
            for e in self._edges:
                if e["parent"] == p and e["child"] != tag:
                    siblings.add(e["child"])
        return sorted(siblings)

    def get_descendants(self, tag: str, depth: int = -1) -> list[str]:
        """Get all descendants (recursive children). depth=-1 for unlimited."""
        result: list[str] = []
        queue: list[tuple[str, int]] = [(tag, 0)]
        while queue:
            current, d = queue.pop(0)
            if 0 <= depth <= d:
                continue
            for e in self._edges:
                if e["parent"] == current:
                    if e["child"] not in result:
                        result.append(e["child"])
                        queue.append((e["child"], d + 1))
        return result

    def get_ancestors(self, tag: str) -> list[str]:
        """Get all ancestors (recursive parents)."""
        result: list[str] = []
        queue = [tag]
        while queue:
            current = queue.pop(0)
            for e in self._edges:
                if e["child"] == current and e["parent"] not in result:
                    result.append(e["parent"])
                    queue.append(e["parent"])
        return result

    def get_tree(self, root: str | None = None, depth: int = -1) -> list[dict[str, Any]]:
        """Build a nested tree structure.

        Each node: {tag, label, children: [...]}
        If root is None, returns all root tags (tags that have children but no parents).
        """
        all_tags = set()
        for e in self._edges:
            all_tags.add(e["parent"])
            all_tags.add(e["child"])

        if root is None:
            # Tags that are parents but never appear as children → roots
            parents = {e["parent"] for e in self._edges}
            children = {e["child"] for e in self._edges}
            roots = sorted(parents - children)
        else:
            roots = [root]

        def _build(node: str, remaining_depth: int) -> dict[str, Any]:
            children_list = self.get_children(node)
            label = children_list[0]["label"] if children_list else node
            node_data: dict[str, Any] = {"tag": node, "label": label, "children": []}
            if remaining_depth != 0:
                for e in self._edges:
                    if e["parent"] == node:
                        node_data["children"].append(_build(e["child"], remaining_depth - 1 if remaining_depth > 0 else -1))
            return node_data

        return [_build(r, depth) for r in roots if r in all_tags]

    def clear(self) -> None:
        """Remove all hierarchy edges."""
        self._edges.clear()
        self._save()

    # ── Internal ────────────────────────────────────────────────────────

    def _load(self) -> None:
        if self._data_store is None:
            return
        try:
            data = self._data_store.load_json("supertag_hierarchy", [])
            if isinstance(data, list):
                self._edges = [dict(e) for e in data if isinstance(e, dict) and e.get("parent") and e.get("child")]
        except Exception as exc:
            logger.warning("Failed to load supertag hierarchy: %s", exc, exc_info=True)

    def _save(self) -> None:
        if self._data_store is None:
            return
        try:
            self._data_store.save_json("supertag_hierarchy", self._edges)
        except Exception as exc:
            logger.warning("Failed to save supertag hierarchy: %s", exc, exc_info=True)


# ── Supertag groups ────────────────────────────────────────────────────


class SupertagGroupManager:
    """Named groups of supertags for convenient categorization."""

    def __init__(self, data_store=None):
        self._data_store = data_store
        self._groups: dict[str, list[str]] = {}
        self._load()

    def list_groups(self) -> dict[str, list[str]]:
        """Return all groups {group_name: [tag, ...]}."""
        return dict(self._groups)

    def get_group(self, name: str) -> list[str]:
        """Get tags in a group."""
        return list(self._groups.get(name, []))

    def create_group(self, name: str, tags: list[str] | None = None) -> bool:
        """Create a new tag group. Returns False if name already exists."""
        if not name or name in self._groups:
            return False
        self._groups[name] = list(tags or [])
        self._save()
        return True

    def delete_group(self, name: str) -> bool:
        """Delete a tag group. Returns True if deleted."""
        if name not in self._groups:
            return False
        del self._groups[name]
        self._save()
        return True

    def add_tag_to_group(self, group_name: str, tag: str) -> bool:
        """Add tag to group. Returns False if group not found."""
        if group_name not in self._groups:
            return False
        if tag not in self._groups[group_name]:
            self._groups[group_name].append(tag)
            self._save()
        return True

    def remove_tag_from_group(self, group_name: str, tag: str) -> bool:
        """Remove tag from group. Returns True if removed."""
        if group_name not in self._groups:
            return False
        if tag in self._groups[group_name]:
            self._groups[group_name] = [t for t in self._groups[group_name] if t != tag]
            self._save()
            return True
        return False

    def _load(self) -> None:
        if self._data_store is None:
            return
        try:
            data = self._data_store.load_json("supertag_groups", {})
            if isinstance(data, dict):
                self._groups = {k: list(v) for k, v in data.items() if isinstance(v, (list, tuple))}
        except Exception as exc:
            logger.warning("Failed to load supertag groups: %s", exc, exc_info=True)

    def _save(self) -> None:
        if self._data_store is None:
            return
        try:
            self._data_store.save_json("supertag_groups", self._groups)
        except Exception as exc:
            logger.warning("Failed to save supertag groups: %s", exc, exc_info=True)


# ── Auto-tag rules ─────────────────────────────────────────────────────


class AutoTagRule:
    """A single auto-tag rule: when conditions match, apply tags with optional structured data."""

    def __init__(
        self,
        *,
        rule_id: str = "",
        name: str = "",
        enabled: bool = True,
        match_keywords: list[str] | None = None,
        match_regex: list[str] | None = None,
        match_supertag_types: list[str] | None = None,
        apply_tags: list[str] | None = None,
        apply_supertag_fields: dict[str, dict[str, Any]] | None = None,
        priority: int = 0,
    ):
        self.rule_id = rule_id or _generate_rule_id()
        self.name = name
        self.enabled = enabled
        self.match_keywords = list(match_keywords or [])
        self.match_regex = list(match_regex or [])
        self.match_supertag_types = list(match_supertag_types or [])
        self.apply_tags = list(apply_tags or [])
        self.apply_supertag_fields = dict(apply_supertag_fields or {})
        self.priority = priority

    def matches(self, text: str, existing_tags: list[str] | None = None) -> bool:
        """Check if this rule matches the given text and existing tags."""
        if not self.enabled:
            return False
        lower = text.lower()

        # Keyword match (OR within list)
        if self.match_keywords:
            if not any(kw.lower() in lower for kw in self.match_keywords):
                return False

        # Regex match (OR within list)
        if self.match_regex:
            if not any(re.search(pat, text) for pat in self.match_regex):
                return False

        # Supertag type match (existing tags contain specific supertags)
        if self.match_supertag_types:
            if not existing_tags:
                return False
            etags = set(existing_tags)
            if not any(st in etags for st in self.match_supertag_types):
                return False

        return True

    def to_dict(self) -> dict[str, Any]:
        return {
            "rule_id": self.rule_id,
            "name": self.name,
            "enabled": self.enabled,
            "match_keywords": list(self.match_keywords),
            "match_regex": list(self.match_regex),
            "match_supertag_types": list(self.match_supertag_types),
            "apply_tags": list(self.apply_tags),
            "apply_supertag_fields": dict(self.apply_supertag_fields),
            "priority": self.priority,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AutoTagRule:
        return cls(
            rule_id=str(data.get("rule_id", "")),
            name=str(data.get("name", "")),
            enabled=bool(data.get("enabled", True)),
            match_keywords=list(data.get("match_keywords") or []),
            match_regex=list(data.get("match_regex") or []),
            match_supertag_types=list(data.get("match_supertag_types") or []),
            apply_tags=list(data.get("apply_tags") or []),
            apply_supertag_fields=dict(data.get("apply_supertag_fields") or {}),
            priority=int(data.get("priority", 0)),
        )


def _generate_rule_id() -> str:
    return f"rule_{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S%f')}"


class AutoTagRuleEngine:
    """Evaluates auto-tag rules against text and produces tag suggestions."""

    def __init__(self, rules: list[AutoTagRule] | None = None):
        self._rules = sorted(rules or [], key=lambda r: r.priority, reverse=True)

    def load_rules(self, rules: list[AutoTagRule]) -> None:
        self._rules = sorted(rules, key=lambda r: r.priority, reverse=True)

    def add_rule(self, rule: AutoTagRule) -> None:
        self._rules.append(rule)
        self._rules.sort(key=lambda r: r.priority, reverse=True)

    def remove_rule(self, rule_id: str) -> bool:
        before = len(self._rules)
        self._rules = [r for r in self._rules if r.rule_id != rule_id]
        return len(self._rules) < before

    def evaluate(
        self,
        text: str,
        existing_tags: list[str] | None = None,
        max_results: int = 5,
    ) -> list[dict[str, Any]]:
        """Evaluate all enabled rules against text and return matched suggestions."""
        suggestions: list[dict[str, Any]] = []
        for rule in self._rules:
            if rule.matches(text, existing_tags):
                suggestions.append({
                    "rule_id": rule.rule_id,
                    "rule_name": rule.name,
                    "apply_tags": list(rule.apply_tags),
                    "apply_supertag_fields": dict(rule.apply_supertag_fields),
                    "priority": rule.priority,
                })
                if len(suggestions) >= max_results:
                    break
        return suggestions
