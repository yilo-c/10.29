"""Mobile bridge manager — ensures automation-hub is LAN-accessible for phone PWA."""

from __future__ import annotations

import logging
import socket
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger("automation_hub.mobile_bridge")


def _loads(text: str):
    import json
    return json.loads(text)


def _legacy_pairing_request(port: int, path: str, method: str, data: bytes | None) -> dict | None:
    """回退路径：直接向本地 hub 发起匿名请求（不依赖 token 配置）。"""
    import urllib.request
    base = f"http://127.0.0.1:{port}"
    req = urllib.request.Request(
        f"{base}{path}",
        method=method,
        data=data,
        headers={"Content-Type": "application/json"} if data is not None else {},
    )
    with urllib.request.urlopen(req, timeout=5) as resp:
        raw = resp.read().decode("utf-8")
    if not raw:
        return None
    try:
        return _loads(raw)
    except Exception:
        return None


@dataclass
class BridgeState:
    running: bool = False
    port: int = 8000
    local_ip: str = ""
    pid: int = 0
    last_error: str = ""


class MobileBridgeManager:
    """Manages the automation-hub server lifecycle for mobile LAN access."""

    def __init__(self, settings_repository=None, app_context=None):
        self._settings = settings_repository
        self._app_context = app_context
        self._process: subprocess.Popen | None = None
        self._state = BridgeState()
        self._hub_base_url: str = ""

    @property
    def state(self) -> BridgeState:
        return self._state

    def get_local_ip(self) -> str:
        """Get the LAN IP address for QR code generation."""
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("10.255.255.255", 1))
            ip = s.getsockname()[0]
            s.close()
            return ip
        except Exception:
            logger.warning("get_local_ip failed, falling back to 127.0.0.1", exc_info=True)
            return "127.0.0.1"

    def get_mobile_url(self) -> str:
        """Get the full mobile PWA URL for phone access."""
        ip = self._state.local_ip or self.get_local_ip()
        return f"http://{ip}:{self._state.port}/mobile/"

    def start(self) -> bool:
        """Start automation-hub with LAN binding."""
        if self._state.running:
            return True

        if self._settings:
            port = self._settings.get_setting("mobile_automation_hub_port", 8000)
        else:
            port = 8000

        # Find the automation-hub directory
        hub_dir = self._find_hub_dir()
        if not hub_dir:
            self._state.last_error = "automation-hub 目录未找到"
            logger.error(self._state.last_error)
            return False

        try:
            self._process = subprocess.Popen(
                [sys.executable, "-m", "uvicorn", "api.main:app",
                 "--host", "0.0.0.0", "--port", str(port)],
                cwd=str(hub_dir),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
            )
            # Wait for startup (poll with short sleeps to avoid long UI freeze)
            import time as _time
            _deadline = _time.monotonic() + 5.0
            while _time.monotonic() < _deadline:
                if self._process.poll() is not None:
                    break
                _time.sleep(0.1)
            if self._process.poll() is not None:
                stderr = self._process.stderr.read() if self._process.stderr else ""
                self._state.last_error = f"启动失败: {stderr.decode('utf-8', errors='replace')[:200]}"
                logger.error(self._state.last_error)
                self._process = None
                return False

            self._state.running = True
            self._state.port = port
            self._state.local_ip = self.get_local_ip()
            self._state.pid = self._process.pid
            self._state.last_error = ""
            self._hub_base_url = f"http://127.0.0.1:{port}"
            logger.info("Mobile bridge started on 0.0.0.0:%d (PID %d)", port, self._process.pid)
            return True

        except Exception as exc:
            self._state.last_error = str(exc)
            logger.exception("Failed to start mobile bridge")
            return False

    def stop(self) -> None:
        """Stop the automation-hub process."""
        if self._process:
            try:
                self._process.terminate()
                self._process.wait(timeout=5)
            except Exception:
                try:
                    self._process.kill()
                except Exception:
                    logger.debug("Non-critical error (see details)", exc_info=True)

            self._process = None
        self._state.running = False
        self._state.pid = 0
        logger.info("Mobile bridge stopped")

    def is_healthy(self) -> bool:
        """Check if the automation-hub is responding."""
        if not self._state.running:
            return False
        try:
            import urllib.request
            req = urllib.request.Request(f"http://127.0.0.1:{self._state.port}/health")
            with urllib.request.urlopen(req, timeout=3) as resp:
                return resp.status == 200
        except Exception:
            logger.warning("mobile bridge health check failed", exc_info=True)
            return False

    def _hub_client(self):
        """构造指向本地 automation-hub 的客户端，复用 HTTP/token/session 逻辑。"""
        try:
            from integrations.automation_hub_client import create_automation_hub_client
            return create_automation_hub_client(
                settings_repository=self._settings,
                base_url=self._hub_base_url or f"http://127.0.0.1:{self._state.port}",
            )
        except Exception:
            return None

    def initiate_pairing(self) -> dict[str, str]:
        """POST /mobile/pairing/initiate, return {setup_token, pin_code, ...}."""
        client = self._hub_client()
        if client is None:
            return _legacy_pairing_request(self._state.port, path="/mobile/pairing/initiate", method="POST", data=b"{}")
        try:
            raw = client.read("POST", "/mobile/pairing/initiate", json_body={}, timeout=5)
        except Exception:
            return _legacy_pairing_request(self._state.port, path="/mobile/pairing/initiate", method="POST", data=b"{}")
        return raw if isinstance(raw, dict) else {}

    def poll_pairing_status(self) -> dict | None:
        """GET /mobile/pairing/status, return data or None on error."""
        client = self._hub_client()
        if client is not None:
            try:
                raw = client.read("GET", "/mobile/pairing/status", timeout=3)
                return raw if isinstance(raw, dict) else None
            except Exception:
                logger.warning("mobile bridge poll_pairing_status failed", exc_info=True)
                return None
        return _legacy_pairing_request(self._state.port, path="/mobile/pairing/status", method="GET", data=None)

    def _find_hub_dir(self) -> Path | None:
        """Locate the automation-hub directory relative to the app."""
        candidates = [
            Path(__file__).resolve().parent.parent.parent / "automation_hub",
            Path(__file__).resolve().parent.parent / "automation_hub",
            Path(__file__).resolve().parent / "automation_hub",
            Path.cwd() / "automation_hub",
        ]
        for path in candidates:
            if (path / "api" / "main.py").exists():
                return path
        return None
