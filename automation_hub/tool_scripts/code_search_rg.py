"""code_search: ripgrep wrapper.

Reads args from env vars set by worker.jobs_v2:
- ARG_PATTERN (required)
- ARG_PATH (optional, default '.')
- ARG_FILE_TYPE (optional)
- ARG_IGNORE_CASE (optional, default true)

Outputs rg stdout directly.
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
    pattern = os.getenv("ARG_PATTERN")
    if not pattern:
        print("missing ARG_PATTERN", file=sys.stderr)
        return 2

    path = os.getenv("ARG_PATH") or "."
    file_type = os.getenv("ARG_FILE_TYPE")
    ignore_case = _bool_env("ARG_IGNORE_CASE", True)

    cmd: list[str] = ["rg", "-n", "--no-heading"]
    if ignore_case:
        cmd.append("-i")
    if file_type:
        cmd.extend(["-t", file_type])
    cmd.extend([pattern, path])

    try:
        proc = subprocess.run(cmd, text=True, check=False)
        return int(proc.returncode)
    except FileNotFoundError:
        print("rg (ripgrep) not found in PATH", file=sys.stderr)
        return 127


if __name__ == "__main__":
    raise SystemExit(main())
