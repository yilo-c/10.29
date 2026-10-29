"""仓库索引器（最小版）。"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Iterable


def iter_files(root: Path) -> Iterable[Path]:
    for p in root.rglob("*"):
        if p.is_file():
            yield p


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def build_index(root: Path, existing: dict[str, dict]) -> list[dict]:
    """Build file index entries, reusing existing sha256 when unchanged."""
    entries: list[dict] = []
    for p in iter_files(root):
        stat = p.stat()
        rel = str(p.relative_to(root)).replace("\\", "/")
        cached = existing.get(rel)
        if cached and int(cached.get("mtime", 0)) == int(stat.st_mtime) and int(cached.get("size", 0)) == int(stat.st_size):
            sha = cached.get("sha256")
        else:
            sha = sha256_file(p)
        entries.append(
            {
                "path": rel,
                "mtime": int(stat.st_mtime),
                "size": int(stat.st_size),
                "sha256": sha,
            }
        )
    return entries
