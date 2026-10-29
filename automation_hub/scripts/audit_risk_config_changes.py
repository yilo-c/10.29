#!/usr/bin/env python
from __future__ import annotations

import argparse
import fnmatch
import subprocess
import sys
from pathlib import Path


SENSITIVE_PATTERNS = (
    "automation-hub/.github/workflows/ci.yaml",
    "automation-hub/scripts/release_check.py",
    "scripts/desktop_quality_gate.py",
    "思忆集app/action_guard.py",
    "思忆集app/settings_schema.py",
)

REVIEW_NOTE_PATTERNS = (
    "docs/risk-config-change*.md",
    "docs/security/risk-config-change*.md",
)


def _git_diff_files(repo_root: Path, base_ref: str, head_ref: str) -> list[str]:
    command = ["git", "diff", "--name-only", f"{base_ref}...{head_ref}"]
    output = subprocess.check_output(command, cwd=str(repo_root), text=True)
    return [line.strip().replace("\\", "/") for line in output.splitlines() if line.strip()]


def _match_any(path: str, patterns: tuple[str, ...]) -> bool:
    return any(fnmatch.fnmatch(path, pattern) for pattern in patterns)


def main() -> int:
    parser = argparse.ArgumentParser(description="审计高风险配置变更是否附带复核说明")
    parser.add_argument("--base-ref", required=True)
    parser.add_argument("--head-ref", required=True)
    args = parser.parse_args()

    repo_root = Path(__file__).resolve().parents[2]

    try:
        changed_files = _git_diff_files(repo_root, args.base_ref, args.head_ref)
    except subprocess.CalledProcessError as exc:
        print(f"[risk-config-audit] failed to get git diff: {exc}")
        return 2

    sensitive_changes = [path for path in changed_files if _match_any(path, SENSITIVE_PATTERNS)]
    review_notes = [path for path in changed_files if _match_any(path, REVIEW_NOTE_PATTERNS)]

    if not sensitive_changes:
        print("[risk-config-audit] no sensitive config changes detected")
        return 0

    print("[risk-config-audit] sensitive files changed:")
    for path in sensitive_changes:
        print(f"- {path}")

    if review_notes:
        print("[risk-config-audit] review note found:")
        for path in review_notes:
            print(f"- {path}")
        return 0

    print("[risk-config-audit] missing review note for sensitive changes")
    print("[risk-config-audit] add docs/risk-config-change-<date>.md to describe reason, impact, rollback")
    return 1


if __name__ == "__main__":
    sys.exit(main())
