from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from api.sync_orchestrator import ApiSyncOrchestrator


class FakeIntegrationHub:
    def __init__(self):
        self.cursors = {}
        self.conflicts = []
        self.audit_events = []

    def get_sync_cursor(self, provider, resource):
        return self.cursors.get((provider, resource))

    def upsert_sync_cursor(self, provider, resource, cursor, extra=None):
        record = {"cursor": cursor, "extra": dict(extra or {})}
        self.cursors[(provider, resource)] = record
        return record

    def enqueue_conflict(self, provider, resource, local_id, remote_id, reason, payload=None):
        row = {
            "provider": provider,
            "resource": resource,
            "local_id": local_id,
            "remote_id": remote_id,
            "reason": reason,
            "payload": dict(payload or {}),
        }
        self.conflicts.append(row)
        return row

    def append_audit_event(self, provider, event, status, payload=None):
        self.audit_events.append(
            {
                "provider": provider,
                "event": event,
                "status": status,
                "payload": dict(payload or {}),
            }
        )


class FakeAutomationHubClient:
    def __init__(self):
        self.upserted = []

    def pull_notes(self, cursor=None, limit=100):
        if cursor is None:
            return SimpleNamespace(notes=[{"remote_id": "r1", "title": "remote"}], next_cursor="cursor-2")
        return SimpleNamespace(notes=[], next_cursor=None)

    def upsert_note(self, payload):
        self.upserted.append(dict(payload))
        return SimpleNamespace(note={"remote_id": "r-local-1", "updated_at": "2026-03-12T00:00:00"})

    def delete_note(self, payload):
        return SimpleNamespace(note={"remote_id": payload.get("remote_id"), "deleted_at": "2026-03-12T00:00:01"})


class FakeNoteRepository:
    def __init__(self):
        self.synced = []
        self.resolved = []

    def merge_remote_notes(self, remote_notes, prefer_remote=False):
        assert prefer_remote is False
        return {
            "added": 0,
            "updated": 0,
            "skipped": 1,
            "conflicts": [
                {"local_id": "1", "remote_id": "r1", "title": "conflict-note"},
            ],
        }

    def list_pending_sync_notes(self):
        return [
            {"id": 1, "deleted_at": None, "remote_id": None},
            {"id": 2, "deleted_at": "2026-03-12T00:00:00", "remote_id": None},
        ]

    def build_note_sync_payload(self, note_id):
        if note_id == 1:
            return {"id": 1, "title": "local note", "remote_id": None, "deleted_at": None}
        return {"id": 2, "title": "removed note", "remote_id": None, "deleted_at": "2026-03-12T00:00:00"}

    def mark_note_as_synced(self, note_id, remote_note):
        self.synced.append((note_id, dict(remote_note)))

    def resolve_local_only_deleted_note(self, note_id):
        self.resolved.append(note_id)


def test_api_sync_orchestrator_sync_notes_flow():
    hub = FakeIntegrationHub()
    client = FakeAutomationHubClient()
    repo = FakeNoteRepository()

    orchestrator = ApiSyncOrchestrator(
        provider="automation_hub",
        integration_hub=hub,
        automation_hub_client=client,
        repository_map={"notes": repo},
    )

    result = orchestrator.sync_resource("notes", limit=20)

    assert result["resource"] == "notes"
    assert result["pulled"] == 1
    assert result["pushed"] == 1
    assert result["deleted"] == 0
    assert result["skipped_local_deleted"] == 1
    assert result["conflicts"] == 1

    assert hub.conflicts and hub.conflicts[0]["resource"] == "notes"
    assert hub.cursors[("automation_hub", "notes")]["cursor"] == "cursor-2"
    assert repo.synced and repo.synced[0][0] == 1
    assert repo.resolved == [2]
    assert any(item["event"] == "sync.resource.notes" for item in hub.audit_events)
