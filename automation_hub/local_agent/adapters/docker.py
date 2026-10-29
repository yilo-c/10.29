"""local_agent.adapters.docker — Docker 操作适配器。"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from local_agent.adapters.base import AdapterBase
from local_agent.stream import StreamBuffer

logger = logging.getLogger("local_agent.adapters.docker")


class DockerAdapter(AdapterBase):
    """通过 docker CLI 操作容器和镜像。"""

    name = "docker"

    def __init__(self, timeout: float = 180) -> None:
        self._timeout = timeout

    async def validate(self, args: dict[str, Any]) -> tuple[bool, str]:
        action = args.get("action", "")
        allowed = ("ps", "logs", "inspect", "exec", "stats", "images", "pull", "stop", "restart")
        if action not in allowed:
            return False, f"unknown action: {action}, allowed: {allowed}"
        if action in ("logs", "inspect", "exec", "stop", "restart") and not args.get("container"):
            return False, "container is required"
        return True, ""

    async def _run(self, cmd_parts: list[str]) -> dict[str, Any]:
        proc = await asyncio.create_subprocess_exec(
            "docker", *cmd_parts,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=self._timeout)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            return {"exit_code": -1, "stdout": "", "stderr": "timeout"}
        return {
            "exit_code": proc.returncode,
            "stdout": stdout.decode("utf-8", errors="replace"),
            "stderr": stderr.decode("utf-8", errors="replace"),
        }

    async def execute(self, args: dict[str, Any], stream: StreamBuffer | None = None) -> dict[str, Any]:
        ok, reason = await self.validate(args)
        if not ok:
            return {"error": reason}

        action = args["action"]
        container = args.get("container", "")

        if action == "ps":
            fmt = args.get("format", "{{.ID}}\t{{.Names}}\t{{.Status}}\t{{.Image}}")
            return await self._run(["ps", "-a", f"--format={fmt}"])
        elif action == "logs":
            tail = str(args.get("tail", 100))
            return await self._run(["logs", "--tail", tail, container])
        elif action == "inspect":
            return await self._run(["inspect", container])
        elif action == "exec":
            command = args.get("command", "echo ok")
            return await self._run(["exec", container, "/bin/sh", "-c", command])
        elif action == "stats":
            return await self._run(["stats", "--no-stream", "--format",
                                    "{{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}"])
        elif action == "images":
            return await self._run(["images", "--format", "{{.Repository}}:{{.Tag}}\t{{.Size}}"])
        elif action == "pull":
            image = args.get("image", "")
            if not image:
                return {"error": "image is required"}
            return await self._run(["pull", image])
        elif action == "stop":
            return await self._run(["stop", container])
        elif action == "restart":
            return await self._run(["restart", container])
        return {"error": "unreachable"}
