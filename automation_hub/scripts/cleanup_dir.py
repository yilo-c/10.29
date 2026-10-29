"""模块说明：目录清理脚本。"""

from __future__ import annotations

import argparse
import time
from pathlib import Path


def main():
    p = argparse.ArgumentParser(description="Delete files older than N days in a directory")
    p.add_argument("--dir", default="/data/tmp", help="Target directory")
    p.add_argument("--days", type=int, default=7, help="Delete files older than N days")
    p.add_argument("--dry-run", default="true", help="true/false")
    args = p.parse_args()

    target = Path(args.dir).expanduser()
    dry_run = str(args.dry_run).lower() in ("1", "true", "yes", "y")
    if not target.exists() or not target.is_dir():
        raise SystemExit(f"directory not found: {target}")

    cutoff = time.time() - args.days * 86400
    deleted = 0
    scanned = 0

    for path in target.rglob("*"):
        if path.is_file():
            scanned += 1
            try:
                if path.stat().st_mtime < cutoff:
                    if dry_run:
                        print(f"[DRY-RUN] delete {path}")
                    else:
                        path.unlink()
                        print(f"deleted {path}")
                    deleted += 1
            except Exception as e:
                print(f"skip {path}: {e}")

    print(f"OK: scanned={scanned}, matched={deleted}, dry_run={dry_run}")


if __name__ == "__main__":
    main()
