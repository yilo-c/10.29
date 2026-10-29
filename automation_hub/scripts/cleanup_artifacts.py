#!/usr/bin/env python
from __future__ import annotations

import argparse
import json

from automation_hub.api.artifacts.service import cleanup_artifacts
from automation_hub.api.config import settings


def main() -> int:
    parser = argparse.ArgumentParser(description="Cleanup expired run/proposal artifacts")
    parser.add_argument("--dry-run", action="store_true", help="Only report matched directories without deleting them")
    parser.add_argument("--run-retention-days", type=int, default=None, help="Override RUN_ARTIFACT_RETENTION_DAYS")
    parser.add_argument("--proposal-retention-days", type=int, default=None, help="Override PROPOSAL_ARTIFACT_RETENTION_DAYS")
    args = parser.parse_args()

    if args.run_retention_days is not None:
        settings.RUN_ARTIFACT_RETENTION_DAYS = args.run_retention_days
    if args.proposal_retention_days is not None:
        settings.PROPOSAL_ARTIFACT_RETENTION_DAYS = args.proposal_retention_days

    summary = cleanup_artifacts(dry_run=args.dry_run)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())