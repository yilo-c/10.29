"""Supertag schema constants — extracted from entry_schema.py for file size management.

Original file: entry_schema.py (used to be 935+ lines, constants extracted here)
"""
from __future__ import annotations

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

# ── 中文显示名映射（不改存储值，仅用于前端展示） ──────────

ENTITY_TYPE_DISPLAY: dict[str, str] = {
    ENTITY_TYPE_PERSON:   "人物",
    ENTITY_TYPE_TASK:     "任务",
    ENTITY_TYPE_PROJECT:  "项目",
    ENTITY_TYPE_EVENT:    "事件",
    ENTITY_TYPE_NOTE:     "笔记",
    ENTITY_TYPE_CONTACT:  "联系人",
    ENTITY_TYPE_CUSTOM:   "自定义",
}

RELATION_DISPLAY: dict[str, str] = {
    RELATION_RELATES_TO:   "关联",
    RELATION_DEPENDS_ON:   "依赖",
    RELATION_REFERENCES:   "引用",
    RELATION_PARENT_OF:    "父节点",
    RELATION_CHILD_OF:     "子节点",
    RELATION_PART_OF:      "属于",
    RELATION_SIBLING_OF:   "同级",
    RELATION_FOLLOWS:      "后续",
}
