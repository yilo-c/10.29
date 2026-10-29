"""run_tests: minimal pytest runner.

Env args:
- ARG_PATH (optional, default 'tests')
- ARG_MARKERS (optional)

Runs: python -m pytest -q <path> [ -m <markers> ]
"""

from __future__ import annotations

import os
import subprocess
import sys


def main() -> int:
    path = os.getenv("ARG_PATH") or "tests"
    markers = os.getenv("ARG_MARKERS")

    cmd: list[str] = [sys.executable, "-m", "pytest", "-q", path]
    if markers:
        cmd.extend(["-m", markers])

    proc = subprocess.run(cmd, text=True, check=False)
    return int(proc.returncode)


if __name__ == "__main__":
    raise SystemExit(main())
