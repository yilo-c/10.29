"""Shell execution and app launch capability domain.

Extracted from capability_router.py — ShellCapabilityDomain.
"""

from __future__ import annotations

import logging
import os
import subprocess
from datetime import datetime, timezone
from typing import Any

from core.action_guard import SHELL_DANGEROUS_COMMAND_PATTERNS, SHELL_UNSAFE_TOKENS
from services.app_launch import resolve_app_launch_allowlist  # 稳定区（默认集 + settings 追加项）

logger = logging.getLogger("siyi.assistant.capability.shell")


class ShellCapabilityDomain:
    """Shell execution and app launch capabilities."""

    def __init__(self, router) -> None:
        self.router = router
        self._UNSAFE_SHELL_TOKENS = SHELL_UNSAFE_TOKENS
        self._CMD_BUILTINS = {
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
        self._DANGEROUS_COMMAND_PATTERNS = SHELL_DANGEROUS_COMMAND_PATTERNS

    def shell_run(self, payload: dict[str, Any]) -> dict[str, Any]:
        command = str(payload.get("command") or "").strip()
        if not command:
            return {"success": False, "reason": "no_command"}
        shell_check = self.router.action_guard.validate_shell_command(command, payload=payload, source=str(payload.get("source") or "assistant"))
        if not shell_check.allowed:
            return {"success": False, "reason": shell_check.reason}
        timeout = max(3, min(int(payload.get("timeout") or 15), 60))
        try:
            return self.router.shell_execution.execute(
                command=command,
                args=payload.get("args"),
                timeout=timeout,
                working_dir=payload.get("working_dir") or payload.get("cwd"),
                env=payload.get("env"),
            )
        except subprocess.TimeoutExpired:
            return {"success": False, "reason": f"timeout>{timeout}s"}
        except FileNotFoundError:
            return {"success": False, "reason": "executable_not_found"}
        except ValueError as exc:
            return {"success": False, "reason": str(exc)}
        except Exception as exc:
            return {"success": False, "reason": str(exc)}

    def build_shell_command(self, command: str, args: Any = None) -> list[str]:
        return self.router.shell_execution.build_command(command, args=args)

    def app_launch(self, payload: dict[str, Any]) -> dict[str, Any]:
        command = str(payload.get("command") or "").strip()
        if not command:
            return {"launched": False, "reason": "no_command"}

        app_key = os.path.basename(command).rsplit(".", 1)[0].lower()
        # B1 合并语义（2026-09-11）：生效名单 = 代码默认集 ∪ settings 追加项。
        # 此前为纯硬编码 frozenset，用户想启动名单外应用无任何途径。
        allowlist = resolve_app_launch_allowlist(getattr(self.router, "settings_repository", None))
        if app_key not in allowlist:
            logger.warning("app launch blocked: %s (not in allowlist)", command)
            return {"launched": False, "reason": f"blocked: {command} is not in the allowed applications list"}

        args = payload.get("args") or []
        if isinstance(args, str):
            args = [args]
        resolved = self.resolve_app_executable(command)
        exe = resolved or command
        if isinstance(exe, str) and exe.lower().endswith(".lnk"):
            try:
                os.startfile(exe)
                return {"launched": True, "command": exe, "via": "start_menu_shortcut", "resolved": True}
            except Exception as exc:
                return {"launched": False, "reason": str(exc)}
        cmd_parts = [exe] + [str(a) for a in args]
        try:
            proc = subprocess.Popen(cmd_parts, shell=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            proc_id = f"app_{os.path.basename(exe)}_{datetime.now(timezone.utc).strftime('%H%M%S%f')}"
            self.router._launched_processes[proc_id] = proc
            return {"launched": True, "command": exe, "args": args, "resolved": resolved is not None}
        except FileNotFoundError:
            try:
                os.startfile(exe)
                return {"launched": True, "command": exe, "via": "startfile"}
            except Exception:
                logger.warning("startfile fallback failed for %s", exe, exc_info=True)
            if resolved and exe != command:
                try:
                    os.startfile(command)
                    return {"launched": True, "command": command, "via": "startfile_fallback"}
                except Exception as exc2:
                    return {"launched": False, "reason": f"not_found: {exc2}"}
            return {"launched": False, "reason": f"not_found: {exe}"}
        except Exception as exc:
            return {"launched": False, "reason": str(exc)}

    def resolve_app_executable(self, app_name: str) -> str | None:
        return self.router.environment_probe.resolve_app_executable(app_name)

    def clipboard_read(self, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            # P1-A（2026-09-18）：裸 text=True 在 PYTHONUTF8=1 下遇 GBK 输出会
            # 静默把 stdout 置 None → 原事故点（Get-Clipboard 恒炸）。改走解码基元。
            from core.subprocess_text import run_text

            result = run_text(["powershell", "-NoProfile", "-Command", "Get-Clipboard"], timeout=5, check=False)
            if result.returncode == 0:
                return {"success": True, "content": result.stdout.strip()}
            return {"success": False, "reason": "clipboard_read_failed", "error": result.stderr.strip()}
        except Exception as exc:
            return {"success": False, "reason": str(exc)}

    def clipboard_write(self, payload: dict[str, Any]) -> dict[str, Any]:
        text = str(payload.get("text") or "")
        if not text:
            return {"success": False, "reason": "no_text"}
        try:
            # P1-A：stdin 走字节（utf-8 编码），PowerShell 侧显式对齐 InputEncoding，
            # 避免 PYTHONUTF8 下 GBK 误解；输出经回退解码基元。取值语义保持原样。
            proc = subprocess.Popen(
                [
                    "powershell",
                    "-NoProfile",
                    "-Command",
                    "[Console]::InputEncoding=[Text.Encoding]::UTF8; Set-Clipboard -Value $input",
                ],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            _, stderr_raw = proc.communicate(input=text.encode("utf-8"), timeout=5)
            from core.subprocess_text import decode_output

            stderr = decode_output(stderr_raw)
            if proc.returncode == 0:
                return {"success": True, "length": len(text)}
            return {"success": False, "reason": "clipboard_write_failed", "error": stderr.strip()}
        except Exception as exc:
            return {"success": False, "reason": str(exc)}
