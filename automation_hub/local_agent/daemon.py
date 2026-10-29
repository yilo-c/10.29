"""local_agent.daemon — 守护进程模式。"""

from __future__ import annotations

import asyncio
import logging
import os
import signal
import sys
from pathlib import Path

logger = logging.getLogger("local_agent.daemon")


def _write_pid(pid_file: str) -> None:
    Path(pid_file).write_text(str(os.getpid()), encoding="utf-8")


def _remove_pid(pid_file: str) -> None:
    try:
        Path(pid_file).unlink(missing_ok=True)
    except Exception:
        pass


def run_daemon(
    hub_url: str,
    token: str | None = None,
    pid_file: str | None = None,
) -> None:
    """以守护形式运行本地代理（无 TTY 交互）。"""

    from local_agent.bus import LocalBus
    from local_agent.connector import HubConnector
    from local_agent.policy import PolicyCache
    from local_agent.runtime import AgentRuntime
    from local_agent.session import SessionManager

    connector = HubConnector(base_url=hub_url, token=token)
    bus = LocalBus()
    session_mgr = SessionManager()
    policy = PolicyCache()
    runtime = AgentRuntime(connector=connector, bus=bus, session_mgr=session_mgr, policy=policy)

    if pid_file:
        _write_pid(pid_file)

    loop = asyncio.new_event_loop()

    def _shutdown() -> None:
        logger.info("received shutdown signal")
        loop.create_task(runtime.stop())

    if sys.platform != "win32":
        loop.add_signal_handler(signal.SIGTERM, _shutdown)
        loop.add_signal_handler(signal.SIGINT, _shutdown)

    async def _main() -> None:
        await runtime.start()
        # 无 CLI 通道，仅维持心跳和策略循环
        stop_event = asyncio.Event()

        def _req_stop() -> None:
            stop_event.set()

        # 在 Windows 上通过 KeyboardInterrupt 退出
        try:
            await stop_event.wait()
        except (KeyboardInterrupt, asyncio.CancelledError):
            pass
        finally:
            await runtime.stop()

    try:
        loop.run_until_complete(_main())
    finally:
        if pid_file:
            _remove_pid(pid_file)
        loop.close()
