"""后端审批补发 watcher —— 弥合 MCP 内核审批与前端之间的跨进程事件缺口。

背景
====
内核（DSH 等）发起的高危操作审批经 MCP 工具 ``siyi_confirm_request`` 落地：
``mcp_server/tools.py::_confirm_request`` 只把条目写入跨进程共享的
``confirmations.json``，**不发射任何事件**；且 MCP server 是独立 stdio 子进程，
即使发射事件也到不了持有 EventBridge 的后端进程。因此前端（Electron ChatView
订阅 ``action.confirm_required``）永远收不到通知，审批弹窗不出现。

本模块在后端进程内轮询 ConfirmationStore（读同一份 ``confirmations.json``），
把新出现的、非 butler 发起的 pending 审批条目补发为 ``action.confirm_required``
事件，经 EventBridge 转发到 WebSocket 客户端。

规则
====
* 只补发 ``source != "butler"`` 的条目 —— butler 发起时已在同进程内同步 emit 事件。
* 只补发 ``status == "pending"`` 且本进程尚未宣布过的 confirmation_id（内存去重，
  避免同一条目重复弹卡）。
* 轮询间隔默认 2s（审批为交互式请求，秒级可见即可）。
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from core.events import AppEvent

logger = logging.getLogger("siyi.backend.confirmation_watcher")

_DEFAULT_POLL_INTERVAL_S = 2.0

#: 不需要补发的事件来源：butler 发起时已自行 emit action.confirm_required
_EXCLUDED_SOURCES = ("butler",)


class ConfirmationWatcher:
    """轮询 ConfirmationStore 并为跨进程审批补发 action.confirm_required 事件。"""

    def __init__(
        self,
        store: Any,
        event_bus: Any,
        interval_s: float = _DEFAULT_POLL_INTERVAL_S,
    ) -> None:
        self._store = store
        self._event_bus = event_bus
        self._interval_s = interval_s
        self._announced: set[str] = set()
        self._task: asyncio.Task | None = None

    def start(self) -> None:
        """启动后台轮询任务；重复调用安全。"""
        if self._task is not None and not self._task.done():
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            logger.warning("confirmation watcher start skipped: no running loop")
            return
        self._task = loop.create_task(self._run())
        logger.info("confirmation watcher started (interval=%.1fs)", self._interval_s)

    async def stop(self) -> None:
        """停止后台轮询任务。"""
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
            self._task = None
        logger.info("confirmation watcher stopped")

    async def _run(self) -> None:
        try:
            while True:
                try:
                    self._scan()
                except Exception as exc:  # noqa: BLE001
                    logger.warning("confirmation scan failed (non-fatal): %s", exc)
                await asyncio.sleep(self._interval_s)
        except asyncio.CancelledError:
            raise

    def _scan(self) -> None:
        """扫描共享 store，把未宣布过的跨进程 pending 条目补发为事件。"""
        if self._event_bus is None:
            return
        entries = self._store.list()
        for entry in entries:
            confirmation_id = str(entry.get("confirmation_id") or "")
            if not confirmation_id:
                continue
            if entry.get("status") != "pending":
                continue
            if entry.get("source") in _EXCLUDED_SOURCES:
                continue
            if confirmation_id in self._announced:
                continue
            self._announced.add(confirmation_id)
            payload = {
                "confirmation_id": confirmation_id,
                "action_id": entry.get("action_id") or "",
                "args": dict(entry.get("args") or {}),
                "source": entry.get("source") or "",
                "title": entry.get("title") or entry.get("action_id") or "",
                "detail": entry.get("detail") or "",
            }
            try:
                self._event_bus.emit(AppEvent(
                    event_type="action.confirm_required",
                    payload=payload,
                    source="confirmation_watcher",
                ))
                logger.info("re-emitted action.confirm_required for %s (%s)",
                            confirmation_id[:8], payload["action_id"])
            except Exception as exc:  # noqa: BLE001
                logger.warning("emit action.confirm_required failed for %s: %s",
                               confirmation_id, exc)
