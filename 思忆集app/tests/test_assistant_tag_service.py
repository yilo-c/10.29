"""Tests for assistant_tag_service — supertag structured data binding."""
from __future__ import annotations

import sys
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

import pytest
from assistant.tag_service import AssistantTagService


class _MockDataStore:
    """Simple in-memory mock for LocalDataStore."""
    def __init__(self):
        self._data: dict = {}

    def load_json(self, key: str, default=None):
        return self._data.get(key, default)

    def save_json(self, key: str, data):
        self._data[key] = data


class TestAssistantTagService:
    """AssistantTagService CRUD and validation."""

    @pytest.fixture
    def service(self):
        return AssistantTagService(_MockDataStore())

    def test_get_structured_data_empty(self, service):
        """Getting data for non-existent entry returns empty dict."""
        data = service.get_structured_data("nonexistent")
        assert data == {}

    def test_set_and_get_structured_data(self, service):
        """Setting and getting structured data round-trips correctly."""
        result = service.set_structured_data("entry_1", "@任务", {
            "priority": "high",
            "status": "todo",
        })
        assert result["success"] is True

        data = service.get_structured_data("entry_1")
        assert "@任务" in data
        assert data["@任务"]["priority"] == "high"
        assert data["@任务"]["status"] == "todo"

    def test_set_invalid_field_value(self, service):
        """Setting invalid field value returns error."""
        result = service.set_structured_data("entry_1", "@任务", {
            "priority": "urgenttt",  # invalid enum value
        })
        assert result["success"] is False
        assert len(result["errors"]) > 0
        assert result["errors"][0]["field"] == "priority"

    def test_set_unknown_supertag(self, service):
        """Setting data for unknown supertag returns error."""
        result = service.set_structured_data("entry_1", "@未知类型", {
            "field": "value",
        })
        assert result["success"] is False

    def test_remove_structured_data(self, service):
        """Removing structured data works."""
        service.set_structured_data("entry_1", "@任务", {"priority": "high"})
        assert service.remove_structured_data("entry_1") is True
        assert service.get_structured_data("entry_1") == {}

    def test_remove_specific_type(self, service):
        """Removing specific supertag type leaves other types intact."""
        service.set_structured_data("entry_1", "@任务", {"priority": "high"})
        service.set_structured_data("entry_1", "@联系人", {"phone": "13800138000"})

        assert service.remove_structured_data("entry_1", "@任务") is True
        data = service.get_structured_data("entry_1")
        assert "@任务" not in data
        assert "@联系人" in data

    def test_update_structured_data(self, service):
        """Updating merges new fields with existing."""
        service.set_structured_data("entry_1", "@任务", {"priority": "low"})
        service.update_structured_data("entry_1", "@任务", {"priority": "high", "status": "done"})

        data = service.get_structured_data("entry_1")["@任务"]
        assert data["priority"] == "high"
        assert data["status"] == "done"

    def test_enrich_entry_with_supertag_data(self, service):
        """Enriching entry dict adds supertag data."""
        service.set_structured_data("note:1", "@任务", {"priority": "high"})
        entry = {"entry_id": "note:1", "title": "test", "tags_text": "@任务"}
        enriched = service.enrich_entry_with_supertag_data(entry)

        assert "supertag_data" in enriched
        assert enriched["supertag_data"]["@任务"]["priority"] == "high"
        assert "supertag_flags" in enriched

    def test_enrich_entry_no_supertag(self, service):
        """Entry without supertag data is unchanged."""
        entry = {"entry_id": "note:1", "title": "test"}
        enriched = service.enrich_entry_with_supertag_data(entry)
        # Should be a new dict if supertag_data was added, or same if not
        assert enriched.get("supertag_data") is None
        assert enriched.get("supertag_flags") is None

    def test_ignores_unknown_fields(self, service):
        """Setting data with unknown fields ignores them."""
        result = service.set_structured_data("entry_1", "@任务", {
            "priority": "high",
            "unknown_field": "should_be_ignored",
        })
        assert result["success"] is True

        data = service.get_structured_data("entry_1")["@任务"]
        assert "unknown_field" not in data

    def test_multiple_supertags_same_entry(self, service):
        """Single entry can have multiple supertags."""
        service.set_structured_data("entry_1", "@任务", {"priority": "high"})
        service.set_structured_data("entry_1", "@联系人", {"phone": "13800138"})

        data = service.get_structured_data("entry_1")
        assert "@任务" in data
        assert "@联系人" in data
