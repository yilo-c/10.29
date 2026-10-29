"""local_agent.cli — Typer CLI 入口。"""

from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path

import typer

app = typer.Typer(name="local-agent", help="automation-hub 本地代理 CLI")

LOG_FMT = "%(asctime)s %(levelname)-7s [%(name)s] %(message)s"


def _setup_logging(verbose: bool = False) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(format=LOG_FMT, level=level, stream=sys.stderr)


# ------------------------------------------------------------------
# start — 启动代理（前台）
# ------------------------------------------------------------------

@app.command()
def start(
    hub_url: str = typer.Option("http://127.0.0.1:8000", "--hub-url", "-u", help="控制面地址"),
    token: str = typer.Option("", "--token", "-t", help="授权 token"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """启动本地代理，连接到 automation-hub 控制面。"""
    _setup_logging(verbose)

    from local_agent.bus import LocalBus
    from local_agent.connector import HubConnector
    from local_agent.policy import PolicyCache
    from local_agent.runtime import AgentRuntime
    from local_agent.session import SessionManager

    connector = HubConnector(base_url=hub_url, token=token or None)
    bus = LocalBus()
    session_mgr = SessionManager()
    policy = PolicyCache()
    runtime = AgentRuntime(connector=connector, bus=bus, session_mgr=session_mgr, policy=policy)

    async def _run() -> None:
        await runtime.start()
        # 前台 CLI 模式：从 stdin 读取消息
        from local_agent.channels.cli_channel import CLIChannel
        channel = CLIChannel(bus=bus)
        try:
            await channel.run()
        except (KeyboardInterrupt, asyncio.CancelledError):
            pass
        finally:
            await runtime.stop()

    asyncio.run(_run())


# ------------------------------------------------------------------
# daemon — 后台守护进程模式
# ------------------------------------------------------------------

@app.command()
def daemon(
    hub_url: str = typer.Option("http://127.0.0.1:8000", "--hub-url", "-u"),
    token: str = typer.Option("", "--token", "-t"),
    pid_file: str = typer.Option("", "--pid-file", "-p"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """以守护进程模式在后台运行。"""
    _setup_logging(verbose)
    from local_agent.daemon import run_daemon
    run_daemon(hub_url=hub_url, token=token or None, pid_file=pid_file or None)


# ------------------------------------------------------------------
# status — 查看代理状态
# ------------------------------------------------------------------

@app.command()
def status(
    hub_url: str = typer.Option("http://127.0.0.1:8000", "--hub-url", "-u"),
    token: str = typer.Option("", "--token", "-t"),
) -> None:
    """查看本地代理连接状态。"""
    from local_agent.connector import HubConnector

    connector = HubConnector(base_url=hub_url, token=token or None)
    ok = asyncio.run(connector.health_check())
    if ok:
        typer.echo("✓ 控制面可达")
    else:
        typer.echo("✗ 控制面不可达", err=True)
        raise typer.Exit(1)


# ------------------------------------------------------------------
# register — 手动注册
# ------------------------------------------------------------------

@app.command()
def register(
    hub_url: str = typer.Option("http://127.0.0.1:8000", "--hub-url", "-u"),
    token: str = typer.Option("", "--token", "-t"),
    label: list[str] = typer.Option([], "--label", "-l", help="key=value 标签"),
) -> None:
    """手动注册节点（调试用）。"""
    import platform
    from local_agent.connector import HubConnector
    from local_agent.models import NodeRegisterRequest

    labels: dict[str, str] = {}
    for item in label:
        if "=" in item:
            k, v = item.split("=", 1)
            labels[k] = v

    connector = HubConnector(base_url=hub_url, token=token or None)
    req = NodeRegisterRequest(
        hostname=platform.node(),
        labels=labels,
        capabilities=[],
    )
    resp = asyncio.run(connector.register(req))
    typer.echo(f"node_id = {resp.node_id}")


# ------------------------------------------------------------------
# run — 一次性执行工具
# ------------------------------------------------------------------

@app.command(name="run")
def run_tool(
    tool: str = typer.Argument(..., help="工具名称"),
    hub_url: str = typer.Option("http://127.0.0.1:8000", "--hub-url", "-u"),
    token: str = typer.Option("", "--token", "-t"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """一次性执行指定工具后退出。"""
    _setup_logging(verbose)
    typer.echo(f"run tool={tool} → 尚未实现，请使用 start 模式")


def main() -> None:
    app()


if __name__ == "__main__":
    main()
