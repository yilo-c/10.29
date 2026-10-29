"""多 Agent 协作编排器 — SubAgent 能力声明注册表。

历史形态是 Planner + Worker 层级模式：``run_planner_workflow`` 分解任务 →
``_execute_subagent`` → ``_run_react_loop`` 自转一套 ReAct 工具循环。该链是
C-1 执行器收敛（审计 docs/architecture-audit-2026-09-23.md）点名的第三套执行体，
生产调用方为零：chat 的 multi_agent 分支 ``backend/handlers/chat_paths/
multi_agent_path.py`` 是 9 行 stub（只 ``return``，不落到这里），
``make_brokered_react_executor`` 也随本波下线。故整条工作流链、P3-11 结果缓存腿、
P1-5 输出压缩腿一并摘除。

保留的是 SubAgent **声明面**：browser / coder / researcher / file_ops 四组
action_id 工具池与 ``list_agents`` 查询——``app_runtime/_context_builder.py``
仍无条件构造本类、``backend/handlers/_chat_memory.py`` 仍以
``hasattr(context, "multi_agent_orchestrator")`` 作为分支门控。
工具执行统一收敛到 ``assistant/capability_router.CapabilityRouter.dispatch``。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger("siyi.assistant.multi_agent")


@dataclass
class SubAgentConfig:
    """SubAgent 配置 — 定义一个专业 Agent 的能力边界。"""

    name: str  # 如 "browser", "coder", "researcher"
    description: str  # 能力描述（供 Planner 决策）
    tools: list[str]  # 可用的工具 action_id 列表
    system_prompt: str = ""  # 额外的系统提示词
    max_steps: int = 10  # 最大 ReAct 循环步数
    timeout_seconds: int = 120  # 超时时间


class MultiAgentOrchestrator:
    """SubAgent 能力声明注册表。

    只回答「哪个专业 Agent 声明了哪些 action_id」；不再自带执行循环。
    sandbox 腿的工具执行经 ``services/react_executor.make_react_executor``
    转发到 capability_router.dispatch。
    """

    def __init__(self, context: Any) -> None:
        self._context = context
        self._agents: dict[str, SubAgentConfig] = {}
        self.register_defaults()

    def register_agent(self, config: SubAgentConfig) -> None:
        """注册一个 SubAgent。"""
        self._agents[config.name] = config
        logger.info("SubAgent 注册: %s (tools=%d)", config.name, len(config.tools))

    def register_defaults(self) -> None:
        """注册默认的 SubAgent 集合。"""
        # 浏览器 SubAgent — 专注网页操作
        self.register_agent(
            SubAgentConfig(
                name="browser",
                description="浏览器自动化专家。可以打开网页、点击、填表、提取数据、截图。"
                "适用于：网页信息采集、在线表单填写、网页内容提取、网页截图。",
                tools=[
                    "browser.navigate",
                    "browser.click",
                    "browser.type",
                    "browser.extract",
                    "browser.screenshot",
                    "browser.scroll",
                    "browser.get_tabs",
                    "browser.switch_tab",
                    "browser.wait",
                    "browser.close",
                    "web.search",
                    "web.fetch",
                ],
                system_prompt="你是浏览器自动化专家。请逐步操作浏览器完成任务，每步完成后报告结果。",
                max_steps=15,
                timeout_seconds=300,
            )
        )

        # 代码执行 SubAgent — 专注数据处理和代码任务
        self.register_agent(
            SubAgentConfig(
                name="coder",
                description="Python 代码执行专家。可以写代码进行数据分析、文件处理、格式转换、计算等。"
                "适用于：数据分析、文件批量处理、格式转换、数学计算、文本处理。",
                tools=[
                    "file.read",
                    "file.write",
                    "file.find",
                    "file.copy",
                    "system.info",
                ],
                system_prompt="你是 Python 编程专家。请写代码完成任务，用 print() 输出结果。",
                max_steps=8,
                timeout_seconds=120,
            )
        )

        # 研究搜索 SubAgent — 专注信息搜集
        self.register_agent(
            SubAgentConfig(
                name="researcher",
                description="信息搜索专家。可以搜索网页、提取内容、查询记忆库、总结信息。适用于：信息调研、资料收集、知识查询、事实核查。",
                tools=[
                    "web.search",
                    "web.fetch",
                    "memory.recall",
                    "translate.text",
                    "browser.navigate",
                    "browser.extract",
                ],
                system_prompt="你是信息研究专家。请系统性地搜索和整理信息，给出带来源的结论。",
                max_steps=10,
                timeout_seconds=180,
            )
        )

        # 文件操作 SubAgent — 专注本地文件管理
        self.register_agent(
            SubAgentConfig(
                name="file_ops",
                description="文件操作专家。可以读写文件、搜索文件、复制文件、整理目录。适用于：文件整理、内容查找、文件批量重命名、目录扫描。",
                tools=[
                    "file.read",
                    "file.write",
                    "file.find",
                    "file.copy",
                ],
                system_prompt="你是文件管理专家。请高效地操作文件系统完成任务。",
                max_steps=5,
                timeout_seconds=60,
            )
        )

    def list_agents(self) -> list[dict[str, Any]]:
        """列出所有已注册的 SubAgent。"""
        return [
            {
                "name": cfg.name,
                "description": cfg.description,
                "tools": cfg.tools,
                "max_steps": cfg.max_steps,
                "timeout_seconds": cfg.timeout_seconds,
            }
            for cfg in self._agents.values()
        ]
