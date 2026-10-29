"""模块说明：笔记备份脚本。"""

from __future__ import annotations

import argparse
import shutil
from datetime import datetime
from pathlib import Path


def main():
    p = argparse.ArgumentParser(description="Backup a directory to a timestamped zip under /data/backups")
    p.add_argument("--source", default="/data/notes", help="Directory to backup")
    p.add_argument("--out-dir", default="/data/backups", help="Output directory for zip files")
    args = p.parse_args()

    source = Path(args.source).expanduser().resolve()
    out_dir = Path(args.out_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    if not source.exists() or not source.is_dir():
        raise SystemExit(f"source directory not found: {source}")

    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    zip_base = out_dir / f"backup-{source.name}-{ts}"
    archive_path = shutil.make_archive(str(zip_base), "zip", root_dir=str(source))

    print(f"OK: created {archive_path}")


if __name__ == "__main__":
    main()
