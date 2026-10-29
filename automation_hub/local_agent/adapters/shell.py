"""local_agent.adapters.shell — 跨平台 shell 命令执行适配器。"""

from __future__ import annotations

import asyncio
import logging
import shlex
import sys
from typing import Any

from local_agent.adapters.base import AdapterBase
from local_agent.stream import StreamBuffer

logger = logging.getLogger("local_agent.adapters.shell")

# 禁止执行的危险命令前缀
_BLOCKED_PREFIXES = frozenset({
    "rm -rf /", "rm -rf /*",
    "mkfs", "dd if=",
    ":(){ :|:& };:",
    "shutdown", "reboot", "init 0", "init 6",
    "format c:", "del /f /s /q c:",
})


class ShellAdapter(AdapterBase):
    """在本地 shell 中执行命令。"""

    name = "shell"

    def __init__(self, timeout: float = 120, max_output: int = 1_000_000) -> None:
        self._timeout = timeout
        self._max_output = max_output

    async def validate(self, args: dict[str, Any]) -> tuple[bool, str]:
        cmd = args.get("command", "")
        if not cmd.strip():
            return False, "command is empty"
        lower = cmd.strip().lower()
        for prefix in _BLOCKED_PREFIXES:
            if lower.startswith(prefix):
                return False, f"blocked dangerous command: {prefix}"
        return True, ""

    async def execute(self, args: dict[str, Any], stream: StreamBuffer | None = None) -> dict[str, Any]:
        ok, reason = await self.validate(args)
        if not ok:
            return {"exit_code": -1, "stdout": "", "stderr": reason}

        cmd = args["command"]
        cwd = args.get("cwd")
        env = args.get("env")

        if sys.platform == "win32":
            proc = await asyncio.create_subprocess_shell(
                cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=cwd,
                env=env,
            )
        else:
            proc = await asyncio.create_subprocess_exec(
                "/bin/sh", "-c", cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=cwd,
                env=env,
            )

        stdout_parts: list[str] = []
        stderr_parts: list[str] = []
        total = 0

        async def _read_stream(s: asyncio.StreamReader | None, channel: str, parts: list[str]) -> None:
            nonlocal total
            if s is None:
                return
            while True:
                line = await s.readline()
                if not line:
                    break
                text = line.decode("utf-8", errors="replace")
                total += len(text)
                if total <= self._max_output:
                    parts.append(text)
                if stream:
                    await stream.write(text, channel=channel)

        try:
            await asyncio.wait_for(
                asyncio.gather(
                    _read_stream(proc.stdout, "stdout", stdout_parts),
                    _read_stream(proc.stderr, "stderr", stderr_parts),
                ),
                timeout=self._timeout,
            )
            await proc.wait()
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            return {"exit_code": -1, "stdout": "".join(stdout_parts), "stderr": "timeout"}

        return {
            "exit_code": proc.returncode,
            "stdout": "".join(stdout_parts),
            "stderr": "".join(stderr_parts),
        }
