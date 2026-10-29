"""TraeBridge — Trae IDE CLI 适配器。

封装 Trae CLI (trae-cn / trae-solo) 为思忆集的一个工具，
让思忆集能够启动 Trae、打开项目/文件、管理扩展。

数据流：
    用户/Agent 请求打开项目
      → TraeBridge.open_project(path)
        → 解析 CLI 路径
        → 构建命令行 (trae-cn <path>)
        → 启动子进程
        → 返回结果

支持的 Trae CLI 命令（基于 VS Code CLI）：
    - open <path>              打开文件/文件夹
    - --goto <file:line:col>   跳转到指定位置
    - --new-window             新窗口打开
    - --diff <f1> <f2>         比较文件
    - --install-extension <id> 安装扩展
    - --uninstall-extension <id> 卸载扩展
    - --list-extensions        列出已安装扩展

集成点:
    - orchestration/drivers/trae_driver.py: AgentDriver 包装
    - backend/handlers/_trae.py: RPC 方法暴露
    - assistant/capability/_shell.py: APP_LAUNCH_ALLOWLIST 白名单
"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from core.subprocess_text import run_text

logger = logging.getLogger("siyi.integrations.trae_bridge")


# ── Public types ───────────────────────────────────────────────────────


@dataclass(frozen=True)
class TraeResult:
    """Result of a Trae CLI operation."""

    success: bool
    action: str = ""
    output: str = ""
    error: str = ""
    cli_path: str = ""
    duration_seconds: float = 0.0
    started_at: str = ""
    completed_at: str = ""


# ── TraeBridge ─────────────────────────────────────────────────────────


class TraeBridge:
    """Trae IDE CLI bridge.

    Wraps trae-cn / trae-solo CLI as a tool that SiyiJi can use
    to launch Trae, open projects/files, and manage extensions.
    """

    # Trae CLI 候选名称（按优先级）
    _CLI_CANDIDATES = (
        "trae-cn",
        "trae-solo",
        "trae",
    )

    # 常见安装路径（Windows）
    _INSTALL_PATHS = [
        # Trae CN
        Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Trae CN",
        Path("D:/新建文件夹 (2)/Trae CN"),
        Path("C:/Program Files/Trae CN"),
        Path("C:/Program Files (x86)/Trae CN"),
        # Trae Solo
        Path("D:/Program Files (x86)/TRAE_Work/TRAE SOLO"),
        Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Trae",
        Path("C:/Program Files/Trae"),
        Path("C:/Program Files (x86)/Trae"),
    ]

    def __init__(self, settings_repository=None, working_dir: str | Path | None = None):
        self._settings = settings_repository
        self._working_dir = Path(working_dir).resolve() if working_dir else Path.cwd()
        self._cli_path: str | None = None
        self._trae_version: str = ""
        self._active_process: asyncio.subprocess.Process | None = None

        # Resolve Trae CLI
        self._cli_path = self._resolve_cli()

    # ── Public API ────────────────────────────────────────────────────

    async def open_project(
        self,
        path: str | Path,
        *,
        new_window: bool = False,
        reuse_window: bool = False,
        wait: bool = False,
    ) -> TraeResult:
        """打开项目/文件夹。

        Args:
            path: 项目路径。
            new_window: 强制在新窗口打开。
            reuse_window: 强制在已有窗口打开。
            wait: 等待窗口关闭后返回。
        """
        target = Path(path).resolve()
        if not target.exists():
            return TraeResult(
                success=False,
                action="open_project",
                error=f"path not found: {target}",
            )

        args = []
        if new_window:
            args.append("--new-window")
        elif reuse_window:
            args.append("--reuse-window")
        if wait:
            args.append("--wait")

        return await self._run_cli([*args, str(target)], action="open_project", detach=not wait)

    async def open_file(
        self,
        file_path: str | Path,
        line: int | None = None,
        column: int | None = None,
        *,
        new_window: bool = False,
    ) -> TraeResult:
        """打开文件并可选跳转到指定行/列。

        Args:
            file_path: 文件路径。
            line: 行号（从 1 开始）。
            column: 列号（从 1 开始）。
            new_window: 在新窗口打开。
        """
        target = Path(file_path).resolve()
        if not target.is_file():
            return TraeResult(
                success=False,
                action="open_file",
                error=f"file not found: {target}",
            )

        if line is not None:
            goto = f"{target}:{line}"
            if column is not None:
                goto += f":{column}"
            args = ["--goto", goto]
        else:
            args = [str(target)]

        if new_window:
            args.insert(0, "--new-window")

        return await self._run_cli(args, action="open_file", detach=True)

    async def diff(self, file1: str | Path, file2: str | Path) -> TraeResult:
        """比较两个文件。"""
        f1, f2 = Path(file1).resolve(), Path(file2).resolve()
        for f, label in [(f1, "file1"), (f2, "file2")]:
            if not f.is_file():
                return TraeResult(
                    success=False,
                    action="diff",
                    error=f"{label} not found: {f}",
                )
        return await self._run_cli(["--diff", str(f1), str(f2)], action="diff", detach=True)

    async def install_extension(self, ext_id: str) -> TraeResult:
        """安装扩展。

        Args:
            ext_id: 扩展 ID（如 "ms-python.python"）或 VSIX 文件路径。
        """
        return await self._run_cli(
            ["--install-extension", ext_id],
            action="install_extension",
            detach=False,
            timeout=120,
        )

    async def uninstall_extension(self, ext_id: str) -> TraeResult:
        """卸载扩展。"""
        return await self._run_cli(
            ["--uninstall-extension", ext_id],
            action="uninstall_extension",
            detach=False,
            timeout=60,
        )

    async def list_extensions(self) -> TraeResult:
        """列出已安装的扩展。"""
        return await self._run_cli(
            ["--list-extensions", "--show-versions"],
            action="list_extensions",
            detach=False,
            timeout=30,
        )

    def cancel(self) -> bool:
        """取消当前正在运行的 CLI 进程。"""
        if self._active_process is not None:
            try:
                self._active_process.terminate()
                self._active_process = None
                return True
            except Exception:
                logger.exception("failed to terminate trae process")
        return False

    @property
    def is_available(self) -> bool:
        return self._cli_path is not None

    @property
    def cli_path(self) -> str | None:
        return self._cli_path

    @property
    def version(self) -> str:
        return self._trae_version

    def get_status(self) -> dict[str, Any]:
        """返回 Trae 安装状态。"""
        return {
            "available": self.is_available,
            "cli_path": self._cli_path or "",
            "version": self._trae_version,
            "working_dir": str(self._working_dir),
        }

    # ── CLI execution ─────────────────────────────────────────────────

    async def _run_cli(
        self,
        args: list[str],
        *,
        action: str,
        detach: bool = False,
        timeout: int = 30,
    ) -> TraeResult:
        """执行 Trae CLI 命令。

        Args:
            args: CLI 参数列表。
            action: 操作名称（用于结果记录）。
            detach: True 表示启动后立即返回（不等待完成，用于打开窗口等 GUI 操作）。
            timeout: 超时秒数（仅在 detach=False 时生效）。
        """
        if not self._cli_path:
            return TraeResult(
                success=False,
                action=action,
                error="Trae CLI not found. Install Trae CN or Trae Solo.",
            )

        cmd = [self._cli_path, *args]
        started_at = datetime.now(timezone.utc).isoformat()
        start_time = datetime.now(timezone.utc)

        logger.info("TraeBridge %s: %s", action, " ".join(cmd[:6]))

        try:
            if detach:
                # GUI 操作：启动后立即返回
                process = await asyncio.create_subprocess_exec(
                    *cmd,
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL,
                    cwd=str(self._working_dir),
                )
                self._active_process = process
                # 等待短暂时间确认进程启动成功
                await asyncio.sleep(0.5)
                duration = (datetime.now(timezone.utc) - start_time).total_seconds()
                return TraeResult(
                    success=True,
                    action=action,
                    cli_path=self._cli_path,
                    duration_seconds=round(duration, 2),
                    started_at=started_at,
                    completed_at=datetime.now(timezone.utc).isoformat(),
                )
            else:
                # 命令行操作：等待完成并捕获输出
                process = await asyncio.create_subprocess_exec(
                    *cmd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    cwd=str(self._working_dir),
                )
                self._active_process = process

                try:
                    stdout_bytes, stderr_bytes = await asyncio.wait_for(process.communicate(), timeout=timeout)
                except asyncio.TimeoutError:
                    process.kill()
                    await process.wait()
                    return TraeResult(
                        success=False,
                        action=action,
                        error=f"timeout>{timeout}s",
                        cli_path=self._cli_path,
                        started_at=started_at,
                        completed_at=datetime.now(timezone.utc).isoformat(),
                    )

                duration = (datetime.now(timezone.utc) - start_time).total_seconds()
                output = stdout_bytes.decode("utf-8", errors="replace").strip()
                error = stderr_bytes.decode("utf-8", errors="replace").strip()
                success = process.returncode == 0

                return TraeResult(
                    success=success,
                    action=action,
                    output=output,
                    error=error if not success else "",
                    cli_path=self._cli_path,
                    duration_seconds=round(duration, 2),
                    started_at=started_at,
                    completed_at=datetime.now(timezone.utc).isoformat(),
                )
        except asyncio.CancelledError:
            return TraeResult(
                success=False,
                action=action,
                error="cancelled",
                cli_path=self._cli_path,
                started_at=started_at,
                completed_at=datetime.now(timezone.utc).isoformat(),
            )
        except Exception as exc:
            logger.exception("TraeBridge %s failed", action)
            return TraeResult(
                success=False,
                action=action,
                error=f"{type(exc).__name__}: {exc}",
                cli_path=self._cli_path,
                started_at=started_at,
                completed_at=datetime.now(timezone.utc).isoformat(),
            )
        finally:
            self._active_process = None

    # ── CLI resolution ───────────────────────────────────────────────

    def _resolve_cli(self) -> str | None:
        """定位 Trae CLI 可执行文件。"""
        # 1. 检查 settings
        if self._settings:
            try:
                path = self._settings.get_setting("trae_bridge_cli_path", "")
                if path and Path(path).is_file():
                    self._detect_version(path)
                    return str(Path(path).resolve())
            except Exception:
                logger.debug("trae_bridge_cli_path setting read failed", exc_info=True)

        # 2. 搜索 PATH
        for candidate in self._CLI_CANDIDATES:
            found = shutil.which(candidate)
            if found:
                self._detect_version(found)
                return found

        # 3. 搜索常见安装路径
        for install_dir in self._INSTALL_PATHS:
            if not install_dir.exists():
                continue
            # 查找 bin 目录下的 CLI 脚本
            bin_dir = install_dir / "bin"
            if bin_dir.exists():
                for cli_name in self._CLI_CANDIDATES:
                    # 优先用无扩展名的 shell 脚本（跨平台）
                    cli_file = bin_dir / cli_name
                    if cli_file.is_file():
                        self._detect_version(str(cli_file))
                        return str(cli_file)
                    # Windows .cmd 包装器
                    cli_cmd = bin_dir / f"{cli_name}.cmd"
                    if cli_cmd.is_file():
                        self._detect_version(str(cli_cmd))
                        return str(cli_cmd)

        logger.info("Trae CLI not found — TraeBridge will be unavailable")
        return None

    def _detect_version(self, cli_path: str) -> None:
        """通过 --version 探测 Trae 版本。"""
        try:
            result = run_text([cli_path, "--version"], timeout=5, shell=False)
            if result.returncode == 0:
                self._trae_version = result.stdout.strip().split("\n")[0]
        except Exception:
            self._trae_version = "unknown"
