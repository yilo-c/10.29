"""GenericToolBridge — 通用 CLI 工具桥接器。

根据 ToolSpec 的命令模板执行任意 CLI 工具，无需为每个工具手写 Bridge 类。
支持：
  - 模板变量替换、参数类型转换、detach 模式（GUI 操作）、超时控制
  - stdin 输入（通过 stdin_template，避免 argv 长度/转义问题）
  - 环境变量注入（从 SecureKeyStorage 加密读取，不写回 os.environ）
  - 输出结构化提取（output_regex 命名分组 → extracted 字典）
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
import shlex
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from services.process_lifecycle_manager import ProcessLifecycleManager
from .tool_spec import ToolSpec, ToolCommand, ToolSpecRegistry

logger = logging.getLogger("siyi.tooling.generic_tool_bridge")


@dataclass(frozen=True)
class ToolResult:
    """通用工具执行结果。"""

    success: bool
    action_id: str = ""
    output: str = ""
    error: str = ""
    cli_path: str = ""
    duration_seconds: float = 0.0
    started_at: str = ""
    completed_at: str = ""
    returncode: int | None = None
    process_id: str = ""
    extracted: dict[str, str] = field(default_factory=dict)
    missing_secrets: list[str] = field(default_factory=list)


class GenericToolBridge:
    """通用 CLI 工具桥接器。

    根据 ToolSpecRegistry 中的配置执行任意 CLI 工具。
    所有 tool.* action_id 都通过此桥接器执行。
    """

    def __init__(
        self,
        registry: ToolSpecRegistry,
        working_dir: str | None = None,
        process_lifecycle_manager: ProcessLifecycleManager | None = None,
        secret_resolver: Any = None,
    ):
        self._registry = registry
        self._working_dir = working_dir or "."
        self._lifecycle_manager = process_lifecycle_manager
        self._secret_resolver = secret_resolver or self._default_secret_resolver

    @staticmethod
    def _default_secret_resolver(key_name: str) -> str | None:
        """默认 secret 解析器：四段式优先级（P1.8 升级）。

        通过 SecretResolver 统一入口，复用全局 SecureKeyStorage 单例：
        1. 系统环境变量（如用户在 shell 里 export GH_TOKEN=...）
        2. SecureKeyStorage.retrieve（仅 keyring 后端可用）
        3. settings 加密 token（若 SecretResolver 注入了 settings_repository）

        Returns: 解析出的明文 secret，找不到返回 None
        """
        try:
            from core.secret_resolver import get_default_resolver

            resolver = get_default_resolver()
            return resolver.resolve_secret(key_name)
        except Exception as exc:
            logger.debug("secret resolver for %s failed: %s", key_name, exc)
            return None

    def _resolve_env(
        self, spec: ToolSpec, cmd: ToolCommand
    ) -> tuple[dict[str, str], list[str]]:
        """解析需要注入子进程的环境变量。

        Returns:
            (env_overrides, missing_keys)
            - env_overrides: 已成功解析的 key → value 字典
            - missing_keys: 未配置的 key 名（写入结果，便于前端提示用户配置）
        """
        seen = set()
        keys = []
        for k in [*spec.env_keys, *cmd.env_keys]:
            if k not in seen:
                seen.add(k)
                keys.append(k)

        env_overrides = {}
        missing = []
        for key in keys:
            val = self._secret_resolver(key)
            if val:
                env_overrides[key] = val
            else:
                missing.append(key)
        return env_overrides, missing

    async def execute(self, action_id: str, payload: dict[str, Any]) -> ToolResult:
        """执行一个工具命令。

        Args:
            action_id: 如 "tool.zcode.open"
            payload: 命令参数
        """
        found = self._registry.find_by_action_id(action_id)
        if not found:
            return ToolResult(
                success=False,
                action_id=action_id,
                error=f"unknown tool action: {action_id}",
            )
        spec, cmd = found

        cli_path = spec.resolve_cli()
        if not cli_path:
            return ToolResult(
                success=False,
                action_id=action_id,
                error=f"CLI not found for tool '{spec.tool_id}' (cli_path='{spec.cli_path}')",
            )

        try:
            cmd_args = self._build_command(cli_path, cmd, payload)
        except ValueError as exc:
            return ToolResult(
                success=False,
                action_id=action_id,
                error=f"parameter error: {exc}",
            )

        # ── stdin 输入（避免超长 argv / 转义问题）──
        stdin_data = None
        if cmd.stdin_template:
            try:
                variables = self._build_variables(cli_path, cmd, payload)
                stdin_str = cmd.stdin_template.format(**variables)
                stdin_data = stdin_str.encode("utf-8")
            except (KeyError, ValueError) as exc:
                return ToolResult(
                    success=False,
                    action_id=action_id,
                    error=f"stdin_template error: {exc}",
                )

        # ── 环境变量解析（不写回 os.environ）──
        env_overrides, missing_secrets = self._resolve_env(spec, cmd)

        # Windows 上 .cmd/.bat 需要通过 cmd /c 执行
        import sys as _sys

        if _sys.platform == "win32" and cli_path.lower().endswith((".cmd", ".bat")):
            cmd_args = ["cmd", "/c"] + cmd_args

        started_at = datetime.now(timezone.utc).isoformat()
        start_time = datetime.now(timezone.utc)

        logger.info(
            "GenericToolBridge %s: %s%s%s",
            action_id,
            " ".join(cmd_args[:6]),
            f" stdin={len(stdin_data)}b" if stdin_data else "",
            f" env=[{','.join(env_overrides.keys())}]" if env_overrides else "",
        )

        process_id = f"tool-{uuid.uuid4().hex[:12]}"

        child_env = None
        if env_overrides:
            child_env = {**os.environ, **env_overrides}

        try:
            if cmd.detach:
                # detach 模式：有 lifecycle_manager 时捕获输出以便后续查询
                if self._lifecycle_manager is not None:
                    process = await asyncio.create_subprocess_exec(
                        *cmd_args,
                        stdin=asyncio.subprocess.PIPE
                        if stdin_data is not None
                        else asyncio.subprocess.DEVNULL,
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.PIPE,
                        cwd=self._working_dir,
                        env=child_env,
                    )
                    self._lifecycle_manager.register_process(
                        process_id=process_id,
                        pid=process.pid,
                        action_id=action_id,
                        command=cmd_args,
                        proc=process,
                        capture_output=True,
                    )
                else:
                    process = await asyncio.create_subprocess_exec(
                        *cmd_args,
                        stdin=asyncio.subprocess.PIPE
                        if stdin_data is not None
                        else asyncio.subprocess.DEVNULL,
                        stdout=asyncio.subprocess.DEVNULL,
                        stderr=asyncio.subprocess.DEVNULL,
                        cwd=self._working_dir,
                        env=child_env,
                    )

                if stdin_data is not None and process.stdin:
                    process.stdin.write(stdin_data)
                    process.stdin.close()

                await asyncio.sleep(0.5)
                duration = (datetime.now(timezone.utc) - start_time).total_seconds()
                return ToolResult(
                    success=True,
                    action_id=action_id,
                    cli_path=cli_path,
                    duration_seconds=round(duration, 2),
                    started_at=started_at,
                    completed_at=datetime.now(timezone.utc).isoformat(),
                    process_id=process_id if self._lifecycle_manager is not None else "",
                    missing_secrets=missing_secrets,
                )

            process = await asyncio.create_subprocess_exec(
                *cmd_args,
                stdin=asyncio.subprocess.PIPE if stdin_data else asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=self._working_dir,
                env=child_env,
            )
            if self._lifecycle_manager is not None:
                # communicate() 已接管 stdout/stderr，无需 lifecycle 再捕获
                self._lifecycle_manager.register_process(
                    process_id=process_id,
                    pid=process.pid,
                    action_id=action_id,
                    command=cmd_args,
                    proc=process,
                    capture_output=False,
                )

            try:
                stdout_bytes, stderr_bytes = await asyncio.wait_for(
                    process.communicate(input=stdin_data), timeout=cmd.timeout
                )
            except asyncio.TimeoutError:
                process.kill()
                await process.wait()
                return ToolResult(
                    success=False,
                    action_id=action_id,
                    error=f"timeout>{cmd.timeout}s",
                    cli_path=cli_path,
                    started_at=started_at,
                    completed_at=datetime.now(timezone.utc).isoformat(),
                    returncode=None,
                    process_id=process_id if self._lifecycle_manager is not None else "",
                    missing_secrets=missing_secrets,
                )

            duration = (datetime.now(timezone.utc) - start_time).total_seconds()
            output = stdout_bytes.decode("utf-8", errors="replace").strip()
            error = stderr_bytes.decode("utf-8", errors="replace").strip()
            success = process.returncode == 0

            extracted = self._extract_output(cmd, output)

            return ToolResult(
                success=success,
                action_id=action_id,
                output=output,
                error=error if not success else "",
                cli_path=cli_path,
                duration_seconds=round(duration, 2),
                started_at=started_at,
                completed_at=datetime.now(timezone.utc).isoformat(),
                returncode=process.returncode,
                process_id=process_id if self._lifecycle_manager is not None else "",
                extracted=extracted,
                missing_secrets=missing_secrets,
            )

        except Exception as exc:
            logger.exception("GenericToolBridge %s failed", action_id)
            return ToolResult(
                success=False,
                action_id=action_id,
                error=f"{type(exc).__name__}: {exc}",
                cli_path=cli_path,
                started_at=started_at,
                completed_at=datetime.now(timezone.utc).isoformat(),
                returncode=None,
                missing_secrets=missing_secrets,
            )

    def _build_variables(
        self, cli_path: str, cmd: ToolCommand, payload: dict[str, Any]
    ) -> dict[str, str]:
        """构建模板变量字典（{cli} + 参数），stdin_template 复用此方法。"""
        for param in cmd.params:
            if param.required and param.name not in payload:
                raise ValueError(f"missing required parameter: {param.name}")

        variables = {"cli": cli_path}
        for param in cmd.params:
            value = payload.get(param.name, param.default)
            if value is None:
                continue
            if param.type == "bool":
                value = "true" if value else "false"
            elif param.type == "int":
                value = str(int(value))
            else:
                value = str(value)
            variables[param.name] = value
        return variables

    def _extract_output(self, cmd: ToolCommand, output: str) -> dict[str, str]:
        """根据 output_regex 从 stdout 提取命名分组到字典。"""
        if not cmd.output_regex:
            return {}
        try:
            match = re.search(cmd.output_regex, output, re.MULTILINE)
            if match:
                return {k: v for k, v in match.groupdict().items() if v is not None}
        except re.error as exc:
            logger.warning(
                "output_regex compile failed for command '%s': %s",
                cmd.name,
                exc,
            )
        return {}

    def _build_command(
        self, cli_path: str, cmd: ToolCommand, payload: dict[str, Any]
    ) -> list[str]:
        """根据模板和参数构建命令行。

        模板格式："{cli} {path} --option {flag}"
        变量从 payload 中提取，{cli} 固定替换为 cli_path。

        策略：先分割模板为 tokens，再对每个 token 做变量替换，
        避免路径中的空格导致分割错误。
        """
        variables = self._build_variables(cli_path, cmd, payload)

        template = cmd.template
        try:
            # posix=False 保留 Windows 路径中的反斜杠
            template_tokens = shlex.split(template, posix=False)
        except ValueError:
            template_tokens = template.split()

        cmd_args = []
        for token in template_tokens:
            if "{" not in token:
                cmd_args.append(token)
                continue
            try:
                expanded = token.format(**variables)
                # 去掉 posix=False 保留的引号
                expanded = expanded.strip('"').strip("'")
                if expanded:
                    cmd_args.append(expanded)
            except KeyError:
                # 未提供的变量 → 跳过该 token
                continue
        return cmd_args
