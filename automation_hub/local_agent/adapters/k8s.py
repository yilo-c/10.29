"""local_agent.adapters.k8s — Kubernetes 操作适配器（通过 kubectl）。"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from local_agent.adapters.base import AdapterBase
from local_agent.stream import StreamBuffer

logger = logging.getLogger("local_agent.adapters.k8s")


class K8sAdapter(AdapterBase):
    """通过 kubectl CLI 与 Kubernetes 集群交互。"""

    name = "k8s"

    def __init__(self, kubeconfig: str | None = None, context: str | None = None, timeout: float = 120) -> None:
        self._kubeconfig = kubeconfig
        self._context = context
        self._timeout = timeout

    def _base_args(self) -> list[str]:
        args: list[str] = []
        if self._kubeconfig:
            args += ["--kubeconfig", self._kubeconfig]
        if self._context:
            args += ["--context", self._context]
        return args

    async def validate(self, args: dict[str, Any]) -> tuple[bool, str]:
        action = args.get("action", "")
        allowed = ("get", "describe", "logs", "top", "exec", "apply", "delete", "rollout")
        if action not in allowed:
            return False, f"unknown action: {action}, allowed: {allowed}"
        return True, ""

    async def _run(self, parts: list[str]) -> dict[str, Any]:
        full = ["kubectl"] + self._base_args() + parts
        proc = await asyncio.create_subprocess_exec(
            *full,
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
        ns = args.get("namespace")
        ns_args = ["-n", ns] if ns else []
        resource = args.get("resource", "")
        name = args.get("name", "")

        if action == "get":
            fmt = args.get("output", "wide")
            cmd = ["get", resource] + ([name] if name else []) + ns_args + ["-o", fmt]
            return await self._run(cmd)
        elif action == "describe":
            cmd = ["describe", resource] + ([name] if name else []) + ns_args
            return await self._run(cmd)
        elif action == "logs":
            tail = str(args.get("tail", 100))
            container_flag = ["-c", args["container"]] if args.get("container") else []
            cmd = ["logs", name, "--tail", tail] + container_flag + ns_args
            return await self._run(cmd)
        elif action == "top":
            cmd = ["top", resource] + ns_args
            return await self._run(cmd)
        elif action == "exec":
            command = args.get("command", "echo ok")
            cmd = ["exec", name] + ns_args + ["--", "/bin/sh", "-c", command]
            return await self._run(cmd)
        elif action == "apply":
            manifest = args.get("manifest", "")
            if not manifest:
                return {"error": "manifest is required (file path or inline yaml)"}
            cmd = ["apply", "-f", manifest] + ns_args
            return await self._run(cmd)
        elif action == "delete":
            cmd = ["delete", resource, name] + ns_args
            return await self._run(cmd)
        elif action == "rollout":
            sub = args.get("sub_action", "status")  # status | restart | undo
            cmd = ["rollout", sub, f"{resource}/{name}"] + ns_args
            return await self._run(cmd)
        return {"error": "unreachable"}
