# -*- coding: utf-8 -*-
"""Tests for data classification enforcement (Layer 3)."""

from __future__ import annotations

import pytest
from automation_hub.api.security.data_classification import (
    DataLevel,
    classify_resource,
    enforce_export_rule,
    is_export_allowed,
    check_resource_export,
    level_rank,
)


class TestDataLevelRank:
    """Verify level ordering: LOCAL_ONLY (0) < SYNCABLE (1) < SHAREABLE (2) < MODEL_SENDABLE (3)."""

    def test_level_rank_ordering(self):
        assert level_rank(DataLevel.LOCAL_ONLY) == 0
        assert level_rank(DataLevel.SYNCABLE) == 1
        assert level_rank(DataLevel.SHAREABLE) == 2
        assert level_rank(DataLevel.MODEL_SENDABLE) == 3


class TestClassifyResource:
    """Test data classification based on resource type and metadata."""

    def test_classify_defaults_to_syncable(self):
        """Unknown resource types default to syncable."""
        assert classify_resource("unknown_type") == DataLevel.SYNCABLE

    def test_classify_api_key_is_local_only(self):
        assert classify_resource("api_key") == DataLevel.LOCAL_ONLY

    def test_classify_note_is_syncable(self):
        assert classify_resource("note") == DataLevel.SYNCABLE

    def test_classify_public_note_is_shareable(self):
        assert classify_resource("public_note") == DataLevel.SHAREABLE

    def test_classify_preference_is_model_sendable(self):
        assert classify_resource("preference") == DataLevel.MODEL_SENDABLE

    def test_tag_override_private(self):
        """Tags can override to a more restrictive level."""
        level = classify_resource("note", {"tags": ["private"]})
        assert level == DataLevel.LOCAL_ONLY

    def test_tag_override_public(self):
        level = classify_resource("note", {"tags": ["public"]})
        assert level == DataLevel.SHAREABLE

    def test_tag_override_llm_context(self):
        level = classify_resource("note", {"tags": ["llm_context"]})
        assert level == DataLevel.MODEL_SENDABLE

    def test_most_restrictive_tag_wins(self):
        """When multiple tags, the most restrictive (lowest rank) takes precedence."""
        level = classify_resource("note", {"tags": ["public", "private"]})
        assert level == DataLevel.LOCAL_ONLY

    def test_explicit_classification_overrides_default(self):
        level = classify_resource("note", {"classification": "local_only"})
        assert level == DataLevel.LOCAL_ONLY

    def test_explicit_classification_overrides_tags(self):
        """Explicit classification in metadata takes precedence over tags."""
        level = classify_resource("note", {
            "classification": "shareable",
            "tags": ["private"],
        })
        assert level == DataLevel.SHAREABLE

    def test_invalid_classification_falls_back(self):
        """Invalid explicit classification falls back to default."""
        level = classify_resource("note", {"classification": "invalid_level"})
        assert level == DataLevel.SYNCABLE


class TestEnforceExportRule:
    """Test export enforcement based on classification level."""

    def test_local_only_to_local_allowed(self):
        """Local-only data can be exported to local target."""
        enforce_export_rule(DataLevel.LOCAL_ONLY, "local")

    def test_local_only_to_sync_blocked(self):
        """Local-only data cannot be synced."""
        with pytest.raises(PermissionError):
            enforce_export_rule(DataLevel.LOCAL_ONLY, "sync")

    def test_local_only_to_share_blocked(self):
        with pytest.raises(PermissionError):
            enforce_export_rule(DataLevel.LOCAL_ONLY, "share")

    def test_local_only_to_model_blocked(self):
        with pytest.raises(PermissionError):
            enforce_export_rule(DataLevel.LOCAL_ONLY, "model")

    def test_syncable_to_sync_allowed(self):
        enforce_export_rule(DataLevel.SYNCABLE, "sync")

    def test_syncable_to_share_blocked(self):
        with pytest.raises(PermissionError):
            enforce_export_rule(DataLevel.SYNCABLE, "share")

    def test_shareable_to_share_allowed(self):
        enforce_export_rule(DataLevel.SHAREABLE, "share")

    def test_shareable_to_model_blocked(self):
        with pytest.raises(PermissionError):
            enforce_export_rule(DataLevel.SHAREABLE, "model")

    def test_model_sendable_to_all_targets_allowed(self):
        """MODEL_SENDABLE is the least restrictive — allowed everywhere."""
        for target in ("local", "sync", "share", "model"):
            enforce_export_rule(DataLevel.MODEL_SENDABLE, target)

    def test_unknown_target_raises_value_error(self):
        with pytest.raises(ValueError):
            enforce_export_rule(DataLevel.SYNCABLE, "unknown")


class TestIsExportAllowed:
    """Test non-throwing export check."""

    def test_local_only_to_local(self):
        assert is_export_allowed(DataLevel.LOCAL_ONLY, "local") is True

    def test_local_only_to_sync(self):
        assert is_export_allowed(DataLevel.LOCAL_ONLY, "sync") is False

    def test_shareable_to_share(self):
        assert is_export_allowed(DataLevel.SHAREABLE, "share") is True

    def test_unknown_target_returns_false(self):
        assert is_export_allowed(DataLevel.SYNCABLE, "unknown") is False


class TestCheckResourceExport:
    """Test the combined resource export check function."""

    def test_allowed_resource(self):
        result = check_resource_export("note", "sync")
        assert result.allowed is True
        assert result.level == DataLevel.SYNCABLE

    def test_blocked_resource(self):
        result = check_resource_export("api_key", "sync")
        assert result.allowed is False
        assert result.level == DataLevel.LOCAL_ONLY
        assert "blocked" in result.reason

    def test_model_sendable_resource(self):
        result = check_resource_export("preference", "model")
        assert result.allowed is True
        assert result.level == DataLevel.MODEL_SENDABLE

    def test_private_tagged_note_blocked_from_share(self):
        result = check_resource_export("note", "share", {"tags": ["private"]})
        assert result.allowed is False
        assert result.level == DataLevel.LOCAL_ONLY
