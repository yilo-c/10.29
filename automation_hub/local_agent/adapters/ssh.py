"""local_agent.adapters.ssh — SSH 远程执行适配器。"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from local_agent.adapters.base import AdapterBase
from local_agent.stream import StreamBuffer

logger = logging.getLogger("local_agent.adapters.ssh")


class SSHAdapter(AdapterBase):
    """通过 ssh CLI 在远程主机执行命令。"""

    name = "ssh"

    def __init__(self, timeout: float = 120) -> None:
        self._timeout = timeout

    async def validate(self, args: dict[str, Any]) -> tuple[bool, str]:
        if not args.get("host"):
            return False, "host is required"
        if not args.get("command"):
            return False, "command is required"
        return True, ""

    async def execute(self, args: dict[str, Any], stream: StreamBuffer | None = None) -> dict[str, Any]:
        ok, reason = await self.validate(args)
        if not ok:
            return {"error": reason}

        host = args["host"]
        user = args.get("user", "")
        port = args.get("port", 22)
        command = args["command"]
        key_file = args.get("key_file")

        target = f"{user}@{host}" if user else host
        ssh_args = [
            "ssh",
            "-o", "StrictHostKeyChecking=accept-new",
            "-o", "ConnectTimeout=10",
            "-p", str(port),
        ]
        if key_file:
            ssh_args += ["-i", key_file]
        ssh_args += [target, command]

        proc = await asyncio.create_subprocess_exec(
            *ssh_args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=self._timeout)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            return {"exit_code": -1, "stdout": "", "stderr": "timeout"}

        out = stdout.decode("utf-8", errors="replace")
        err = stderr.decode("utf-8", errors="replace")

        if stream:
            await stream.write(out, channel="stdout")
            if err:
                await stream.write(err, channel="stderr")

        return {"exit_code": proc.returncode, "stdout": out, "stderr": err}
