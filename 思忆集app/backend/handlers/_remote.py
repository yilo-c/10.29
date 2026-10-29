"""Remote control JSON-RPC handlers — mobile/cross-device entry points.

Bridge:
- `remote.agent.execute` → `agent.run_now`-style synchronous execution

All handlers require an authenticated WS connection (enforced by `ws_server._verify_auth`
for non-localhost peers) — there is no separate authorization layer here.

@deprecated (S11, 2026-08-07): remote.agent.execute 为僵尸接口——后端已实现但
前端（desk-notes / 手机端app）从未调用。列入 UNUSED_METHODS_BASELINE。

V2.1 批次1#7: 移除远程命令执行与截屏攻击面——
- `remote.shell`（shell_run）→ 已删除（避免跨设备 RCE 面）
- `remote.screenshot`（screen_read）→ 已删除（避免跨设备私密屏暴露）
二者前端从未调用，删除为净收缩。
"""

from __future__ import annotations

import logging
from typing import Any

from app_runtime.app_context import AppContext
from backend.i18n import t

logger = logging.getLogger("siyi.backend.handlers.remote")


def register_remote(registry, context: AppContext) -> None:
    """Register `remote.*` JSON-RPC methods for mobile/cross-device control."""

    # V2.1 批次1#7: remote.screenshot / remote.shell 已移除（攻击面净收缩）。

    # ── remote.agent.execute ──────────────────────────────────────
    async def remote_agent_execute(
        agent_id: str = "",
        task: str = "",
        context_data: dict[str, Any] | None = None,
        timeout: int = 300,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Trigger an external agent driver immediately (mobile entry into `agent.run_now`)."""
        agent_id = str(agent_id or "").strip()
        task = str(task or "").strip()
        if not agent_id:
            return {"success": False, "error": t("error.missing_param", name="agent_id")}
        if not task:
            return {"success": False, "error": t("error.missing_param", name="task")}
        directory = getattr(context, "agent_directory", None)
        if directory is None:
            return {"success": False, "error": t("agent.directory_unavailable")}
        driver = directory.get_driver(agent_id)
        if driver is None:
            return {"success": False, "error": t("agent.not_registered", agent_id=agent_id)}
        if not driver.is_available:
            return {"success": False, "error": t("agent.unavailable", agent_id=agent_id)}
        try:
            invocation = await driver.execute(
                task,
                context_data or {},
                timeout=int(timeout),
            )
        except Exception as exc:
            logger.warning("remote.agent.execute(%s) failed: %s", agent_id, exc)
            return {"success": False, "error": f"{type(exc).__name__}: {exc}"}
        try:
            directory.record_invocation(
                invocation.invocation_id,
                agent_id,
                task,
                invocation.status,
            )
        except Exception:
            logger.debug("remote.agent.execute: invocation log skipped", exc_info=True)
        return {
            "success": invocation.status in ("completed", "success"),
            "invocation": invocation.to_dict(),
        }

    registry.register("remote.agent.execute", remote_agent_execute)
