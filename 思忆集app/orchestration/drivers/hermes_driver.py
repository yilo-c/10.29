"""HermesDriver — Hermes Agent CLI 驱动。

通过 `hermes -z` 非交互模式调用。
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

logger = logging.getLogger("siyi.orchestration.drivers.hermes")


class HermesDriver:
    """Hermes Agent CLI 驱动。

    调用 hermes 非交互模式执行长时间运行的任务。
    适合数据收集、分析、监控等耗时任务。
    """

    agent_info = AgentInfo(
        agent_id="hermes",
        agent_type=AgentType.HERMES,
        display_name="Hermes Agent",
        description="长时间运行 AI Agent — 数据收集、分析、监控、持续任务",
        capabilities=[
            AgentCapability.LONG_RUNNING_TASK,
            AgentCapability.CONVERSATION,
            AgentCapability.DATA_ANALYSIS,
            AgentCapability.WEB_SEARCH,
        ],
        version="",
        is_available=False,
        status=AgentStatus.UNAVAILABLE,
        max_concurrent=1,
        cost_rank=6,
    )

    _CLI_CANDIDATES = ("hermes", "hermes.exe")

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
                agent_id="hermes",
                task=task,
                status="failed",
                error="Hermes CLI not found",
                started_at=started_at,
                completed_at=_now(),
            )

        try:
            import time

            start_time = time.monotonic()

            # hermes -z "<task>" —— 非交互一次性执行
            cmd = [self._cli_path, "-z", task]

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

            success = process.returncode == 0
            self.agent_info.invocation_count += 1
            self.agent_info.last_used_at = _now()

            # 默认整段 stdout 作为回复；若是 JSON 对象则改用解析结果
            result_data = {"response": stdout_str.strip()}
            try:
                parsed = json.loads(stdout_str.strip())
                if isinstance(parsed, dict):
                    result_data = parsed
            except (json.JSONDecodeError, TypeError):
                pass

            return AgentInvocation(
                invocation_id=invocation_id,
                agent_id="hermes",
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
                agent_id="hermes",
                task=task,
                status="failed",
                error=f"timeout after {timeout}s",
                started_at=started_at,
                completed_at=_now(),
            )
        except asyncio.CancelledError:
            return AgentInvocation(
                invocation_id=invocation_id,
                agent_id="hermes",
                task=task,
                status="cancelled",
                started_at=started_at,
                completed_at=_now(),
            )
        except Exception as exc:
            logger.exception("HermesDriver execute failed")
            return AgentInvocation(
                invocation_id=invocation_id,
                agent_id="hermes",
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
