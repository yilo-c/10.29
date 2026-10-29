"""git_diff: wrapper for `git diff`.

Env args:
- ARG_FILE (optional)
- ARG_CACHED (optional, default false)
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
    cached = _bool_env("ARG_CACHED", False)
    file_path = os.getenv("ARG_FILE")

    cmd: list[str] = ["git", "diff"]
    if cached:
        cmd.append("--cached")
    if file_path:
        cmd.extend(["--", file_path])

    proc = subprocess.run(cmd, text=True, check=False)
    return int(proc.returncode)


if __name__ == "__main__":
    raise SystemExit(main())
