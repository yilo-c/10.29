from __future__ import annotations

import os
import time

from automation_hub.api.artifacts.service import cleanup_artifacts
from automation_hub.api.audit.service import query_events
from automation_hub.api.config import settings
from automation_hub.tests.support import configure_test_env


def _touch_old(path, *, days_ago: int) -> None:
    path.mkdir(parents=True, exist_ok=True)
    marker = path / "marker.txt"
    marker.write_text("old", encoding="utf-8")
    old_time = time.time() - days_ago * 86400
    os.utime(path, (old_time, old_time))
    os.utime(marker, (old_time, old_time))


def test_cleanup_artifacts_dry_run_keeps_old_directories(tmp_path, monkeypatch):
    env = configure_test_env(tmp_path, monkeypatch)
    monkeypatch.setattr(settings, "RUN_ARTIFACT_RETENTION_DAYS", 7)
    monkeypatch.setattr(settings, "PROPOSAL_ARTIFACT_RETENTION_DAYS", 7)

    old_run = env["runs_dir"] / "tool_runs" / "run-old"
    old_proposal = env["data_dir"] / "proposals" / "proposal-old"
    _touch_old(old_run, days_ago=30)
    _touch_old(old_proposal, days_ago=30)

    summary = cleanup_artifacts(dry_run=True)
    assert summary["dry_run"] is True
    assert summary["runs"]["matched"] == 1
    assert summary["proposals"]["matched"] == 1
    assert old_run.exists()
    assert old_proposal.exists()


def test_cleanup_artifacts_deletes_old_run_and_proposal_directories(tmp_path, monkeypatch):
    env = configure_test_env(tmp_path, monkeypatch)
    monkeypatch.setattr(settings, "RUN_ARTIFACT_RETENTION_DAYS", 7)
    monkeypatch.setattr(settings, "PROPOSAL_ARTIFACT_RETENTION_DAYS", 7)

    old_run = env["runs_dir"] / "tool_runs" / "run-old"
    fresh_run = env["runs_dir"] / "tool_runs" / "run-fresh"
    old_proposal = env["data_dir"] / "proposals" / "proposal-old"
    fresh_proposal = env["data_dir"] / "proposals" / "proposal-fresh"

    _touch_old(old_run, days_ago=30)
    _touch_old(old_proposal, days_ago=30)
    fresh_run.mkdir(parents=True, exist_ok=True)
    (fresh_run / "stdout.txt").write_text("fresh", encoding="utf-8")
    fresh_proposal.mkdir(parents=True, exist_ok=True)
    (fresh_proposal / "apply_meta.json").write_text("{}", encoding="utf-8")

    summary = cleanup_artifacts(dry_run=False)
    assert summary["deleted_total"] == 2
    assert summary["runs"]["deleted"] == 1
    assert summary["proposals"]["deleted"] == 1

    assert not old_run.exists()
    assert not old_proposal.exists()
    assert fresh_run.exists()
    assert fresh_proposal.exists()

    events = query_events(limit=20, event_type="artifacts.cleaned")
    assert any(event["resource_id"] == "cleanup" for event in events)