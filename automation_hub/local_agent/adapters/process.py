"""local_agent.adapters.process — 进程管理适配器。"""

from __future__ import annotations

import asyncio
import logging
import os
import signal
import sys
from typing import Any

from local_agent.adapters.base import AdapterBase
from local_agent.stream import StreamBuffer

logger = logging.getLogger("local_agent.adapters.process")


class ProcessAdapter(AdapterBase):
    """本地进程管理：列出、终止进程。"""

    name = "process"

    async def validate(self, args: dict[str, Any]) -> tuple[bool, str]:
        action = args.get("action", "")
        if action not in ("list", "kill", "info"):
            return False, f"unknown action: {action}"
        if action in ("kill", "info") and not args.get("pid"):
            return False, "pid is required"
        return True, ""

    async def execute(self, args: dict[str, Any], stream: StreamBuffer | None = None) -> dict[str, Any]:
        ok, reason = await self.validate(args)
        if not ok:
            return {"error": reason}

        action = args["action"]
        if action == "list":
            return await self._list_processes(args.get("filter"))
        elif action == "kill":
            return await self._kill(int(args["pid"]), args.get("signal", "SIGTERM"))
        elif action == "info":
            return await self._info(int(args["pid"]))
        return {"error": "unreachable"}

    async def _list_processes(self, name_filter: str | None = None) -> dict[str, Any]:
        """列出进程（跨平台）。"""
        if sys.platform == "win32":
            cmd = "tasklist /FO CSV /NH"
        else:
            cmd = "ps aux --no-headers"

        proc = await asyncio.create_subprocess_shell(
            cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await proc.communicate()
        lines = stdout.decode("utf-8", errors="replace").strip().splitlines()

        if name_filter:
            lower_filter = name_filter.lower()
            lines = [l for l in lines if lower_filter in l.lower()]

        return {"count": len(lines), "processes": lines[:200]}  # 限制返回量

    async def _kill(self, pid: int, sig_name: str) -> dict[str, Any]:
        try:
            if sys.platform == "win32":
                os.kill(pid, signal.SIGTERM)
            else:
                sig = getattr(signal, sig_name, signal.SIGTERM)
                os.kill(pid, sig)
            return {"killed": pid, "signal": sig_name}
        except ProcessLookupError:
            return {"error": f"process {pid} not found"}
        except PermissionError:
            return {"error": f"permission denied for pid {pid}"}

    async def _info(self, pid: int) -> dict[str, Any]:
        if sys.platform == "win32":
            cmd = f'tasklist /FI "PID eq {pid}" /FO CSV /NH'
        else:
            cmd = f"ps -p {pid} -o pid,ppid,user,%cpu,%mem,comm"
        proc = await asyncio.create_subprocess_shell(
            cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await proc.communicate()
        return {"info": stdout.decode("utf-8", errors="replace").strip()}
