from __future__ import annotations

import os
import shlex
import shutil
import subprocess
from pathlib import Path
from typing import Any

from core.subprocess_text import run_text


class ShellExecutionModel:
    """统一 shell.run 的构建、环境净化与工作区边界约束。"""

    _CMD_BUILTINS = {
        "assoc",
        "break",
        "call",
        "cd",
        "chdir",
        "cls",
        "copy",
        "date",
        "del",
        "dir",
        "echo",
        "endlocal",
        "erase",
        "for",
        "ftype",
        "md",
        "mkdir",
        "mklink",
        "move",
        "path",
        "pause",
        "popd",
        "prompt",
        "pushd",
        "rd",
        "ren",
        "rename",
        "rmdir",
        "set",
        "setlocal",
        "shift",
        "start",
        "time",
        "title",
        "type",
        "ver",
        "verify",
        "vol",
    }
    _WINDOWS_WRAPPER_SUFFIXES = {".cmd", ".bat"}
    _ENV_DENYLIST = {
        "BASH_ENV",
        "CDPATH",
        "DYLD_INSERT_LIBRARIES",
        "ENV",
        "GIT_DIR",
        "GIT_INDEX_FILE",
        "GIT_WORK_TREE",
        "IFS",
        "LD_PRELOAD",
        "NODE_OPTIONS",
        "PROMPT_COMMAND",
        "PYTHONHOME",
        "PYTHONPATH",
        "SHELLOPTS",
        "ZDOTDIR",
    }
    _ENV_ALLOWLIST = {"FORCE_COLOR", "LANG", "LC_ALL", "LC_CTYPE", "NO_COLOR", "TERM"}
    _ENV_ALLOW_PREFIXES = ("LC_",)

    def __init__(self, *, workspace_root: Path | None = None):
        self.workspace_root = workspace_root.resolve(strict=False) if workspace_root is not None else None

    def build_command(self, command: str, args: Any = None) -> list[str]:
        raw_command = str(command or "").strip()
        if not raw_command:
            raise ValueError("empty_shell_command")

        if isinstance(args, list) and args:
            raw_parts = [raw_command] + [str(item).strip() for item in args if str(item).strip()]
        else:
            raw_parts = shlex.split(raw_command, posix=os.name != "nt")
        if not raw_parts:
            raise ValueError("empty_shell_command")

        executable = str(raw_parts[0]).strip().lower()
        if os.name == "nt" and executable in self._CMD_BUILTINS:
            command_line = subprocess.list2cmdline(raw_parts)
            return ["cmd.exe", "/d", "/s", "/c", command_line]

        resolved_executable = self._resolve_executable(raw_parts[0])
        return [resolved_executable, *raw_parts[1:]]

    def resolve_working_dir(self, working_dir: str | None = None) -> str | None:
        base_dir = self.workspace_root
        if base_dir is None and not working_dir:
            return None
        if not working_dir:
            return str(base_dir) if base_dir is not None else None

        candidate = Path(str(working_dir).strip()).expanduser()
        resolved = candidate.resolve(strict=False) if candidate.is_absolute() else ((base_dir or Path.cwd()) / candidate).resolve(strict=False)
        if self.workspace_root is not None and not resolved.is_relative_to(self.workspace_root):
            raise ValueError("working_dir_outside_workspace")
        return str(resolved)

    def build_environment(self, extra_env: dict[str, Any] | None = None) -> dict[str, str]:
        env = {key: value for key, value in os.environ.items() if key not in self._ENV_DENYLIST}
        if not isinstance(extra_env, dict):
            return env
        for key, value in extra_env.items():
            normalized_key = str(key or "").strip()
            if not normalized_key or value is None:
                continue
            if normalized_key in self._ENV_ALLOWLIST or normalized_key.startswith(self._ENV_ALLOW_PREFIXES):
                env[normalized_key] = str(value)
        return env

    def execute(
        self,
        *,
        command: str,
        args: Any = None,
        timeout: int = 15,
        working_dir: str | None = None,
        env: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        cmd_parts = self.build_command(command, args=args)
        resolved_cwd = self.resolve_working_dir(working_dir)
        self._enforce_workspace_boundary(cmd_parts, resolved_cwd)
        try:
            result = run_text(cmd_parts, shell=False, timeout=timeout, cwd=resolved_cwd, env=self.build_environment(env))
        except subprocess.TimeoutExpired as exc:
            raise TimeoutError(f"shell command timed out after {timeout}s: {exc.cmd}") from exc
        return {
            "success": result.returncode == 0,
            "returncode": result.returncode,
            "stdout": result.stdout[:16384],
            "stderr": result.stderr[:4096],
            "resolved_command": cmd_parts,
            "cwd": resolved_cwd or "",
        }

    def _resolve_executable(self, executable: str) -> str:
        raw_executable = str(executable or "").strip()
        if not raw_executable:
            raise ValueError("empty_shell_command")

        if Path(raw_executable).is_absolute() or any(sep in raw_executable for sep in ("/", "\\")):
            resolved = Path(raw_executable).expanduser().resolve(strict=False)
        else:
            located = shutil.which(raw_executable)
            if not located:
                raise FileNotFoundError(raw_executable)
            resolved = Path(located).resolve(strict=False)

        if os.name == "nt" and resolved.suffix.lower() in self._WINDOWS_WRAPPER_SUFFIXES:
            raise ValueError("windows_cmd_wrapper_blocked")
        return str(resolved)

    def _enforce_workspace_boundary(self, cmd_parts: list[str], resolved_cwd: str | None) -> None:
        if self.workspace_root is None:
            return
        allowed_roots = [self.workspace_root]
        if resolved_cwd:
            allowed_roots.append(Path(resolved_cwd).resolve(strict=False))
        for token in cmd_parts[1:]:
            candidate = Path(str(token or "").strip().strip('"'))
            if not candidate.is_absolute():
                continue
            resolved = candidate.resolve(strict=False)
            if not any(resolved.is_relative_to(root) for root in allowed_roots):
                raise ValueError("path_outside_workspace")
