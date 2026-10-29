from __future__ import annotations

import sys
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from api.integration_hub import ApiIntegrationHub
from data.local_storage import LocalDataStore


def test_api_integration_hub_oauth_cursor_conflict_and_audit_flow(tmp_path):
    data_store = LocalDataStore(base_dir=str(tmp_path))
    hub = ApiIntegrationHub(data_store=data_store, observability_service=None)

    token = hub.upsert_oauth_token(
        "youdao",
        {
            "access_token": "token-a",
            "refresh_token": "refresh-a",
            "scope": "notes news",
            "expires_at": "2026-06-01T00:00:00",
        },
    )
    assert token["provider"] == "youdao"
    assert hub.get_oauth_token("youdao")["access_token"] == "token-a"

    cursor = hub.upsert_sync_cursor("youdao", "notes", "cursor-1", extra={"page_size": 100})
    assert cursor["cursor"] == "cursor-1"
    loaded_cursor = hub.get_sync_cursor("youdao", "notes")
    assert loaded_cursor is not None and loaded_cursor["cursor"] == "cursor-1"

    conflict = hub.enqueue_conflict(
        "youdao",
        "notes",
        local_id="l-1",
        remote_id="r-1",
        reason="version_mismatch",
        payload={"local_version": 2, "remote_version": 3},
    )
    assert conflict["status"] == "open"

    conflicts = hub.list_conflicts("youdao", "notes", unresolved_only=True)
    assert len(conflicts) == 1
    resolved = hub.resolve_conflict("youdao", conflict_id=conflict["id"], resolution="prefer_remote")
    assert resolved is not None and resolved["status"] == "resolved"

    logs = hub.list_recent_audit_logs("youdao", limit=20)
    assert any(item["event"] == "oauth.upsert" for item in logs)
    assert any(item["event"] == "sync.cursor.upsert" for item in logs)
    assert any(item["event"] == "sync.conflict.enqueue" for item in logs)
    assert any(item["event"] == "sync.conflict.resolve" for item in logs)


def test_api_integration_hub_clear_oauth_token(tmp_path):
    data_store = LocalDataStore(base_dir=str(tmp_path))
    hub = ApiIntegrationHub(data_store=data_store, observability_service=None)

    hub.upsert_oauth_token("youdao", {"access_token": "token-a"})
    assert hub.get_oauth_token("youdao") is not None

    removed = hub.clear_oauth_token("youdao")
    assert removed is True
    assert hub.get_oauth_token("youdao") is None
