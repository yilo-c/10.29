"""Trae IDE capability domain backed by :mod:`integrations.trae_bridge`."""

from __future__ import annotations

import asyncio
import concurrent.futures
import logging
from typing import Any

from integrations.trae_bridge import TraeBridge, TraeResult

logger = logging.getLogger("siyi.assistant.capability.trae")


class TraeCapabilityDomain:
    """Expose TraeBridge operations through the capability router."""

    def __init__(self, router) -> None:
        self.router = router
        self._bridge: TraeBridge | None = None

    def _get_bridge(self) -> TraeBridge:
        if self._bridge is None:
            self._bridge = TraeBridge(
                settings_repository=getattr(self.router, "settings_repository", None),
                working_dir=getattr(self.router, "workspace_root", None),
            )
        return self._bridge

    @staticmethod
    def _result(result: TraeResult) -> dict[str, Any]:
        return {
            "success": result.success,
            "action": result.action,
            "output": result.output,
            "error": result.error,
            "cli_path": result.cli_path,
            "duration_seconds": result.duration_seconds,
            "started_at": result.started_at,
            "completed_at": result.completed_at,
        }

    @staticmethod
    def _run(coroutine) -> TraeResult:
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(coroutine)
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
            return executor.submit(asyncio.run, coroutine).result()

    def trae_open(self, payload: dict[str, Any]) -> dict[str, Any]:
        path = str(payload.get("path") or ".")
        try:
            result = self._run(
                self._get_bridge().open_project(
                    path,
                    new_window=bool(payload.get("new_window", False)),
                    reuse_window=bool(payload.get("reuse_window", False)),
                    wait=bool(payload.get("wait", False)),
                )
            )
            return self._result(result)
        except Exception as exc:
            logger.warning("trae.open failed: %s", exc)
            return {"success": False, "reason": str(exc)}

    def trae_open_file(self, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            result = self._run(
                self._get_bridge().open_file(
                    str(payload.get("file_path") or ""),
                    line=payload.get("line"),
                    column=payload.get("column"),
                    new_window=bool(payload.get("new_window", False)),
                )
            )
            return self._result(result)
        except Exception as exc:
            logger.warning("trae.open_file failed: %s", exc)
            return {"success": False, "reason": str(exc)}

    def trae_diff(self, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            return self._result(
                self._run(
                    self._get_bridge().diff(
                        str(payload.get("file1") or ""),
                        str(payload.get("file2") or ""),
                    )
                )
            )
        except Exception as exc:
            logger.warning("trae.diff failed: %s", exc)
            return {"success": False, "reason": str(exc)}

    def trae_list_extensions(self, _payload: dict[str, Any]) -> dict[str, Any]:
        try:
            return self._result(self._run(self._get_bridge().list_extensions()))
        except Exception as exc:
            logger.warning("trae.list_extensions failed: %s", exc)
            return {"success": False, "reason": str(exc)}

    def trae_install_extension(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._extension_action(payload, "install_extension")

    def trae_uninstall_extension(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._extension_action(payload, "uninstall_extension")

    def _extension_action(self, payload: dict[str, Any], action: str) -> dict[str, Any]:
        extension_id = str(payload.get("extension_id") or "").strip()
        if not extension_id:
            return {"success": False, "reason": "missing_extension_id"}
        try:
            bridge = self._get_bridge()
            coroutine = getattr(bridge, action)(extension_id)
            return self._result(self._run(coroutine))
        except Exception as exc:
            logger.warning("trae.%s failed: %s", action, exc)
            return {"success": False, "reason": str(exc)}


__all__ = ["TraeCapabilityDomain"]
