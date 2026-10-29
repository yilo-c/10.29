from __future__ import annotations

import importlib.util
import json
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = APP_ROOT.parent
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))
from services.lifecycle_recovery_service import LifecycleRecoveryService
from data.local_storage import LocalDataStore
from repositories import LocalImprovementProposalRepository, LocalPersonalInboxRepository


def _load_module_from_path(module_name: str, file_path: Path):
    spec = importlib.util.spec_from_file_location(module_name, str(file_path))
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_lifecycle_recovery_restores_stale_transient_states(tmp_path):
    data_store = LocalDataStore(base_dir=str(tmp_path))
    inbox_repo = LocalPersonalInboxRepository(data_store)
    proposal_repo = LocalImprovementProposalRepository(data_store)

    stale_time = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
    data_store.save_json(
        "tasks",
        [
            {
                "id": 1,
                "name": "stale task",
                "sync_state": "failed",
                "updated_at": stale_time,
            }
        ],
    )

    inbox_id = inbox_repo.add_entry({"title": "x", "content": "y", "status": "processing", "updated_at": stale_time})
    proposal_id = proposal_repo.add_proposal({"title": "p", "content": "c", "status": "applying", "updated_at": stale_time})

    service = LifecycleRecoveryService(
        data_store=data_store,
        inbox_repository=inbox_repo,
        proposal_repository=proposal_repo,
        observability_service=None,
    )

    summary = service.recover_on_startup()

    assert summary["total_recovered"] >= 3
    recovered_task = data_store.load_json("tasks", [])[0]
    assert recovered_task["sync_state"] == "dirty"
    assert inbox_repo.get_entry(inbox_id)["status"] == "new"
    assert proposal_repo.get_proposal(proposal_id)["status"] == "pending_review"


def test_desktop_data_governance_supports_dry_run_and_apply(tmp_path):
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)

    stale = (datetime.now(timezone.utc) - timedelta(days=90)).isoformat()
    inbox_path = data_dir / "inbox_entries.json"
    proposal_path = data_dir / "improvement_proposals.json"
    inbox_path.write_text(
        json.dumps(
            [
                {"id": 1, "title": "A", "content": "B", "source": "manual", "status": "archived", "updated_at": stale},
                {"id": 2, "title": "A", "content": "B", "source": "manual", "status": "new", "updated_at": stale},
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    proposal_path.write_text(
        json.dumps(
            [
                {"id": 1, "title": "P", "content": "C", "status": "accepted", "updated_at": stale},
                {"id": 2, "title": "Q", "content": "D", "status": "pending_review", "updated_at": stale},
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    module = _load_module_from_path("desktop_data_governance_test", PROJECT_ROOT / "scripts" / "desktop_data_governance.py")

    dry_result = module.run_governance(data_dir=data_dir, dry_run=True, archive_days=30, resolved_days=30)
    assert dry_result["inbox_archived_removed"] == 1
    assert dry_result["proposal_resolved_removed"] == 1

    apply_result = module.run_governance(data_dir=data_dir, dry_run=False, archive_days=30, resolved_days=30)
    assert apply_result["inbox_archived_removed"] == 1
    assert apply_result["proposal_resolved_removed"] == 1

    applied_inbox = json.loads(inbox_path.read_text(encoding="utf-8"))
    applied_proposals = json.loads(proposal_path.read_text(encoding="utf-8"))
    assert len(applied_inbox) == 1
    assert len(applied_proposals) == 1
