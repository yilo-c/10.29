"""local_agent.adapters.powershell — PowerShell 执行适配器。"""

from __future__ import annotations

import asyncio
import logging
import sys
from typing import Any

from local_agent.adapters.base import AdapterBase
from local_agent.stream import StreamBuffer

logger = logging.getLogger("local_agent.adapters.powershell")


class PowerShellAdapter(AdapterBase):
    """通过 pwsh / powershell.exe 执行 PowerShell 脚本。"""

    name = "powershell"

    def __init__(self, timeout: float = 120, max_output: int = 1_000_000) -> None:
        self._timeout = timeout
        self._max_output = max_output
        self._exe = "pwsh" if sys.platform != "win32" else "powershell.exe"

    async def validate(self, args: dict[str, Any]) -> tuple[bool, str]:
        script = args.get("script", "")
        if not script.strip():
            return False, "script is empty"
        return True, ""

    async def execute(self, args: dict[str, Any], stream: StreamBuffer | None = None) -> dict[str, Any]:
        ok, reason = await self.validate(args)
        if not ok:
            return {"exit_code": -1, "stdout": "", "stderr": reason}

        script = args["script"]
        cwd = args.get("cwd")

        proc = await asyncio.create_subprocess_exec(
            self._exe, "-NoProfile", "-NonInteractive", "-Command", script,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd,
        )

        try:
            stdout_raw, stderr_raw = await asyncio.wait_for(proc.communicate(), timeout=self._timeout)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            return {"exit_code": -1, "stdout": "", "stderr": "timeout"}

        stdout = stdout_raw.decode("utf-8", errors="replace")[: self._max_output]
        stderr = stderr_raw.decode("utf-8", errors="replace")[: self._max_output]

        if stream:
            await stream.write(stdout, channel="stdout")
            if stderr:
                await stream.write(stderr, channel="stderr")

        return {"exit_code": proc.returncode, "stdout": stdout, "stderr": stderr}
