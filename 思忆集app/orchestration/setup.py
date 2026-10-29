"""Setup helper — 一键初始化所有 Agent Driver 并注册到 AgentDirectory。

供 AppContext 或测试代码调用。

历史：原先注册了 OpenClaw / Hermes 两个 CLI Driver，但它们是空壳子进程，
从未在生产中调用过。已于 P2-13 移除注册路径，源码保留供参考。
"""

from __future__ import annotations

import logging
from typing import Any

from .agent_directory import AgentDirectory
from .drivers.claude_code_driver import ClaudeCodeDriver
from .drivers.codex_driver import CodexDriver
from .drivers.trae_driver import TraeDriver

logger = logging.getLogger("siyi.orchestration.setup")


def create_default_directory(
    *,
    settings_repository=None,
    memory_service=None,
    working_dir: str | None = None,
    data_store=None,
    planner=None,
    security=None,
    memory=None,
    observability_service=None,
    settings_repository_for_codex=None,
) -> AgentDirectory:
    """创建并注册所有可用的 Agent Driver。

    每个 Driver 会检测 CLI 是否可用，不可用的自动标记为 unavailable。

    Args:
        settings_repository: ClaudeCodeDriver 的 settings 依赖
        memory_service: ClaudeCodeDriver 的 memory 依赖
        working_dir: ClaudeCodeDriver 的工作目录
        data_store: CodexDriver 的 data_store 依赖
        planner: CodexDriver 的 planner 依赖
        security: CodexDriver 的 security 依赖
        memory: CodexDriver 的 memory 依赖
        observability_service: 可观测性服务
        settings_repository_for_codex: CodexDriver 的 settings 依赖

    Returns:
        配置好的 AgentDirectory
    """
    directory = AgentDirectory()

    # Claude Code Driver（包装 CCBridge）
    try:
        cc_driver = ClaudeCodeDriver(
            settings_repository=settings_repository,
            memory_service=memory_service,
            working_dir=working_dir,
        )
        directory.register(cc_driver)
        if cc_driver.is_available:
            logger.info("Claude Code CLI available at %s", cc_driver._bridge._cli_path if cc_driver._bridge else "?")
        else:
            logger.info("Claude Code CLI not found — mark claude_code as unavailable")
    except Exception as exc:
        logger.warning("Failed to initialize ClaudeCodeDriver: %s", exc)

    # Codex Driver
    try:
        cx_driver = CodexDriver(
            data_store=data_store,
            planner=planner,
            security=security,
            memory=memory,
            observability_service=observability_service,
            settings_repository=settings_repository_for_codex,
        )
        directory.register(cx_driver)
        logger.info("CodexDriver registered")
    except Exception as exc:
        logger.warning("Failed to initialize CodexDriver: %s", exc)

    # Trae IDE Driver（包装 TraeBridge）
    try:
        trae_driver = TraeDriver(
            settings_repository=settings_repository,
            working_dir=working_dir,
        )
        directory.register(trae_driver)
        if trae_driver.is_available:
            logger.info("Trae CLI available at %s", trae_driver._bridge.cli_path if trae_driver._bridge else "?")
        else:
            logger.info("Trae CLI not found — mark trae as unavailable")
    except Exception as exc:
        logger.warning("Failed to initialize TraeDriver: %s", exc)

    total = len(directory.list_infos())
    available = directory.count_available()
    logger.info("AgentDirectory ready: %d registered, %d available", total, available)

    return directory
