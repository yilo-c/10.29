"""git_apply_patch: apply unified diff via `git apply`.

This is a write tool and should be protected by approvals.

Env args:
- ARG_PATCH_TEXT (required)
- ARG_CHECK_ONLY (optional, default false)

Notes:
- Uses `git apply --check` first.
- Then applies patch from stdin.
"""

from __future__ import annotations

import os
import subprocess
import sys


def _bool_env(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "y", "on"}


def main() -> int:
    patch_text = os.getenv("ARG_PATCH_TEXT")
    if not patch_text:
        print("missing ARG_PATCH_TEXT", file=sys.stderr)
        return 2

    check_only = _bool_env("ARG_CHECK_ONLY", False)

    check = subprocess.run(
        ["git", "apply", "--check", "--whitespace=nowarn", "-"],
        input=patch_text,
        text=True,
        check=False,
    )
    if check.returncode != 0:
        return int(check.returncode)

    if check_only:
        print("patch check ok")
        return 0

    apply = subprocess.run(
        ["git", "apply", "--whitespace=nowarn", "-"],
        input=patch_text,
        text=True,
        check=False,
    )
    return int(apply.returncode)


if __name__ == "__main__":
    raise SystemExit(main())
