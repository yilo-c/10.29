"""TraeDriver — Trae IDE 驱动。

基于 TraeBridge 实现，包装为 AbstractAgentDriver 接口。
让思忆集能够通过自然语言调度 Trae IDE：打开项目、跳转文件、管理扩展。
"""

from __future__ import annotations

import asyncio
import logging
import re
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

logger = logging.getLogger("siyi.orchestration.drivers.trae")

# 延迟导入 TraeBridge 避免循环依赖
_TraeBridge = None


def _get_trae_bridge():
    global _TraeBridge
    if _TraeBridge is None:
        from integrations.trae_bridge import TraeBridge

        _TraeBridge = TraeBridge
    return _TraeBridge


class TraeDriver:
    """Trae IDE 驱动。

    封装 Trae CLI 为思忆集的一个标准 AgentDriver，
    支持打开项目/文件、管理扩展等 IDE 控制操作。
    """

    agent_info = AgentInfo(
        agent_id="trae",
        agent_type=AgentType.TRAE,
        display_name="Trae IDE",
        description="Trae IDE 控制器 — 打开项目/文件、管理扩展、比较文件",
        capabilities=[
            AgentCapability.FILE_OPERATIONS,
            AgentCapability.SYSTEM_OPERATION,
        ],
        version="",
        is_available=False,
        status=AgentStatus.UNAVAILABLE,
        max_concurrent=1,
        cost_rank=10,  # 本地操作，无 API 成本
    )

    def __init__(
        self,
        settings_repository=None,
        working_dir: str | None = None,
    ):
        self._settings = settings_repository
        self._working_dir = working_dir
        self._bridge = None
        self._init_bridge()

    def _init_bridge(self):
        Bridge = _get_trae_bridge()
        self._bridge = Bridge(
            settings_repository=self._settings,
            working_dir=self._working_dir,
        )
        self.agent_info.is_available = self._bridge.is_available
        self.agent_info.status = AgentStatus.IDLE if self._bridge.is_available else AgentStatus.UNAVAILABLE
        self.agent_info.version = self._bridge.version

    async def execute(
        self,
        task: str,
        context: dict[str, Any] | None = None,
        *,
        timeout: int = 60,
        progress_callback: Callable[[str], None] | None = None,
    ) -> AgentInvocation:
        """执行一个 Trae IDE 控制任务。

        支持的自然语言意图：
        - "打开项目 D:/xxx" / "打开文件夹 ..." → open_project
        - "打开文件 xxx.py 第 42 行" → open_file
        - "列出扩展" / "已安装扩展" → list_extensions
        - "安装扩展 ms-python.python" → install_extension
        - "卸载扩展 xxx" → uninstall_extension
        - "比较文件 a.py b.py" → diff
        """
        invocation_id = _new_id()
        started_at = _now()
        self.agent_info.status = AgentStatus.BUSY

        if not self._bridge or not self._bridge.is_available:
            self.agent_info.status = AgentStatus.UNAVAILABLE
            return AgentInvocation(
                invocation_id=invocation_id,
                agent_id="trae",
                task=task,
                status="failed",
                error="Trae CLI not available",
                started_at=started_at,
                completed_at=_now(),
            )

        try:
            import time

            start_time = time.monotonic()

            result = await self._dispatch_task(task, context or {}, progress_callback)

            duration = time.monotonic() - start_time
            self.agent_info.invocation_count += 1
            self.agent_info.last_used_at = _now()

            status = "completed" if result.success else "failed"
            return AgentInvocation(
                invocation_id=invocation_id,
                agent_id="trae",
                task=task,
                context=context or {},
                status=status,
                result={
                    "action": result.action,
                    "output": result.output,
                    "cli_path": result.cli_path,
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
                agent_id="trae",
                task=task,
                status="cancelled",
                started_at=started_at,
                completed_at=_now(),
            )
        except Exception as exc:
            logger.exception("TraeDriver execute failed")
            return AgentInvocation(
                invocation_id=invocation_id,
                agent_id="trae",
                task=task,
                status="failed",
                error=f"{type(exc).__name__}: {exc}",
                started_at=started_at,
                completed_at=_now(),
            )
        finally:
            self.agent_info.status = AgentStatus.IDLE

    async def _dispatch_task(
        self,
        task: str,
        context: dict[str, Any],
        progress_callback: Callable[[str], None] | None,
    ) -> Any:
        """解析任务意图并分发到对应的 TraeBridge 方法。"""
        from integrations.trae_bridge import TraeResult

        task_lower = task.lower().strip()
        if self._bridge is None:
            return {"success": False, "error": "Trae 未连接（bridge 不可用）"}

        # 列出扩展
        if any(kw in task_lower for kw in ("列出扩展", "已安装扩展", "list extension", "扩展列表")):
            if progress_callback:
                progress_callback("正在列出已安装扩展...")
            return await self._bridge.list_extensions()

        # 安装扩展
        install_match = re.search(
            r"(?:安装扩展|install(?:\s+extension)?)\s+([a-zA-Z0-9_.-]+(?:\.\w+)?)",
            task_lower,
        )
        if install_match:
            ext_id = install_match.group(1)
            if progress_callback:
                progress_callback(f"正在安装扩展 {ext_id}...")
            return await self._bridge.install_extension(ext_id)

        # 卸载扩展
        uninstall_match = re.search(
            r"(?:卸载扩展|uninstall(?:\s+extension)?)\s+([a-zA-Z0-9_.-]+(?:\.\w+)?)",
            task_lower,
        )
        if uninstall_match:
            ext_id = uninstall_match.group(1)
            if progress_callback:
                progress_callback(f"正在卸载扩展 {ext_id}...")
            return await self._bridge.uninstall_extension(ext_id)

        # 比较文件
        diff_match = re.search(
            r"(?:比较文件|diff?)\s+(.+?)\s+(.+)",
            task_lower,
        )
        if diff_match:
            f1, f2 = diff_match.group(1).strip(), diff_match.group(2).strip()
            if progress_callback:
                progress_callback(f"正在比较 {f1} 和 {f2}...")
            return await self._bridge.diff(f1, f2)

        # 打开文件（带行号）
        # 匹配: "打开文件 xxx.py 第 42 行" / "open file xxx.py line 42"
        file_line_match = re.search(
            r"(?:打开文件|open\s+file)\s+(.+?)(?:\s+(?:第|line)\s*(\d+))?(?:\s+(?:列|col(?:umn)?)\s*(\d+))?$",
            task,
            re.IGNORECASE,
        )
        if file_line_match:
            file_path = file_line_match.group(1).strip().strip('"').strip("'")
            line = int(file_line_match.group(2)) if file_line_match.group(2) else None
            column = int(file_line_match.group(3)) if file_line_match.group(3) else None
            if progress_callback:
                progress_callback(f"正在打开文件 {file_path}...")
            return await self._bridge.open_file(file_path, line=line, column=column)

        # 打开项目/文件夹
        # 匹配: "打开项目 D:/xxx" / "打开文件夹 ..." / "open project ..."
        project_match = re.search(
            r"(?:打开项目|打开文件夹|open\s+(?:project|folder))\s+(.+)",
            task,
            re.IGNORECASE,
        )
        if project_match:
            path = project_match.group(1).strip().strip('"').strip("'")
            if progress_callback:
                progress_callback(f"正在打开项目 {path}...")
            return await self._bridge.open_project(path)

        # 兜底：如果 task 看起来是一个路径，直接尝试打开
        if re.match(r"^[A-Za-z]:[\\/]|^/", task.strip()):
            path = task.strip().strip('"').strip("'")
            if progress_callback:
                progress_callback(f"正在打开 {path}...")
            return await self._bridge.open_project(path)

        # 无法识别
        return TraeResult(
            success=False,
            action="unknown",
            error=f"无法识别任务意图: {task[:100]}。支持: 打开项目/文件、列出/安装/卸载扩展、比较文件",
        )

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
