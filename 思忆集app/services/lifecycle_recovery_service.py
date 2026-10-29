from __future__ import annotations

import logging
from datetime import datetime, timezone, timedelta
from typing import Any

from core.observability import SEVERITY_DEGRADED, SEVERITY_INFO

logger = logging.getLogger("siyi.lifecycle_recovery")


class LifecycleRecoveryService:
    """Lifecycle recovery service for handling crashes."""

    STALE_SECONDS = 30 * 60

    def __init__(self, data_store=None, inbox_repository=None, proposal_repository=None, observability_service=None):
        self.data_store = data_store
        self.inbox_repository = inbox_repository
        self.proposal_repository = proposal_repository
        self.observability_service = observability_service

    def recover_on_startup(self) -> dict[str, Any]:
        sync_recovered = self._recover_sync_transient_states()
        inbox_recovered = self._recover_inbox_stale_entries()
        proposal_recovered = self._recover_proposal_stale_entries()

        summary = {
            "sync_recovered": sync_recovered,
            "inbox_recovered": inbox_recovered,
            "proposal_recovered": proposal_recovered,
            "total_recovered": sync_recovered + inbox_recovered + proposal_recovered,
        }

        severity = SEVERITY_INFO if summary["total_recovered"] == 0 else SEVERITY_DEGRADED
        self._track("lifecycle.recovery.completed", severity, summary)
        return summary

    def _recover_sync_transient_states(self) -> int:
        if self.data_store is None:
            return 0

        recovered = 0
        keys = ["tasks", "notes", "news", "translations", "words", "wordbooks"]
        stale_states = {"syncing", "retrying", "conflict", "failed"}

        for key in keys:
            items = self.data_store.load_json(key, [])
            if not isinstance(items, list):
                continue
            changed = False
            for item in items:
                if not isinstance(item, dict):
                    continue
                if str(item.get("sync_state") or "").strip().lower() not in stale_states:
                    continue
                if not self._is_stale(item.get("updated_at") or item.get("last_synced_at")):
                    continue
                item["sync_state"] = "dirty"
                item["updated_at"] = datetime.now(timezone.utc).isoformat()
                item["sync_recovery_count"] = int(item.get("sync_recovery_count") or 0) + 1
                recovered += 1
                changed = True
            if changed:
                self.data_store.save_json(key, items)
        return recovered

    def _recover_inbox_stale_entries(self) -> int:
        if self.inbox_repository is None:
            return 0
        recovered = 0
        for entry in self.inbox_repository.list_entries(include_archived=True):
            if str(entry.get("status") or "").strip().lower() != "processing":
                continue
            if not self._is_stale(entry.get("updated_at") or entry.get("created_at")):
                continue
            metadata = dict(entry.get("metadata") or {})
            metadata["auto_recovered_at"] = datetime.now(timezone.utc).isoformat()
            self.inbox_repository.update_entry(
                int(entry.get("id") or 0),
                {
                    "status": "new",
                    "metadata": metadata,
                },
            )
            recovered += 1
        return recovered

    def _recover_proposal_stale_entries(self) -> int:
        if self.proposal_repository is None:
            return 0
        recovered = 0
        stale_status = {"applying", "running", "failed"}
        for proposal in self.proposal_repository.list_proposals():
            if str(proposal.get("status") or "").strip().lower() not in stale_status:
                continue
            if not self._is_stale(proposal.get("updated_at") or proposal.get("created_at")):
                continue
            self.proposal_repository.update_proposal(
                int(proposal.get("id") or 0),
                {
                    "status": "pending_review",
                    "recovered_at": datetime.now(timezone.utc).isoformat(),
                },
            )
            recovered += 1
        return recovered

    def _is_stale(self, timestamp: Any) -> bool:
        if not timestamp:
            return False
        parsed = self._parse_ts(timestamp)
        if parsed is None:
            return False
        return parsed <= datetime.now(timezone.utc) - timedelta(seconds=self.STALE_SECONDS)

    def _parse_ts(self, value: Any) -> datetime | None:
        text = str(value or "").strip()
        if not text:
            return None
        for candidate in (text, text.replace("Z", "+00:00")):
            try:
                dt = datetime.fromisoformat(candidate)
                if dt.tzinfo is not None:
                    return dt
                return dt.replace(tzinfo=None)
            except ValueError:
                continue
        return None

    def _track(self, event_name: str, severity: str, payload: dict[str, Any]) -> None:
        service = self.observability_service
        if service is None:
            return
        try:
            service.track_event(event_name, severity=severity, payload=payload)
        except Exception:
            logger.warning("failed to track lifecycle event: %s", event_name)
