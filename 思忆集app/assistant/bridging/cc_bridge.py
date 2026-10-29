"""CCBridge — Claude Code CLI "超级工具"适配器。

封装 Claude Code CLI (claude.exe) 为思忆集的一个超级工具，
让 Judge 判定为 complex/very_complex 的编码/分析任务自动路由到此执行。

数据流：
    Judge 判定复杂编码任务
      → CCBridge.execute(task, context)
        → 动态生成 prompt（注入记忆+上下文+安全约束）
        → 启动 claude.exe 子进程
        → 实时监控 stdout 进度
        → 检测完成标记 (ALL DONE / BLOCKED)
        → 解析结构化 report
        → 执行结果存入记忆系统
        → 通知用户

集成点:
    - assistant_judge.py: JudgeScheduler 路由 complex 任务
    - assistant_memory_extractor.py: 执行结果反馈闭环
    - assistant_autonomy.py: 常驻调度器调用
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Callable

logger = logging.getLogger("siyi.assistant.cc_bridge")


# ── Public types ───────────────────────────────────────────────────────


class CCStatus(Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    BLOCKED = "blocked"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass(frozen=True)
class CCResult:
    """Result of a CCBridge task execution."""
    task_id: str
    status: CCStatus
    summary: str = ""
    files_created: list[str] = field(default_factory=list)
    files_modified: list[str] = field(default_factory=list)
    commands_run: list[str] = field(default_factory=list)
    output_log: str = ""
    error: str = ""
    duration_seconds: float = 0.0
    started_at: str = ""
    completed_at: str = ""


# ── CCBridge ───────────────────────────────────────────────────────────


class CCBridge:
    """Claude Code CLI bridge.

    Wraps claude.exe as a "super tool" that the Judge can route
    complex tasks to. Manages subprocess lifecycle, output parsing,
    and result persistence.
    """

    # Completion markers from Claude Code
    _MARKER_DONE = "=== ALL DONE ==="
    _MARKER_BLOCKED = "=== BLOCKED ==="
    _PROGRESS_RE = re.compile(r"^\[(\d+)/(\d+)\]", re.MULTILINE)

    # Default CLI path candidates
    _CLI_CANDIDATES = (
        "claude.exe",
        "claude",
        "npx.cmd",
        "npx",
    )

    def __init__(
        self,
        settings_repository=None,
        memory_service=None,
        working_dir: str | Path | None = None,
    ):
        self._settings = settings_repository
        self._memory = memory_service
        self._cli_path: str | None = None
        self._working_dir = Path(working_dir).resolve() if working_dir else Path.cwd()
        self._active_task: _TaskHandle | None = None

        # Resolve Claude Code CLI
        self._cli_path = self._resolve_cli()

    # ── Public API ────────────────────────────────────────────────────

    async def execute(
        self,
        task: str,
        context: dict[str, Any] | None = None,
        *,
        max_turns: int = 100,
        model: str = "sonnet",
        allowed_tools: str = "Read,Write,Edit,Bash,Glob,Grep",
        progress_callback: Callable[[str], None] | None = None,
        timeout: int = 600,
    ) -> CCResult:
        """Execute a task via Claude Code CLI.

        Args:
            task: The task description / prompt for Claude Code.
            context: Optional context dict (memory snippets, user profile, etc.).
            max_turns: Maximum interaction turns for Claude Code.
            model: Claude model to use (sonnet, opus, haiku).
            allowed_tools: Comma-separated list of permitted tools.
            progress_callback: Called with each stdout line for UI updates.
            timeout: Maximum execution time in seconds.

        Returns:
            CCResult with status, summary, files changed, etc.
        """
        if not self._cli_path:
            return CCResult(
                task_id=_gen_task_id(),
                status=CCStatus.FAILED,
                error="Claude Code CLI not found. Install with: npm install -g @anthropic-ai/claude-code",
            )

        # Cancel any previous active task
        self.cancel()

        task_id = _gen_task_id()
        started_at = datetime.now(timezone.utc).isoformat()
        start_time = time.monotonic()

        # Build the prompt with context injection
        prompt = self._build_prompt(task, context or {})

        # Prepare temp files for prompt and output
        prompt_file = Path(tempfile.mktemp(suffix=".md"))
        try:
            prompt_file.write_text(prompt, encoding="utf-8")

            # Build command
            cmd = self._build_command(
                prompt_file=prompt_file,
                max_turns=max_turns,
                model=model,
                allowed_tools=allowed_tools,
            )

            logger.info("CCBridge starting task %s: %s", task_id, task[:120])

            # Execute
            output_lines: list[str] = []
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(self._working_dir),
                env={**os.environ, "CLAUDE_CODE_HEADLESS": "1"},
            )

            self._active_task = _TaskHandle(task_id=task_id, process=process)

            # Read stdout line by line
            files_created: list[str] = []
            files_modified: list[str] = []
            commands_run: list[str] = []
            status = CCStatus.RUNNING

            async def _read_stream(
                stream: asyncio.StreamReader,
                output: list[str],
            ) -> None:
                while True:
                    line = await stream.readline()
                    if not line:
                        break
                    decoded = line.decode("utf-8", errors="replace").rstrip()
                    output.append(decoded)
                    if progress_callback:
                        progress_callback(decoded)
                    # Parse progress
                    if decoded.startswith("created:"):
                        files_created.append(decoded[8:].strip())
                    elif decoded.startswith("modified:"):
                        files_modified.append(decoded[9:].strip())
                    elif decoded.startswith("$ "):
                        commands_run.append(decoded[2:].strip())

            # Read both streams concurrently
            await asyncio.gather(
                _read_stream(process.stdout, output_lines),
                _read_stream(process.stderr, output_lines),
            )

            await process.wait()
            duration = time.monotonic() - start_time

            # Determine status
            full_output = "\n".join(output_lines)
            if process.returncode != 0 and process.returncode is not None:
                status = CCStatus.FAILED
            elif self._MARKER_BLOCKED in full_output:
                status = CCStatus.BLOCKED
            elif self._MARKER_DONE in full_output:
                status = CCStatus.SUCCESS
            else:
                status = CCStatus.SUCCESS if process.returncode == 0 else CCStatus.FAILED

            # Parse summary from output
            summary = self._extract_summary(full_output, task)

            result = CCResult(
                task_id=task_id,
                status=status,
                summary=summary,
                files_created=list(set(files_created)),
                files_modified=list(set(files_modified)),
                commands_run=list(set(commands_run)),
                output_log=full_output[-4096:] if len(full_output) > 4096 else full_output,
                error="" if process.returncode == 0 else f"exit code {process.returncode}",
                duration_seconds=round(duration, 2),
                started_at=started_at,
                completed_at=datetime.now(timezone.utc).isoformat(),
            )

        except asyncio.CancelledError:
            result = CCResult(
                task_id=task_id, status=CCStatus.CANCELLED,
                error="Task was cancelled", started_at=started_at,
                completed_at=datetime.now(timezone.utc).isoformat(),
            )
        except Exception as exc:
            logger.exception("CCBridge task %s failed", task_id)
            result = CCResult(
                task_id=task_id, status=CCStatus.FAILED,
                error=f"{type(exc).__name__}: {exc}",
                started_at=started_at,
                completed_at=datetime.now(timezone.utc).isoformat(),
            )
        finally:
            self._active_task = None
            if prompt_file.exists():
                prompt_file.unlink(missing_ok=True)

        # Persist to memory if available
        if self._memory and result.status in (CCStatus.SUCCESS, CCStatus.BLOCKED):
            await self._persist_to_memory(result, task)

        return result

    def cancel(self) -> bool:
        """Cancel the currently running task, if any."""
        if self._active_task is not None:
            try:
                self._active_task.process.terminate()
                self._active_task = None
                return True
            except Exception:
                logger.exception("failed to terminate cc_bridge task")
        return False

    @property
    def is_running(self) -> bool:
        return self._active_task is not None

    @property
    def is_available(self) -> bool:
        return self._cli_path is not None

    # ── Prompt building ──────────────────────────────────────────────

    def _build_prompt(self, task: str, context: dict[str, Any]) -> str:
        """Build a comprehensive prompt for Claude Code with context injection."""
        parts: list[str] = []

        # System preamble
        parts.append(
            "# Task from SiyiJi (思忆集)\n\n"
            "You are running inside the SiyiJi assistant ecosystem. "
            "Complete the following task according to the project conventions.\n"
        )

        # Context injection
        if context.get("memory_snippets"):
            parts.append("## Relevant Memories\n" + "\n".join(
                f"- {s}" for s in context["memory_snippets"]
            ))

        if context.get("user_profile"):
            profile = context["user_profile"]
            parts.append("## User Context\n" + json.dumps(profile, ensure_ascii=False, indent=2))

        if context.get("files"):
            parts.append("## Relevant Files\n" + "\n".join(f"- {f}" for f in context["files"]))

        if context.get("constraints"):
            parts.append("## Constraints\n" + "\n".join(f"- {c}" for c in context["constraints"]))

        # The actual task
        parts.append("## Task\n" + task)

        # Completion markers
        parts.append(
            "\n---\n"
            "When you have fully completed this task, output exactly:\n"
            f"{self._MARKER_DONE}\n\n"
            "If you cannot complete this task, output exactly:\n"
            f"{self._MARKER_BLOCKED}\n\n"
            "Report a one-line summary of what was done."
        )

        return "\n\n".join(parts)

    # ── Command building ─────────────────────────────────────────────

    def _build_command(
        self,
        prompt_file: Path,
        max_turns: int,
        model: str,
        allowed_tools: str,
    ) -> list[str]:
        """Build the Claude Code CLI command line."""
        cli = self._cli_path or "claude"
        cmd = [cli, "--model", model, "--max-turns", str(max_turns)]

        if allowed_tools:
            cmd.extend(["--allowed-tools", allowed_tools])

        cmd.extend(["--print-system", prompt_file])
        # Use --resume if available, pass prompt via file
        cmd.append(str(prompt_file))
        return cmd

    # ── CLI resolution ───────────────────────────────────────────────

    def _resolve_cli(self) -> str | None:
        """Locate the Claude Code CLI executable."""
        # Check settings first
        if self._settings:
            try:
                path = self._settings.get_setting("cc_bridge_cli_path", "")
                if path and Path(path).is_file():
                    return str(Path(path).resolve())
            except Exception:
                logger.debug("cc_bridge_cli_path setting read failed", exc_info=True)

        # Search PATH
        for candidate in self._CLI_CANDIDATES:
            found = shutil.which(candidate)
            if found:
                return found

        # Common install locations
        extra_paths = [
            Path(os.environ.get("LOCALAPPDATA", "")) / "npm" / "claude.exe",
            Path(os.environ.get("APPDATA", "")) / "npm" / "claude.exe",
            Path.home() / "AppData" / "Roaming" / "npm" / "claude.exe",
            Path.home() / ".npm" / "bin" / "claude",
        ]
        for p in extra_paths:
            if p.is_file():
                return str(p.resolve())

        return None

    # ── Result parsing ───────────────────────────────────────────────

    @staticmethod
    def _extract_summary(output: str, task: str) -> str:
        """Extract a concise summary from Claude Code's output."""
        lines = output.split("\n")

        # Look for summary patterns
        for line in reversed(lines):
            stripped = line.strip()
            if stripped.startswith(("Summary:", "summary:", "Done:")):
                return stripped.split(":", 1)[1].strip()[:500]

        # Fallback: last meaningful line before marker
        for line in reversed(lines):
            stripped = line.strip()
            if stripped and not stripped.startswith(("===", "[", "$", "created:", "modified:")):
                return stripped[:300]

        return task[:200] + "..." if len(task) > 200 else task

    # ── Memory persistence ───────────────────────────────────────────

    async def _persist_to_memory(self, result: CCResult, task: str) -> None:
        """Store execution results into the memory system."""
        try:
            if hasattr(self._memory, "store_execution_record"):
                await self._memory.store_execution_record(
                    source="cc_bridge",
                    task=task[:500],
                    summary=result.summary,
                    status=result.status.value,
                    files_created=result.files_created,
                    files_modified=result.files_modified,
                    duration_seconds=result.duration_seconds,
                )
        except Exception as exc:
            logger.warning("Failed to persist CCBridge result to memory: %s", exc)


# ── Internal helpers ───────────────────────────────────────────────────


@dataclass
class _TaskHandle:
    task_id: str
    process: asyncio.subprocess.Process


def _gen_task_id() -> str:
    import uuid
    return f"cc_{uuid.uuid4().hex[:12]}"
