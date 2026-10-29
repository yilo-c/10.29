"""Docker 执行器。

在 Docker 容器中执行命令（推荐用于生产环境）。
"""

from __future__ import annotations
import subprocess
import os
import shutil
import stat
from pathlib import Path

from automation_hub.api.config import settings
from .base import Executor


class DockerExecutor(Executor):
    """在 Docker 容器中执行命令的执行器。
    
    提供更好的隔离性和安全性。
    
    适用于：
    - 所有生产环境工具
    - 高风险操作
    - 需要特定依赖的工具
    
    TODO: 当前使用 docker CLI，未来可升级为 Docker SDK for Python。
    """
    
    def __init__(self, image: str | None = None):
        """初始化 Docker 执行器。
        
        Args:
            image: Docker 镜像名称
        """
        self.image = image or settings.DOCKER_EXECUTOR_IMAGE

    def _map_cwd_to_container(self, cwd: str | None, workspace_host: Path) -> str:
        if not cwd:
            return "/workspace"
        try:
            path_obj = Path(cwd)
            if path_obj.is_absolute():
                try:
                    rel = path_obj.resolve().relative_to(workspace_host.resolve())
                    return str(Path("/workspace") / rel).replace("\\", "/")
                except Exception:
                    return "/workspace"
            return str(Path("/workspace") / path_obj).replace("\\", "/")
        except Exception:
            return "/workspace"

    def run(
        self,
        command: list[str],
        cwd: str | None,
        env: dict[str, str],
        timeout: int,
        stdout_path: str,
        stderr_path: str
    ) -> int:
        """在 Docker 容器中执行命令。"""
        with open(stdout_path, "w", encoding="utf-8") as out_f, \
             open(stderr_path, "w", encoding="utf-8") as err_f:
            try:
                if not settings.DOCKER_EXECUTOR_ALLOW_HOST_FALLBACK:
                    ok, msg = _preflight(self.image)
                    if not ok:
                        err_f.write(f"\nERROR: docker preflight failed: {msg}\n")
                        return 125

                workspace_host = Path(str(settings.WORKSPACE_DIR)).resolve()
                data_host = Path(str(settings.DATA_DIR)).resolve()

                workspace_host.mkdir(parents=True, exist_ok=True)
                data_host.mkdir(parents=True, exist_ok=True)

                container_cwd = self._map_cwd_to_container(cwd, workspace_host)

                docker_cmd: list[str] = [
                    "docker",
                    "run",
                    "--rm",
                    "--workdir",
                    container_cwd,
                    "--network",
                    settings.DOCKER_EXECUTOR_NETWORK,
                    "-v",
                    f"{workspace_host}:/workspace",
                    "-v",
                    f"{data_host}:/data",
                ]

                pass_env_keys = ["WORKSPACE", "DATA_DIR", "RUN_ID", "TOOL_ID"]
                for key in list(env.keys()):
                    if key.startswith("ARG_"):
                        pass_env_keys.append(key)

                for key in pass_env_keys:
                    value = env.get(key)
                    if value is None:
                        continue
                    docker_cmd.extend(["-e", f"{key}={value}"])

                docker_cmd.append(self.image)
                docker_cmd.extend(command)

                try:
                    proc = subprocess.run(
                        docker_cmd,
                        timeout=timeout,
                        stdout=out_f,
                        stderr=err_f,
                        text=True,
                        check=False,
                    )
                    return proc.returncode
                except FileNotFoundError:
                    err_f.write("\nERROR: docker CLI not found\n")
                    if not settings.DOCKER_EXECUTOR_ALLOW_HOST_FALLBACK:
                        return 127

                if settings.DOCKER_EXECUTOR_ALLOW_HOST_FALLBACK:
                    err_f.write("\nWARN: docker execution failed; falling back to host execution\n")
                    proc2 = subprocess.run(
                        command,
                        cwd=cwd,
                        env=env,
                        timeout=timeout,
                        stdout=out_f,
                        stderr=err_f,
                        text=True,
                        check=False,
                    )
                    return proc2.returncode
                return 1
            except subprocess.TimeoutExpired:
                err_f.write(f"\nERROR: Command timed out after {timeout} seconds\n")
                return 124
            except Exception as e:
                err_f.write(f"\nERROR: {str(e)}\n")
                return 1


_PREFLIGHT_CACHE: dict[str, tuple[bool, str]] = {}


def _cache_key(image: str) -> str:
    sock = getattr(settings, "DOCKER_EXECUTOR_SOCK_PATH", "/var/run/docker.sock")
    pull = bool(getattr(settings, "DOCKER_EXECUTOR_PREFLIGHT_PULL", True))
    return f"{image}::{sock}::pull={int(pull)}"


def _is_socket(path: str) -> bool:
    try:
        st = os.stat(path)
        return stat.S_ISSOCK(st.st_mode)
    except Exception:
        return False


def _preflight(image: str) -> tuple[bool, str]:
    key = _cache_key(image)
    cached = _PREFLIGHT_CACHE.get(key)
    if cached is not None:
        return cached

    timeout = int(getattr(settings, "DOCKER_EXECUTOR_PREFLIGHT_TIMEOUT_SECONDS", 5) or 5)
    sock = str(getattr(settings, "DOCKER_EXECUTOR_SOCK_PATH", "/var/run/docker.sock"))

    if not shutil.which("docker"):
        msg = "docker CLI not found in PATH"
        _PREFLIGHT_CACHE[key] = (False, msg)
        return False, msg

    if not os.path.exists(sock) or not _is_socket(sock):
        msg = f"docker socket not available: {sock}"
        _PREFLIGHT_CACHE[key] = (False, msg)
        return False, msg

    try:
        res = subprocess.run(
            ["docker", "version"],
            timeout=timeout,
            capture_output=True,
            text=True,
            check=False,
        )
        if res.returncode != 0:
            msg = (res.stderr or res.stdout or "docker version failed").strip()
            _PREFLIGHT_CACHE[key] = (False, msg)
            return False, msg
    except subprocess.TimeoutExpired:
        msg = "docker version timed out"
        _PREFLIGHT_CACHE[key] = (False, msg)
        return False, msg
    except Exception as e:
        msg = f"docker version error: {str(e)}"
        _PREFLIGHT_CACHE[key] = (False, msg)
        return False, msg

    # 镜像可用性：优先 inspect，缺失则 pull（可禁用）
    try:
        inspect = subprocess.run(
            ["docker", "image", "inspect", image],
            timeout=max(timeout, 10),
            capture_output=True,
            text=True,
            check=False,
        )
        if inspect.returncode != 0:
            if bool(getattr(settings, "DOCKER_EXECUTOR_PREFLIGHT_PULL", True)):
                pull = subprocess.run(
                    ["docker", "pull", image],
                    timeout=300,
                    capture_output=True,
                    text=True,
                    check=False,
                )
                if pull.returncode != 0:
                    msg = (pull.stderr or pull.stdout or "docker pull failed").strip()
                    _PREFLIGHT_CACHE[key] = (False, msg)
                    return False, msg
            else:
                msg = (inspect.stderr or inspect.stdout or "image not present").strip()
                _PREFLIGHT_CACHE[key] = (False, msg)
                return False, msg
    except subprocess.TimeoutExpired:
        msg = "docker image inspect/pull timed out"
        _PREFLIGHT_CACHE[key] = (False, msg)
        return False, msg
    except Exception as e:
        msg = f"docker image check error: {str(e)}"
        _PREFLIGHT_CACHE[key] = (False, msg)
        return False, msg

    _PREFLIGHT_CACHE[key] = (True, "ok")
    return True, "ok"
