from __future__ import annotations

import logging
import shutil
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Event, Thread

from ..audit.service import log_event
from ..config import settings


logger = logging.getLogger(__name__)


@dataclass
class CleanupBucket:
    name: str
    scanned: int = 0
    matched: int = 0
    deleted: int = 0

    def as_dict(self) -> dict:
        return {
            "name": self.name,
            "scanned": self.scanned,
            "matched": self.matched,
            "deleted": self.deleted,
        }


def _iter_run_artifact_dirs() -> list[Path]:
    base = Path(settings.RUNS_DIR)
    targets: list[Path] = []

    if base.exists():
        for child in base.iterdir():
            if not child.is_dir():
                continue
            if child.name == "tool_runs":
                continue
            targets.append(child)

    tool_runs_dir = base / "tool_runs"
    if tool_runs_dir.exists():
        for child in tool_runs_dir.iterdir():
            if child.is_dir():
                targets.append(child)

    return targets


def _iter_proposal_artifact_dirs() -> list[Path]:
    base = Path(settings.DATA_DIR) / "proposals"
    if not base.exists():
        return []
    return [child for child in base.iterdir() if child.is_dir()]


def _cleanup_bucket(paths: list[Path], retention_days: int, bucket_name: str, *, dry_run: bool) -> CleanupBucket:
    bucket = CleanupBucket(name=bucket_name)
    if retention_days <= 0:
        return bucket

    cutoff = datetime.now(timezone.utc) - timedelta(days=retention_days)
    for path in paths:
        bucket.scanned += 1
        try:
            modified_at = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
        except FileNotFoundError:
            continue

        if modified_at >= cutoff:
            continue

        bucket.matched += 1
        if dry_run:
            continue

        shutil.rmtree(path, ignore_errors=False)
        bucket.deleted += 1

    return bucket


def cleanup_artifacts(*, dry_run: bool = False) -> dict:
    run_bucket = _cleanup_bucket(
        _iter_run_artifact_dirs(),
        int(settings.RUN_ARTIFACT_RETENTION_DAYS or 0),
        "runs",
        dry_run=dry_run,
    )
    proposal_bucket = _cleanup_bucket(
        _iter_proposal_artifact_dirs(),
        int(settings.PROPOSAL_ARTIFACT_RETENTION_DAYS or 0),
        "proposals",
        dry_run=dry_run,
    )

    summary = {
        "dry_run": dry_run,
        "runs": run_bucket.as_dict(),
        "proposals": proposal_bucket.as_dict(),
        "deleted_total": run_bucket.deleted + proposal_bucket.deleted,
        "matched_total": run_bucket.matched + proposal_bucket.matched,
    }

    if not dry_run:
        log_event(
            event_type="artifacts.cleaned",
            action="cleanup",
            status="success",
            resource_type="artifact",
            resource_id="cleanup",
            message="artifact cleanup completed",
            meta=summary,
        )

    return summary


def _artifact_cleaner_loop(stop_event: Event) -> None:
    interval = max(1, int(settings.ARTIFACT_CLEANER_INTERVAL_SECONDS or 3600))
    while not stop_event.is_set():
        try:
            summary = cleanup_artifacts(dry_run=False)
            if summary["deleted_total"]:
                logger.info("deleted %s expired artifact directories", summary["deleted_total"])
        except Exception:
            logger.exception("artifact cleaner loop failed")

        if stop_event.wait(interval):
            break


def start_artifact_cleaner(stop_event: Event) -> Thread | None:
    if int(settings.RUN_ARTIFACT_RETENTION_DAYS or 0) <= 0 and int(settings.PROPOSAL_ARTIFACT_RETENTION_DAYS or 0) <= 0:
        return None

    thread = Thread(target=_artifact_cleaner_loop, args=(stop_event,), daemon=True, name="artifact-cleaner")
    thread.start()
    return thread