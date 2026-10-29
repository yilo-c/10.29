"""OpenClawDriver — OpenClaw CLI 驱动。

通过 `openclaw agent --local --message` 非交互模式调用。
"""

from __future__ import annotations

import asyncio
import json
import logging
import shutil
from typing import Any, Callable

from ..agent_driver import (
    AgentCapability,
    AgentInfo,
    AgentInvocation,
    AgentStatus,
    AgentType,
    _new_id,
    _now,
)

from core.subprocess_text import run_text

logger = logging.getLogger("siyi.orchestration.drivers.openclaw")


class OpenClawDriver:
    """OpenClaw CLI 驱动。

    调用 openclaw agent 非交互模式执行任务。
    支持多步骤任务规划、长文本处理。
    """

    agent_info = AgentInfo(
        agent_id="openclaw",
        agent_type=AgentType.OPENCLAW,
        display_name="OpenClaw",
        description="通用 AI Agent — 多步骤任务规划、复杂对话、长文本处理",
        capabilities=[
            AgentCapability.TASK_PLANNING,
            AgentCapability.CONVERSATION,
            AgentCapability.REASONING,
            AgentCapability.CODE_GENERATION,
        ],
        version="",
        is_available=False,
        status=AgentStatus.UNAVAILABLE,
        max_concurrent=1,
        cost_rank=4,
    )

    _CLI_CANDIDATES = ("openclaw", "openclaw.cmd")

    def __init__(self):
        self._cli_path = None
        self._active_process = None
        self._resolve_cli()

    def _resolve_cli(self) -> None:
        for candidate in self._CLI_CANDIDATES:
            found = shutil.which(candidate)
            if found:
                self._cli_path = found
                self.agent_info.is_available = True
                self.agent_info.status = AgentStatus.IDLE

                try:
                    r = run_text([found, "--version"], timeout=10)
                    if r.returncode == 0:
                        self.agent_info.version = r.stdout.strip()
                except Exception:
                    pass
                return
        self.agent_info.is_available = False
        self.agent_info.status = AgentStatus.UNAVAILABLE
        self.agent_info.version = ""

    async def execute(
        self,
        task: str,
        context: dict[str, Any] | None = None,
        *,
        timeout: int = 600,
        progress_callback: Callable[[str], None] | None = None,
    ) -> AgentInvocation:
        invocation_id = _new_id()
        started_at = _now()
        self.agent_info.status = AgentStatus.BUSY

        if not self._cli_path:
            self.agent_info.status = AgentStatus.UNAVAILABLE
            return AgentInvocation(
                invocation_id=invocation_id,
                agent_id="openclaw",
                task=task,
                status="failed",
                error="OpenClaw CLI not found",
                started_at=started_at,
                completed_at=_now(),
            )

        try:
            import time

            start_time = time.monotonic()

            # openclaw agent --local --message "<task>" --json
            cmd = [self._cli_path, "agent", "--local", "--message", task, "--json"]

            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            self._active_process = process

            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout)

            duration = time.monotonic() - start_time
            stdout_str = stdout.decode("utf-8", errors="replace") if stdout else ""
            stderr_str = stderr.decode("utf-8", errors="replace") if stderr else ""
            full_output = stdout_str + ("\n" + stderr_str if stderr_str else "")

            # --json 模式下逐行找第一段合法 JSON
            result_data = {}
            for line in stdout_str.split("\n"):
                line = line.strip()
                if line.startswith("{"):
                    try:
                        result_data = json.loads(line)
                        break
                    except json.JSONDecodeError:
                        continue

            if not result_data:
                # 非 JSON 输出：整段 stdout 作为回复文本
                result_data = {"response": stdout_str.strip()}

            success = process.returncode == 0
            self.agent_info.invocation_count += 1
            self.agent_info.last_used_at = _now()

            return AgentInvocation(
                invocation_id=invocation_id,
                agent_id="openclaw",
                task=task,
                context=dict(context or {}),
                status="completed" if success else "failed",
                result=result_data,
                error=stderr_str if not success else "",
                started_at=started_at,
                completed_at=_now(),
                duration_seconds=round(duration, 2),
            )

        except asyncio.TimeoutError:
            self._kill_process()
            return AgentInvocation(
                invocation_id=invocation_id,
                agent_id="openclaw",
                task=task,
                status="failed",
                error=f"timeout after {timeout}s",
                started_at=started_at,
                completed_at=_now(),
            )
        except asyncio.CancelledError:
            return AgentInvocation(
                invocation_id=invocation_id,
                agent_id="openclaw",
                task=task,
                status="cancelled",
                started_at=started_at,
                completed_at=_now(),
            )
        except Exception as exc:
            logger.exception("OpenClawDriver execute failed")
            return AgentInvocation(
                invocation_id=invocation_id,
                agent_id="openclaw",
                task=task,
                status="failed",
                error=f"{type(exc).__name__}: {exc}",
                started_at=started_at,
                completed_at=_now(),
            )
        finally:
            self._active_process = None
            self.agent_info.status = AgentStatus.IDLE

    def cancel(self) -> bool:
        return self._kill_process()

    def _kill_process(self) -> bool:
        if self._active_process and self._active_process.returncode is None:
            try:
                self._active_process.terminate()
                return True
            except Exception:
                pass
        return False

    @property
    def is_available(self) -> bool:
        available = self._cli_path is not None
        self.agent_info.is_available = available
        return available
