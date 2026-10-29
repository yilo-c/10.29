"""Sync directory manager — manages the ~/SiyiSync/ directory tree.

Owns the directory structure, file hash comparison, and change detection
for the file-direct-sync subsystem. All file I/O below the sync root goes
through this module.

Sync root layout (user-configurable via settings):
    ~/SiyiSync/
    ├── photos/       # Camera roll, screenshots — organized by YYYY/MM
    ├── diaries/      # Daily markdown diaries
    ├── notes/        # Synced notes as .md / .json
    ├── tasks/        # Task data as .json
    ├── memories/     # Memory index exports
    └── settings/     # Settings backups
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Iterator

logger = logging.getLogger("siyi.sync.directory_manager")

# Default subdirectories under sync root
SYNC_SUBDIRS = frozenset({"photos", "diaries", "notes", "tasks", "memories", "settings"})

# File extensions handled by each subdirectory
SYNC_EXTENSIONS: dict[str, frozenset[str]] = {
    "photos": frozenset({".jpg", ".jpeg", ".png", ".gif", ".webp", ".heic", ".mov", ".mp4"}),
    "diaries": frozenset({".md", ".txt"}),
    "notes": frozenset({".md", ".json"}),
    "tasks": frozenset({".json"}),
    "memories": frozenset({".json"}),
    "settings": frozenset({".json", ".yaml", ".yml"}),
}


@dataclass(frozen=True)
class FileEntry:
    """A file under the sync tree with its content hash."""
    relative_path: str          # path relative to sync root, e.g. "photos/2026/05/img.jpg"
    subdir: str                 # photos | diaries | notes | tasks | memories | settings
    size_bytes: int = 0
    modified_at: float = 0.0    # unix timestamp of last modification
    content_hash: str = ""      # sha256 hex digest
    is_directory: bool = False


@dataclass(frozen=True)
class SyncDirConfig:
    """Persistent configuration for the sync directory manager."""
    root_path: str = ""
    auto_create: bool = True
    photos_organized_by_month: bool = True


class SyncDirectoryManager:
    """Owns the sync directory tree on disk.

    Responsibilities:
    - Create and validate the sync directory structure
    - List files by subdirectory with content hashes
    - Detect new / changed / deleted files since a known state
    - Organise incoming files into the correct subdirectory
    """

    def __init__(self, config: SyncDirConfig | None = None):
        self._config = config or SyncDirConfig()
        self._root: Path | None = None
        if self._config.root_path:
            self.set_root(self._config.root_path)

    # ── Root management ────────────────────────────────────────────────

    def set_root(self, path: str | Path) -> None:
        """Set the sync root directory. Creates it if auto_create is on."""
        p = Path(path).resolve()
        if self._config.auto_create:
            p.mkdir(parents=True, exist_ok=True)
        self._root = p

    @property
    def root(self) -> Path | None:
        return self._root

    @property
    def is_ready(self) -> bool:
        return self._root is not None and self._root.is_dir()

    def ensure_structure(self) -> list[str]:
        """Create the standard subdirectory tree. Returns paths created."""
        if not self._root:
            raise RuntimeError("SyncDirectoryManager: root not set")
        created: list[str] = []
        for sub in sorted(SYNC_SUBDIRS):
            p = self._root / sub
            if not p.exists():
                p.mkdir(parents=True, exist_ok=True)
                created.append(str(p.relative_to(self._root)))
        return created

    def validate_structure(self) -> list[str]:
        """Return a list of issues (empty = all good)."""
        issues: list[str] = []
        if not self._root:
            issues.append("root not set")
            return issues
        if not self._root.is_dir():
            issues.append(f"root '{self._root}' is not a directory")
            return issues
        for sub in SYNC_SUBDIRS:
            p = self._root / sub
            if not p.is_dir():
                issues.append(f"missing subdirectory '{sub}'")
        return issues

    # ── File listing & hashing ─────────────────────────────────────────

    def list_files(self, subdir: str | None = None) -> list[FileEntry]:
        """Recursively list all files under *subdir* (or entire tree)."""
        if not self.is_ready:
            return []
        root = self._root
        if root is None:
            return []
        base = root
        if subdir:
            base = base / subdir
            if not base.is_dir():
                return []

        results: list[FileEntry] = []
        for fp in self._iter_files(base):
            rel = fp.relative_to(root)
            sub = rel.parts[0] if len(rel.parts) > 0 else ""
            if sub not in SYNC_SUBDIRS:
                continue
            stat = fp.stat()
            results.append(FileEntry(
                relative_path=str(rel.as_posix()),
                subdir=sub,
                size_bytes=stat.st_size,
                modified_at=stat.st_mtime,
                content_hash=self._hash_file(fp),
            ))
        return results

    def find_changes(
        self,
        known_state: dict[str, FileEntry],
        subdir: str | None = None,
    ) -> dict[str, list[FileEntry]]:
        """Compare current files against *known_state*.

        Returns {"new": [...], "changed": [...], "removed": [...], "unchanged": [...]}.
        """
        current = {e.relative_path: e for e in self.list_files(subdir)}
        current_keys = set(current)
        known_keys = set(known_state)

        new_keys = current_keys - known_keys
        removed_keys = known_keys - current_keys
        common_keys = current_keys & known_keys

        new_files = [current[k] for k in sorted(new_keys)]
        removed_files = [known_state[k] for k in sorted(removed_keys)]

        changed: list[FileEntry] = []
        unchanged: list[FileEntry] = []
        for k in sorted(common_keys):
            cur = current[k]
            prev = known_state[k]
            if cur.content_hash != prev.content_hash or cur.size_bytes != prev.size_bytes:
                changed.append(cur)
            else:
                unchanged.append(cur)

        return {"new": new_files, "changed": changed, "removed": removed_files, "unchanged": unchanged}

    def resolve_path(self, subdir: str, *parts: str) -> Path:
        """Build an absolute path under *subdir*, validating the subdir name."""
        if subdir not in SYNC_SUBDIRS:
            raise ValueError(f"invalid sync subdir: {subdir!r}, must be one of {sorted(SYNC_SUBDIRS)}")
        root = self._root
        if not root:
            raise RuntimeError("SyncDirectoryManager: root not set")
        # 路径穿越防护：拒绝绝对路径片段与 ".."，并校验规范化后仍在 root 下，
        # 避免远端不可信 relative_path 逃逸出 sync root 读写任意文件。
        if any(p == ".." or Path(p).is_absolute() for p in parts):
            raise ValueError(f"invalid path parts: {parts!r}")
        resolved = root / subdir / "/".join(parts)
        if not resolved.resolve().is_relative_to(root.resolve()):
            raise ValueError(f"path traversal detected: {parts!r} escapes sync root")
        return resolved

    def organise_file(self, source: Path, subdir: str) -> Path:
        """Move *source* into the sync tree under *subdir*, returning the destination.

        For ``photos``, files are organised as ``photos/YYYY/MM/filename``.
        For all others the file lands in the subdirectory root.
        """
        if subdir not in SYNC_SUBDIRS:
            raise ValueError(f"invalid sync subdir: {subdir!r}")
        if not source.is_file():
            raise FileNotFoundError(str(source))

        root = self._root
        if root is None:
            raise RuntimeError("SyncDirectoryManager: root not set")
        dest_dir = root / subdir
        if subdir == "photos" and self._config.photos_organized_by_month:
            today = date.today()
            dest_dir = dest_dir / str(today.year) / f"{today.month:02d}"

        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / source.name
        # Avoid collision by appending a counter
        counter = 1
        while dest.exists():
            stem = source.stem
            suffix = source.suffix
            dest = dest_dir / f"{stem}_{counter}{suffix}"
            counter += 1

        shutil.move(str(source), str(dest))
        logger.info("Organised %s → %s", source, dest)
        return dest

    def get_subdir_size(self, subdir: str) -> int:
        """Total bytes used by a subdirectory (recursive)."""
        return sum(e.size_bytes for e in self.list_files(subdir))

    # ── Internals ──────────────────────────────────────────────────────

    def _iter_files(self, directory: Path) -> Iterator[Path]:
        if not directory.is_dir():
            return
        for entry in os.scandir(str(directory)):
            if entry.is_file(follow_symlinks=False):
                # Skip hidden files
                if entry.name.startswith("."):
                    continue
                yield Path(entry.path)
            elif entry.is_dir(follow_symlinks=False):
                # Only recurse into month dirs under photos
                sub = entry.name
                if sub in SYNC_SUBDIRS or (sub.isdigit() and len(sub) in (2, 4)):
                    yield from self._iter_files(Path(entry.path))

    @staticmethod
    def _hash_file(path: Path) -> str:
        """SHA-256 hash of file contents. Empty file returns the hash of empty bytes."""
        h = hashlib.sha256()
        try:
            with open(path, "rb") as f:
                while True:
                    chunk = f.read(65536)
                    if not chunk:
                        break
                    h.update(chunk)
        except OSError:
            return ""
        return h.hexdigest()

    @staticmethod
    def hash_bytes(data: bytes) -> str:
        return hashlib.sha256(data).hexdigest()

    @staticmethod
    def hash_text(text: str) -> str:
        return hashlib.sha256(text.encode("utf-8")).hexdigest()
