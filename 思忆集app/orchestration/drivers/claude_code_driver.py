"""ClaudeCodeDriver — Claude Code CLI 驱动。

基于现有的 CCBridge 实现，包装为 AbstractAgentDriver 接口。
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Callable

from ..agent_driver import (
    AbstractAgentDriver,
    AgentCapability,
    AgentInfo,
    AgentInvocation,
    AgentStatus,
    AgentType,
    _new_id,
    _now,
)

logger = logging.getLogger("siyi.orchestration.drivers.claude_code")

# 延迟导入 CCBridge 避免循环依赖
_CCBridge = None


def _get_cc_bridge():
    global _CCBridge
    if _CCBridge is None:
        from assistant.cc_bridge import CCBridge
        _CCBridge = CCBridge
    return _CCBridge


class ClaudeCodeDriver:
    """Claude Code CLI 驱动。

    封装 claude.exe 为思忆集的一个标准 AgentDriver，
    支持代码生成、审查、重构、测试等编码任务。
    """

    agent_info = AgentInfo(
        agent_id="claude_code",
        agent_type=AgentType.CLAUDE_CODE,
        display_name="Claude Code",
        description="AI 编码助手 — 代码生成、重构、审查、测试",
        capabilities=[
            AgentCapability.CODE_REVIEW,
            AgentCapability.CODE_GENERATION,
            AgentCapability.FILE_OPERATIONS,
            AgentCapability.REASONING,
        ],
        version="",
        is_available=False,
        status=AgentStatus.UNAVAILABLE,
        max_concurrent=1,
        cost_rank=3,
    )

    def __init__(
        self,
        settings_repository=None,
        memory_service=None,
        working_dir: str | None = None,
    ):
        self._settings = settings_repository
        self._memory = memory_service
        self._working_dir = working_dir
        self._bridge = None
        self._init_bridge()

    def _init_bridge(self):
        Bridge = _get_cc_bridge()
        self._bridge = Bridge(
            settings_repository=self._settings,
            memory_service=self._memory,
            working_dir=self._working_dir,
        )
        self.agent_info.is_available = self._bridge.is_available
        self.agent_info.status = AgentStatus.IDLE if self._bridge.is_available else AgentStatus.UNAVAILABLE
        self.agent_info.version = ""

    async def execute(
        self,
        task: str,
        context: dict[str, Any] | None = None,
        *,
        timeout: int = 300,
        progress_callback: Callable[[str], None] | None = None,
    ) -> AgentInvocation:
        invocation_id = _new_id()
        started_at = _now()
        self.agent_info.status = AgentStatus.BUSY

        if not self._bridge or not self._bridge.is_available:
            self.agent_info.status = AgentStatus.UNAVAILABLE
            return AgentInvocation(
                invocation_id=invocation_id,
                agent_id="claude_code",
                task=task,
                status="failed",
                error="Claude Code CLI not available",
                started_at=started_at,
                completed_at=_now(),
            )

        try:
            import time
            start_time = time.monotonic()

            cc_context = dict(context or {})
            result = await self._bridge.execute(
                task=task,
                context=cc_context,
                timeout=timeout,
                progress_callback=progress_callback,
            )

            duration = time.monotonic() - start_time
            self.agent_info.invocation_count += 1
            self.agent_info.last_used_at = _now()

            return AgentInvocation(
                invocation_id=invocation_id,
                agent_id="claude_code",
                task=task,
                context=cc_context,
                status=result.status.value if hasattr(result.status, 'value') else str(result.status),
                result={
                    "summary": result.summary,
                    "files_created": result.files_created,
                    "files_modified": result.files_modified,
                    "commands_run": result.commands_run,
                    "output_log": result.output_log,
                    "duration_seconds": result.duration_seconds,
                },
                error=result.error,
                started_at=started_at,
                completed_at=_now(),
                duration_seconds=round(duration, 2),
            )

        except asyncio.CancelledError:
            return AgentInvocation(
                invocation_id=invocation_id,
                agent_id="claude_code",
                task=task,
                status="cancelled",
                started_at=started_at,
                completed_at=_now(),
            )
        except Exception as exc:
            logger.exception("ClaudeCodeDriver execute failed")
            return AgentInvocation(
                invocation_id=invocation_id,
                agent_id="claude_code",
                task=task,
                status="failed",
                error=f"{type(exc).__name__}: {exc}",
                started_at=started_at,
                completed_at=_now(),
            )
        finally:
            self.agent_info.status = AgentStatus.IDLE

    def cancel(self) -> bool:
        if self._bridge:
            return self._bridge.cancel()
        return False

    @property
    def is_available(self) -> bool:
        if self._bridge:
            available = self._bridge.is_available
            self.agent_info.is_available = available
            return available
        return False
