from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import urlopen

from core.action_guard import ActionGuard
from core.observability import SEVERITY_DEGRADED
from core.settings_keys import (
    KEY_ASSISTANT_AUTO_PREPARE_DOCKER,
    KEY_ASSISTANT_DOCKER_STACK_PORT,
    KEY_AUTOMATION_HUB_API_BASE,
    KEY_LLM_API_BASE,
    KEY_LLM_API_KEY,
    KEY_LLM_MODEL,
)

logger = logging.getLogger("siyi.assistant_runtime")


class AssistantRuntimeManager:
    """Runtime context for assistant execution."""

    DEFAULT_LOCAL_API_PORT = 8001
    DEFAULT_DOCKER_API_PORT = 8010
    HEALTH_POLL_SECONDS = 18
    HEALTH_POLL_INTERVAL = 1.5

    def __init__(self, settings_repository=None, observability_service=None):
        self.settings_repository = settings_repository
        self.observability_service = observability_service
        self._command_guard = ActionGuard(settings_repository=settings_repository, observability_service=observability_service)
        self.app_dir = Path(__file__).resolve().parent
        self.workspace_dir = self.app_dir.parent
        self.automation_hub_dir = self.workspace_dir / "automation-hub"
        self.docker_stack_script = self.automation_hub_dir / "scripts" / "docker_local_stack.ps1"
        self._last_status = {
            "summary": "助手后台服务尚未鍑嗗?。?",
            "automation_hub": "unknown",
            "docker_stack": "unknown",
            "automation_hub_base": None,
            "docker_stack_base": f"http://127.0.0.1:{self.DEFAULT_DOCKER_API_PORT}",
            "details": [],
        }

    def prepare_runtime(self) -> dict:
        details: list[str] = []
        automation_hub_state = "skipped"
        docker_state = "skipped"

        base_url = self._get_setting(KEY_AUTOMATION_HUB_API_BASE, f"http://127.0.0.1:{self.DEFAULT_LOCAL_API_PORT}")
        if self._is_local_http_url(base_url):
            if self._probe_health(base_url):
                automation_hub_state = "ready"
                details.append(f"automation-hub 已就绪：{base_url}")
            else:
                started = self._start_local_agent_backend(base_url)
                if started and self._wait_until_healthy(base_url):
                    automation_hub_state = "started"
                    details.append(f"已在后台启动 automation-hub：{base_url}")
                elif started:
                    automation_hub_state = "starting"
                    details.append(f"automation-hub 已发起启动：{base_url}")
                else:
                    automation_hub_state = "error"
                    details.append(f"automation-hub 启动失败：{base_url}")
        else:
            details.append("automation-hub API Base 不是本地地址，跳过本地自动拉起。")

        docker_base = f"http://127.0.0.1:{self._get_setting(KEY_ASSISTANT_DOCKER_STACK_PORT, self.DEFAULT_DOCKER_API_PORT)}"
        if self._get_setting(KEY_ASSISTANT_AUTO_PREPARE_DOCKER, True):
            if self._probe_health(docker_base):
                docker_state = "ready"
                details.append(f"Docker 准备栈已就绪：{docker_base}")
            else:
                start_result = self._start_docker_stack(docker_base)
                docker_state = start_result["state"]
                details.append(start_result["message"])
        else:
            details.append("已关闭 Docker 准备栈自动拉起。")

        summary = "、".join(details) if details else "助手后台服务检查完成。"
        self._last_status = {
            "summary": summary,
            "automation_hub": automation_hub_state,
            "docker_stack": docker_state,
            "automation_hub_base": base_url,
            "docker_stack_base": docker_base,
            "details": details,
        }
        return dict(self._last_status)

    def get_status_snapshot(self) -> dict:
        return dict(self._last_status)

    def _get_setting(self, key: str, default=None):
        if self.settings_repository is None:
            return default
        return self.settings_repository.get_setting(key, default)

    def _is_local_http_url(self, base_url: str | None) -> bool:
        if not isinstance(base_url, str) or not base_url.strip():
            return False
        parsed = urlparse(base_url.strip())
        return parsed.scheme in {"http", "https"} and parsed.hostname in {"127.0.0.1", "localhost"}

    def _probe_health(self, base_url: str, timeout: float = 2.0) -> bool:
        try:
            with urlopen(f"{base_url.rstrip('/')}/health", timeout=timeout) as response:
                return int(getattr(response, "status", 0) or 0) == 200
        except (HTTPError, URLError, TimeoutError, OSError, ValueError):
            return False

    def _wait_until_healthy(self, base_url: str) -> bool:
        deadline = time.time() + self.HEALTH_POLL_SECONDS
        while time.time() < deadline:
            if self._probe_health(base_url):
                return True
            time.sleep(self.HEALTH_POLL_INTERVAL)
        return False

    def _build_llm_env(self) -> dict[str, str]:
        api_key = str(self._get_setting(KEY_LLM_API_KEY, "") or "").strip()
        base_url = str(self._get_setting(KEY_LLM_API_BASE, "") or "").strip()
        model = str(self._get_setting(KEY_LLM_MODEL, "") or "").strip()
        env: dict[str, str] = {"AGENT_ENABLED": "true"}
        if api_key:
            env["OPENAI_API_KEY"] = api_key
        if base_url:
            env["OPENAI_BASE_URL"] = base_url.rstrip("/")
        if model:
            env["OPENAI_MODEL"] = model
        return env

    def _start_local_agent_backend(self, base_url: str) -> bool:
        if not self.automation_hub_dir.exists():
            logger.warning("automation-hub 鐩?綍不存鍦? %s", self.automation_hub_dir)
            return False

        parsed = urlparse(base_url.strip())
        port = parsed.port or self.DEFAULT_LOCAL_API_PORT
        env = os.environ.copy()
        env.update(self._build_llm_env())

        command = [
            sys.executable,
            "-m",
            "uvicorn",
            "api.main:app",
            "--host",
            "0.0.0.0",
            "--port",
            str(port),
        ]
        if not self._allow_command(
            "start_local_agent_backend",
            payload={"command": "uvicorn api.main:app", "source": "assistant_runtime"},
        ):
            return False
        return self._spawn_detached(command, cwd=str(self.automation_hub_dir), env=env)

    def _start_docker_stack(self, docker_base: str) -> dict:
        if not self.docker_stack_script.exists():
            return {"state": "missing", "message": "未找到Docker准备脚本，跳过自动拉起。"}

        docker_exe = self._resolve_docker_executable()
        if not docker_exe:
            return {"state": "missing", "message": "本机未检测到docker，可先安装或启动Docker Desktop。"}
        if not self._docker_daemon_ready(docker_exe):
            return {"state": "unavailable", "message": "Docker CLI 已安装，但Docker Desktop / daemon 当前不可用。"}

        parsed = urlparse(docker_base)
        api_port = str(parsed.port or self.DEFAULT_DOCKER_API_PORT)
        env = os.environ.copy()
        env.update(self._build_llm_env())
        powershell = shutil.which("powershell") or shutil.which("pwsh") or "powershell"
        command = [
            powershell,
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(self.docker_stack_script),
            "-Action",
            "up",
            "-ApiPort",
            api_port,
        ]

        if not self._allow_command(
            "start_docker_stack",
            payload={"command": "powershell docker_local_stack.ps1 -Action up", "source": "assistant_runtime"},
        ):
            return {"state": "blocked", "message": "当前安全策略阻止了Docker准备栈启动命令。"}

        if not self._spawn_detached(command, cwd=str(self.automation_hub_dir), env=env):
            return {"state": "error", "message": "Docker准备栈启动命令发起失败。"}

        if self._wait_until_healthy(docker_base):
            return {"state": "started", "message": f"已在后台拉起 Docker 鍑嗗?栈：{docker_base}"}
        return {"state": "starting", "message": f"已发璧?Docker 鍑嗗?栈启鍔?細{docker_base}"}

    def _resolve_docker_executable(self) -> str | None:
        docker_exe = shutil.which("docker")
        if docker_exe and Path(docker_exe).exists():
            return docker_exe
        fallback = Path(r"C:\Program Files\Docker\Docker\resources\bin\docker.exe")
        return str(fallback) if fallback.exists() else None

    def _docker_daemon_ready(self, docker_exe: str) -> bool:
        if not self._allow_command(
            "docker_version_check",
            payload={"command": "docker version --format {{json .Server}}", "source": "assistant_runtime"},
        ):
            return False
        try:
            result = subprocess.run(
                [docker_exe, "version", "--format", "{{json .Server}}"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                stdin=subprocess.DEVNULL,
                timeout=6,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
            )
            return result.returncode == 0 and bool((result.stdout or "").strip())
        except Exception:
            return False

    def _allow_command(self, command_id: str, payload: dict | None = None) -> bool:
        check_result = self._command_guard.validate_command(
            command_id=command_id,
            payload=payload or {},
            source="assistant_runtime.command",
        )
        if check_result.allowed:
            return True

        logger.warning("runtime command blocked: id=%s reason=%s risk=%s", command_id, check_result.reason, check_result.risk_level)
        self._track_event(
            "assistant.runtime.command.blocked",
            {
                "command_id": command_id,
                "reason": check_result.reason,
                "risk_level": check_result.risk_level,
            },
        )
        return False

    def _track_event(self, event_name: str, payload: dict) -> None:
        service = self.observability_service
        if service is None:
            return
        try:
            service.track_event(event_name, severity=SEVERITY_DEGRADED, payload=payload)
        except Exception:
            return

    def _spawn_detached(self, command: list[str], cwd: str, env: dict[str, str]) -> bool:
        creation_flags = 0
        creation_flags |= getattr(subprocess, "DETACHED_PROCESS", 0)
        creation_flags |= getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        try:
            subprocess.Popen(
                command,
                cwd=cwd,
                env=env,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                stdin=subprocess.DEVNULL,
                creationflags=creation_flags,
                close_fds=False,
            )
            return True
        except Exception as exc:
            logger.warning("后台鍚?姩失败: command=%s error=%s", command, exc)
            return False
