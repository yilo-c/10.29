"""AgentDirectory — 可用 AI 客户端的注册表与能力查询。

所有 AgentDriver 在初始化后注册到此处，供 Planner 路由和 Orchestrator 调度使用。
"""

from __future__ import annotations

import logging
from typing import Any

from .agent_driver import AbstractAgentDriver, AgentCapability, AgentInfo, AgentStatus, _new_id, _now

logger = logging.getLogger("siyi.orchestration.directory")


class AgentDirectory:
    """Agent 目录 — 注册、查询可用 agent 及其能力。"""

    def __init__(self):
        self._drivers: dict[str, AbstractAgentDriver] = {}
        self._invocation_history: list[dict[str, Any]] = []

    # ── 注册 ───────────────────────────────────────────────

    def register(self, driver: AbstractAgentDriver) -> None:
        """注册一个 AgentDriver。"""
        info = driver.agent_info
        self._drivers[info.agent_id] = driver
        logger.info("registered agent: %s (%s) — %s", info.agent_id, info.display_name, info.description)

    def register_many(self, drivers: list[AbstractAgentDriver]) -> None:
        for d in drivers:
            self.register(d)

    # ── 查询 ───────────────────────────────────────────────

    def get_driver(self, agent_id: str) -> AbstractAgentDriver | None:
        return self._drivers.get(agent_id)

    def get_info(self, agent_id: str) -> AgentInfo | None:
        driver = self._drivers.get(agent_id)
        if driver is None:
            return None
        # 刷新可用性
        info = driver.agent_info
        info.is_available = driver.is_available
        return info

    def list_infos(self) -> list[AgentInfo]:
        """返回所有已注册 agent 的信息（刷新可用性）。"""
        result: list[AgentInfo] = []
        for driver in self._drivers.values():
            info = driver.agent_info
            info.is_available = driver.is_available
            result.append(info)
        return result

    def find_by_capability(self, capability: AgentCapability) -> list[AgentInfo]:
        """按能力查找可用 agent。"""
        matched: list[AgentInfo] = []
        for driver in self._drivers.values():
            if capability in driver.agent_info.capabilities and driver.is_available:
                info = driver.agent_info
                info.is_available = True
                matched.append(info)
        return matched

    def find_best_match(self, task_description: str) -> AgentInfo | None:
        """简单关键词匹配 — 根据任务描述推荐最合适的 agent。

        后续可升级为 LLM 打分。
        """
        task_lower = task_description.lower()

        # 关键词 → 能力映射
        code_keywords = ("代码", "重构", "审查", "测试", "写", "编码", "修复 bug", "实现", "重构",
                         "code", "refactor", "test", "implement", "bug", "pr")
        analysis_keywords = ("分析", "研究", "调查", "比较", "对比", "研究这个",
                             "analyze", "research", "investigate")
        long_task_keywords = ("收集", "监控", "跟踪", "长时间", "定時", "持续",
                              "collect", "monitor", "track", "long")
        quick_question_keywords = ("?", "什么", "怎么", "如何", "why", "how", "what", "解释", "说明")
        # Trae IDE 控制关键词
        trae_keywords = ("trae", "打开项目", "打开文件夹", "open project", "open folder",
                         "打开文件", "open file", "安装扩展", "install extension",
                         "列出扩展", "list extension", "卸载扩展", "uninstall extension")

        # 简单问答 → 思忆集自身（本地处理最快）
        if any(kw in task_lower for kw in quick_question_keywords) and len(task_description) < 100:
            local = self._find_agent("siyiji")
            if local and local.is_available:
                return local

        # 编码任务 → Claude Code
        if any(kw in task_lower for kw in code_keywords):
            cc = self._find_agent("claude_code")
            if cc and cc.is_available:
                return cc

        # Trae IDE 控制 → Trae
        if any(kw in task_lower for kw in trae_keywords):
            trae = self._find_agent("trae")
            if trae and trae.is_available:
                return trae

        # 长时间任务 → Hermes
        if any(kw in task_lower for kw in long_task_keywords):
            h = self._find_agent("hermes")
            if h and h.is_available:
                return h

        # 分析任务 → OpenClaw / Claude Code
        if any(kw in task_lower for kw in analysis_keywords):
            oc = self._find_agent("openclaw")
            if oc and oc.is_available:
                return oc
            cc = self._find_agent("claude_code")
            if cc and cc.is_available:
                return cc

        # 兜底：返回第一个可用 agent
        for driver in self._drivers.values():
            if driver.is_available:
                info = driver.agent_info
                info.is_available = True
                return info
        return None

    def _find_agent(self, agent_id: str) -> AgentInfo | None:
        driver = self._drivers.get(agent_id)
        if driver is None:
            return None
        info = driver.agent_info
        info.is_available = driver.is_available
        return info if info.is_available else None

    def count_available(self) -> int:
        return sum(1 for d in self._drivers.values() if d.is_available)

    def record_invocation(self, invocation_id: str, agent_id: str, task: str, status: str) -> None:
        self._invocation_history.append({
            "invocation_id": invocation_id,
            "agent_id": agent_id,
            "task": task[:200],
            "status": status,
            "timestamp": _now(),
        })
        self._invocation_history = self._invocation_history[-500:]  # 上限 500

    def get_recent_invocations(self, limit: int = 10) -> list[dict[str, Any]]:
        return self._invocation_history[-limit:]

    def build_catalog(self) -> list[dict[str, Any]]:
        """为 Planner 构建 agent 能力目录。"""
        catalog: list[dict[str, Any]] = []
        for info in self.list_infos():
            catalog.append({
                "agent_id": info.agent_id,
                "display_name": info.display_name,
                "description": info.description,
                "capabilities": [c.value if isinstance(c, AgentCapability) else c for c in info.capabilities],
                "is_available": info.is_available,
            })
        return catalog
