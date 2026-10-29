"""local_agent.adapters.logs — 日志查看与分析适配器。"""

from __future__ import annotations

import asyncio
import logging
import re
from pathlib import Path
from typing import Any

from local_agent.adapters.base import AdapterBase
from local_agent.stream import StreamBuffer

logger = logging.getLogger("local_agent.adapters.logs")


class LogsAdapter(AdapterBase):
    """读取与分析本地日志文件。"""

    name = "logs"

    def __init__(self, allowed_dirs: list[str] | None = None, max_lines: int = 2000) -> None:
        self._allowed_dirs = [Path(d).resolve() for d in (allowed_dirs or [])]
        self._max_lines = max_lines

    def _is_path_allowed(self, p: Path) -> bool:
        if not self._allowed_dirs:
            return True
        resolved = p.resolve()
        return any(resolved == d or d in resolved.parents for d in self._allowed_dirs)

    async def validate(self, args: dict[str, Any]) -> tuple[bool, str]:
        action = args.get("action", "")
        if action not in ("tail", "grep", "head", "count_errors"):
            return False, f"unknown action: {action}"
        path = args.get("path", "")
        if not path:
            return False, "path is required"
        if not self._is_path_allowed(Path(path)):
            return False, f"path not within allowed dirs: {path}"
        return True, ""

    async def execute(self, args: dict[str, Any], stream: StreamBuffer | None = None) -> dict[str, Any]:
        ok, reason = await self.validate(args)
        if not ok:
            return {"error": reason}

        action = args["action"]
        path = Path(args["path"])

        if not path.exists():
            return {"error": f"file not found: {path}"}

        if action == "tail":
            return await self._tail(path, int(args.get("lines", 50)))
        elif action == "head":
            return await self._head(path, int(args.get("lines", 50)))
        elif action == "grep":
            return await self._grep(path, args.get("pattern", ""), int(args.get("max_matches", 100)))
        elif action == "count_errors":
            return await self._count_errors(path)
        return {"error": "unreachable"}

    async def _tail(self, path: Path, n: int) -> dict[str, Any]:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        return {"lines": lines[-n:], "total": len(lines)}

    async def _head(self, path: Path, n: int) -> dict[str, Any]:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        return {"lines": lines[:n], "total": len(lines)}

    async def _grep(self, path: Path, pattern: str, max_matches: int) -> dict[str, Any]:
        try:
            regex = re.compile(pattern, re.IGNORECASE)
        except re.error as e:
            return {"error": f"invalid regex: {e}"}
        matches: list[dict[str, Any]] = []
        for i, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
            if regex.search(line):
                matches.append({"line_no": i, "text": line})
                if len(matches) >= max_matches:
                    break
        return {"matches": matches, "count": len(matches)}

    async def _count_errors(self, path: Path) -> dict[str, Any]:
        content = path.read_text(encoding="utf-8", errors="replace")
        error_pattern = re.compile(r"\b(error|exception|traceback|fatal|critical)\b", re.IGNORECASE)
        warn_pattern = re.compile(r"\bwarn(ing)?\b", re.IGNORECASE)
        errors = len(error_pattern.findall(content))
        warnings = len(warn_pattern.findall(content))
        return {"errors": errors, "warnings": warnings, "total_lines": content.count("\n") + 1}
