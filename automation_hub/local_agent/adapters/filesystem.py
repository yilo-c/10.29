"""local_agent.adapters.filesystem — 文件系统操作适配器。"""

from __future__ import annotations

import logging
import os
import shutil
import stat
from pathlib import Path
from typing import Any

from local_agent.adapters.base import AdapterBase
from local_agent.stream import StreamBuffer

logger = logging.getLogger("local_agent.adapters.filesystem")


class FilesystemAdapter(AdapterBase):
    """安全的本地文件系统操作。"""

    name = "filesystem"

    def __init__(self, allowed_roots: list[str] | None = None) -> None:
        self._allowed_roots = [Path(r).resolve() for r in (allowed_roots or [])]

    def _is_path_allowed(self, p: Path) -> bool:
        if not self._allowed_roots:
            return True
        resolved = p.resolve()
        return any(resolved == root or root in resolved.parents for root in self._allowed_roots)

    async def validate(self, args: dict[str, Any]) -> tuple[bool, str]:
        action = args.get("action", "")
        if action not in ("read", "write", "list", "stat", "mkdir", "delete", "copy", "move"):
            return False, f"unknown action: {action}"
        path = args.get("path", "")
        if not path:
            return False, "path is required"
        if not self._is_path_allowed(Path(path)):
            return False, f"path not within allowed roots: {path}"
        return True, ""

    async def execute(self, args: dict[str, Any], stream: StreamBuffer | None = None) -> Any:
        ok, reason = await self.validate(args)
        if not ok:
            return {"error": reason}

        action = args["action"]
        path = Path(args["path"])

        if action == "read":
            return await self._read(path, args.get("encoding", "utf-8"), args.get("max_bytes", 1_000_000))
        elif action == "write":
            return await self._write(path, args.get("content", ""), args.get("encoding", "utf-8"))
        elif action == "list":
            return await self._list(path)
        elif action == "stat":
            return await self._stat(path)
        elif action == "mkdir":
            return await self._mkdir(path)
        elif action == "delete":
            return await self._delete(path)
        elif action == "copy":
            return await self._copy(path, Path(args.get("dest", "")))
        elif action == "move":
            return await self._move(path, Path(args.get("dest", "")))
        return {"error": "unreachable"}

    async def _read(self, path: Path, encoding: str, max_bytes: int) -> dict[str, Any]:
        try:
            data = path.read_bytes()[:max_bytes]
            return {"content": data.decode(encoding, errors="replace"), "size": path.stat().st_size}
        except Exception as e:
            return {"error": str(e)}

    async def _write(self, path: Path, content: str, encoding: str) -> dict[str, Any]:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding=encoding)
            return {"written": len(content)}
        except Exception as e:
            return {"error": str(e)}

    async def _list(self, path: Path) -> dict[str, Any]:
        try:
            entries = []
            for child in path.iterdir():
                entries.append({
                    "name": child.name,
                    "is_dir": child.is_dir(),
                    "size": child.stat().st_size if child.is_file() else 0,
                })
            return {"entries": entries}
        except Exception as e:
            return {"error": str(e)}

    async def _stat(self, path: Path) -> dict[str, Any]:
        try:
            s = path.stat()
            return {
                "size": s.st_size,
                "mode": stat.filemode(s.st_mode),
                "mtime": s.st_mtime,
                "is_dir": path.is_dir(),
            }
        except Exception as e:
            return {"error": str(e)}

    async def _mkdir(self, path: Path) -> dict[str, Any]:
        try:
            path.mkdir(parents=True, exist_ok=True)
            return {"created": str(path)}
        except Exception as e:
            return {"error": str(e)}

    async def _delete(self, path: Path) -> dict[str, Any]:
        try:
            if path.is_dir():
                shutil.rmtree(path)
            else:
                path.unlink()
            return {"deleted": str(path)}
        except Exception as e:
            return {"error": str(e)}

    async def _copy(self, src: Path, dest: Path) -> dict[str, Any]:
        if not dest.name:
            return {"error": "dest is required"}
        if not self._is_path_allowed(dest):
            return {"error": f"dest not within allowed roots: {dest}"}
        try:
            if src.is_dir():
                shutil.copytree(src, dest)
            else:
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dest)
            return {"copied": str(dest)}
        except Exception as e:
            return {"error": str(e)}

    async def _move(self, src: Path, dest: Path) -> dict[str, Any]:
        if not dest.name:
            return {"error": "dest is required"}
        if not self._is_path_allowed(dest):
            return {"error": f"dest not within allowed roots: {dest}"}
        try:
            shutil.move(str(src), str(dest))
            return {"moved": str(dest)}
        except Exception as e:
            return {"error": str(e)}
