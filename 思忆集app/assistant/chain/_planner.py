"""Agent 链计划生成：目标拆解与执行计划构建。

Extracted from agent_chain.py — AgentChainEngine decomposition & planning methods.
"""

from __future__ import annotations

import json
import logging
import uuid
from typing import TYPE_CHECKING, Any, Protocol

from ._nodes import (
    MAX_RETRIES_DEFAULT,
    DELEGATE_LOCAL,
    DELEGATE_CLOUD,
    DELEGATE_MCP,
    DELEGATE_AUTOMATION,
    extract_json_block,
)
from ..models import AgentChainPlan, ChainNode
from services.context_compressor import compress_text_head_tail

logger = logging.getLogger("siyi.assistant.agent_chain.planner")


# P1-3: 重规划时识别的"换策略"候选委派顺序——同一节点重规划时，
# 按此顺序挑选下一个委派目标，避免重复同一执行器。
_DELEGATE_FALLBACK_CHAIN: dict[str, str] = {
    DELEGATE_LOCAL: DELEGATE_CLOUD,
    DELEGATE_CLOUD: DELEGATE_MCP,
    DELEGATE_MCP: DELEGATE_AUTOMATION,
    DELEGATE_AUTOMATION: DELEGATE_CLOUD,
}


class _PlannerHost(Protocol):
    """PlannerMixin 依赖的宿主契约 — AgentChainEngine.__init__ 注入 _ai_client。"""

    _ai_client: Any


class PlannerMixin:
    """Mixin providing goal decomposition and plan building methods.

    Expects self._ai_client set by AgentChainEngine.__init__.
    """

    if TYPE_CHECKING:
        _ai_client: Any

    def _decompose_goal(
        self,
        goal: str,
        context: dict[str, Any],
    ) -> list[ChainNode]:
        """用 LLM 将目标拆解为子步骤。"""
        nodes: list[ChainNode] = []

        rule_nodes = self._decompose_by_rule(goal)
        if rule_nodes:
            return rule_nodes

        if self._ai_client is not None:
            try:
                llm_nodes = self._decompose_by_llm(goal, context)
                if llm_nodes:
                    return llm_nodes
            except Exception:
                logger.warning("LLM 拆解失败，使用兜底", exc_info=True)

        nodes.append(
            ChainNode(
                node_id=f"step_1_{uuid.uuid4().hex[:4]}",
                description=f"分析目标：{goal[:40]}",
                action_id="reasoning",
                delegate_to=DELEGATE_LOCAL,
                risk_level="low",
            )
        )
        nodes.append(
            ChainNode(
                node_id=f"step_2_{uuid.uuid4().hex[:4]}",
                description="执行主要操作",
                action_id="shell.run",
                delegate_to=DELEGATE_LOCAL,
                risk_level="medium",
                depends_on=[nodes[0].node_id],
            )
        )
        nodes.append(
            ChainNode(
                node_id=f"step_3_{uuid.uuid4().hex[:4]}",
                description="汇总结果",
                action_id="reasoning",
                delegate_to=DELEGATE_LOCAL,
                risk_level="low",
                depends_on=[nodes[1].node_id],
            )
        )
        return nodes

    def _decompose_by_rule(self, goal: str) -> list[ChainNode] | None:
        """用规则解析常见任务模式。"""
        lower = goal.lower()
        nodes: list[ChainNode] = []
        step_counter = [0]

        def _next_id() -> str:
            step_counter[0] += 1
            return f"step_{step_counter[0]}_{uuid.uuid4().hex[:4]}"

        if "整理" in goal and ("收件箱" in goal or "inbox" in lower):
            s1 = _next_id()
            nodes.append(
                ChainNode(
                    node_id=s1,
                    description="扫描收件箱，列出所有待处理项",
                    action_id="inbox.list",
                    delegate_to=DELEGATE_LOCAL,
                    risk_level="low",
                )
            )
            s2 = _next_id()
            nodes.append(
                ChainNode(
                    node_id=s2,
                    description="分析每项内容，判断是任务、归档还是丢弃",
                    action_id="reasoning",
                    delegate_to=DELEGATE_CLOUD,
                    risk_level="medium",
                    depends_on=[s1],
                )
            )
            s3 = _next_id()
            nodes.append(
                ChainNode(
                    node_id=s3,
                    description="将匹配项目的转任务，不重要的归档",
                    action_id="inbox.organize",
                    delegate_to=DELEGATE_AUTOMATION,
                    risk_level="high",
                    depends_on=[s2],
                )
            )
            s4 = _next_id()
            nodes.append(
                ChainNode(
                    node_id=s4,
                    description="汇总整理结果",
                    action_id="reasoning",
                    delegate_to=DELEGATE_LOCAL,
                    risk_level="low",
                    depends_on=[s3],
                )
            )
            return nodes

        if ("整理" in goal or "归类" in goal or "组织" in goal) and ("文件" in goal or "文档" in goal):
            s1 = _next_id()
            nodes.append(
                ChainNode(
                    node_id=s1,
                    description="扫描目标目录，列出所有文件",
                    action_id="file.list",
                    delegate_to=DELEGATE_MCP,
                    risk_level="low",
                )
            )
            s2 = _next_id()
            nodes.append(
                ChainNode(
                    node_id=s2,
                    description="分析文件分类策略",
                    action_id="reasoning",
                    delegate_to=DELEGATE_CLOUD,
                    risk_level="low",
                    depends_on=[s1],
                )
            )
            s3 = _next_id()
            nodes.append(
                ChainNode(
                    node_id=s3,
                    description="按分类策略移动/整理文件",
                    action_id="file.organize",
                    delegate_to=DELEGATE_MCP,
                    risk_level="high",
                    depends_on=[s2],
                )
            )
            s4 = _next_id()
            nodes.append(
                ChainNode(
                    node_id=s4,
                    description="生成整理报告",
                    action_id="reasoning",
                    delegate_to=DELEGATE_LOCAL,
                    risk_level="low",
                    depends_on=[s3],
                )
            )
            return nodes

        if any(p in goal for p in ("分析", "对比", "比较", "调研", "研究")):
            s1 = _next_id()
            nodes.append(
                ChainNode(
                    node_id=s1,
                    description="搜集相关信息和数据",
                    action_id="web.search",
                    delegate_to=DELEGATE_MCP,
                    risk_level="low",
                )
            )
            s2 = _next_id()
            nodes.append(
                ChainNode(
                    node_id=s2,
                    description="深入分析关键信息",
                    action_id="reasoning",
                    delegate_to=DELEGATE_CLOUD,
                    risk_level="medium",
                    depends_on=[s1],
                )
            )
            s3 = _next_id()
            nodes.append(
                ChainNode(
                    node_id=s3,
                    description="生成分析报告",
                    action_id="reasoning",
                    delegate_to=DELEGATE_CLOUD,
                    risk_level="low",
                    depends_on=[s2],
                )
            )
            return nodes

        return None

    def _decompose_by_llm(
        self,
        goal: str,
        context: dict[str, Any],
    ) -> list[ChainNode] | None:
        """用 LLM 将目标拆解为步骤。"""
        context_hint = ""
        if context.get("user_profile"):
            context_hint = "\n用户偏好: " + json.dumps(context["user_profile"], ensure_ascii=False)
        if context.get("available_capabilities"):
            caps = context["available_capabilities"]
            context_hint += f"\n可用能力: {json.dumps(caps[:10], ensure_ascii=False)}"

        prompt = (
            "将以下目标拆解为 JSON 步骤列表。每步包含：\n"
            "- node_id: 唯一标识（step_1, step_2, ...）\n"
            "- description: 步骤描述\n"
            "- action_id: 操作类型（reasoning/file.read/file.write/web.search/shell.run/inbox.list）\n"
            "- depends_on: 前置步骤 ID 列表（空列表表示无依赖）\n"
            "- mode: serial（串行）| parallel（可并行）\n"
            "- delegate_to: local（本地低风险）| cloud（高复杂度）| mcp（外部工具）| automation（自动化规则）\n"
            "- risk_level: low | medium | high\n\n"
            f"目标：{goal}\n"
            f"{context_hint}\n\n"
            "输出格式：\n"
            '{"steps": [{"node_id": "...", "description": "...", "action_id": "...", '
            '"depends_on": [], "mode": "serial", "delegate_to": "local", "risk_level": "low"}]}\n'
            "最多 8 步，每步描述不超过 30 字。"
        )

        try:
            response = self._ai_client.chat_completion(
                messages=[
                    {"role": "system", "content": "你是任务拆解专家，将复杂目标分解为可执行的步骤列表，只输出 JSON。"},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.2,
                max_tokens=800,
            )

            raw = ""
            if isinstance(response, dict):
                choices = response.get("choices") or []
                if choices:
                    raw = (choices[0].get("message") or {}).get("content", "")
            elif isinstance(response, str):
                raw = response

            if not raw.strip():
                return None

            json_str = extract_json_block(raw)
            if json_str is None:
                return None
            parsed = json.loads(json_str)
            steps_data = parsed.get("steps", []) if isinstance(parsed, dict) else parsed if isinstance(parsed, list) else []

            nodes: list[Any] = []

            for sd in steps_data:
                node = ChainNode(
                    node_id=sd.get("node_id", f"step_{len(nodes) + 1}_{uuid.uuid4().hex[:4]}"),
                    description=sd.get("description", "执行步骤"),
                    action_id=sd.get("action_id", "reasoning"),
                    payload={},
                    depends_on=list(sd.get("depends_on", [])),
                    mode=sd.get("mode", "serial"),
                    delegate_to=sd.get("delegate_to", DELEGATE_LOCAL),
                    risk_level=sd.get("risk_level", "low"),
                    max_retries=MAX_RETRIES_DEFAULT,
                )
                nodes.append(node)

            return nodes if nodes else None

        except Exception:
            logger.warning("LLM 拆解失败", exc_info=True)
            return None

    def _build_plan(
        self,
        goal: str,
        nodes: list[ChainNode],
        context: dict[str, Any],
    ) -> AgentChainPlan:
        """构建执行计划对象。"""
        estimated_seconds = sum(self._estimate_node_duration(n) for n in nodes)
        risk_levels = [n.risk_level for n in nodes]
        highest_risk = "high" if "high" in risk_levels else "medium" if "medium" in risk_levels else "low"

        return AgentChainPlan(
            goal=goal,
            nodes=nodes,
            total_steps=len(nodes),
            estimated_seconds=estimated_seconds,
            highest_risk=highest_risk,
        )

    # ═══════════════════════════════════════════════════════════
    # P1-3: 重规划（ADJUST 闭环）
    # ═══════════════════════════════════════════════════════════

    def _replan_node(
        self,
        goal: str,
        failed_node: ChainNode,
        error: str,
        attempt: int,
        context: dict[str, Any] | None = None,
    ) -> ChainNode | None:
        """P1-3: 重规划失败的节点。

        策略（按优先级降序）：
        1. LLM 重规划：调用 AI 客户端，传入失败原因 + 上游结果，生成替代节点
        2. 规则重规划：切换委派目标（local→cloud→mcp→automation），
           调整风险等级或拆分为更小步骤
        3. 兜底：返回 None（终止重规划，由调用方判定最终失败）

        Args:
            goal: 链总目标
            failed_node: 失败的节点
            error: 失败原因
            attempt: 当前重规划次数（1-based）
            context: 可选上下文（含上游结果）

        Returns:
            新的 ChainNode（带新 node_id 后缀 _replan{N}）；失败返回 None
        """
        ctx = context or {}
        # LLM 路径优先
        if self._ai_client is not None:
            llm_node = self._replan_by_llm(goal, failed_node, error, attempt, ctx)
            if llm_node is not None:
                logger.info(
                    "P1-3 LLM 重规划成功: %s (attempt=%d, new_delegate=%s)",
                    failed_node.node_id,
                    attempt,
                    llm_node.delegate_to,
                )
                return llm_node
            logger.warning(
                "P1-3 LLM 重规划失败，回退到规则重规划: %s (attempt=%d)",
                failed_node.node_id,
                attempt,
            )

        # 规则重规划：换委派目标
        return self._replan_by_rule(failed_node, attempt)

    def _replan_by_llm(
        self,
        goal: str,
        failed_node: ChainNode,
        error: str,
        attempt: int,
        context: dict[str, Any],
    ) -> ChainNode | None:
        """用 LLM 生成替代节点。"""
        upstream_hint = ""
        upstream = context.get("upstream_results") or {}
        if upstream:
            try:
                upstream_hint = "\n上游结果摘要: " + compress_text_head_tail(
                    json.dumps(
                        {dep_id: (r.get("execution", {}).get("data") if isinstance(r, dict) else None) for dep_id, r in upstream.items()},
                        ensure_ascii=False,
                        default=str,
                    ),
                    max_chars=600,
                )
            except Exception:
                upstream_hint = ""

        prompt = (
            "你是任务重规划专家。下面这个执行步骤失败了，请生成一个替代步骤。\n\n"
            f"总体目标: {goal}\n"
            f"失败步骤: {failed_node.description}\n"
            f"原动作: {failed_node.action_id}\n"
            f"原执行器: {failed_node.delegate_to}\n"
            f"失败原因: {compress_text_head_tail(error, max_chars=300)}\n"
            f"重规划次数: {attempt}{upstream_hint}\n\n"
            "请输出一个 JSON 对象，字段：\n"
            "- description: 新步骤描述（不超过 30 字）\n"
            "- action_id: 新动作类型（reasoning/file.read/file.write/web.search/shell.run/inbox.list 等）\n"
            "- delegate_to: local | cloud | mcp | automation（必须与原执行器不同）\n"
            "- risk_level: low | medium | high\n"
            "- payload: 可选对象\n\n"
            "只输出 JSON，不要其他内容。"
        )

        try:
            response = self._ai_client.chat_completion(
                messages=[
                    {"role": "system", "content": "你是任务重规划助手，只输出 JSON。"},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.3,
                max_tokens=500,
            )

            raw = ""
            if isinstance(response, dict):
                choices = response.get("choices") or []
                if choices:
                    raw = (choices[0].get("message") or {}).get("content", "")
            elif isinstance(response, str):
                raw = response

            if not raw.strip():
                return None

            json_str = extract_json_block(raw)
            if json_str is None:
                return None
            parsed = json.loads(json_str)
            if not isinstance(parsed, dict):
                return None

            new_delegate = str(parsed.get("delegate_to", failed_node.delegate_to))
            # 强制要求与原执行器不同（避免 LLM 输出相同委派目标导致死循环）
            if new_delegate == failed_node.delegate_to:
                new_delegate = _DELEGATE_FALLBACK_CHAIN.get(
                    failed_node.delegate_to,
                    DELEGATE_CLOUD,
                )

            new_node = ChainNode(
                node_id=f"{failed_node.node_id}_replan{attempt}",
                description=str(parsed.get("description", f"重规划：{failed_node.description}"))[:60],
                action_id=str(parsed.get("action_id", failed_node.action_id)),
                payload=dict(parsed.get("payload") or failed_node.payload or {}),
                depends_on=list(failed_node.depends_on),
                mode=failed_node.mode,
                delegate_to=new_delegate,
                risk_level=str(parsed.get("risk_level", failed_node.risk_level)),
                max_retries=MAX_RETRIES_DEFAULT,
            )
            # 继承上游结果（重规划节点可读取原节点的上游输出）
            new_node.upstream_results = getattr(failed_node, "upstream_results", {})  # type: ignore[attr-defined]
            return new_node
        except Exception:
            logger.warning("P1-3 LLM 重规划异常", exc_info=True)
            return None

    @staticmethod
    def _replan_by_rule(
        failed_node: ChainNode,
        attempt: int,
    ) -> ChainNode | None:
        """规则重规划：换委派目标 + 重置重试计数。"""
        new_delegate = _DELEGATE_FALLBACK_CHAIN.get(
            failed_node.delegate_to,
            DELEGATE_CLOUD,
        )
        # 第二次重规划再换一档
        if attempt >= 2:
            new_delegate = _DELEGATE_FALLBACK_CHAIN.get(new_delegate, DELEGATE_CLOUD)

        new_node = ChainNode(
            node_id=f"{failed_node.node_id}_replan{attempt}",
            description=f"重规划(attempt {attempt})：{failed_node.description}",
            action_id=failed_node.action_id,
            payload=dict(failed_node.payload or {}),
            depends_on=list(failed_node.depends_on),
            mode=failed_node.mode,
            delegate_to=new_delegate,
            risk_level=failed_node.risk_level,
            max_retries=MAX_RETRIES_DEFAULT,
        )
        # 继承上游结果
        new_node.upstream_results = getattr(failed_node, "upstream_results", {})  # type: ignore[attr-defined]
        logger.info(
            "P1-3 规则重规划: %s → %s (delegate %s→%s)",
            failed_node.node_id,
            new_node.node_id,
            failed_node.delegate_to,
            new_delegate,
        )
        return new_node

    @staticmethod
    def _estimate_node_duration(node: ChainNode) -> int:
        """估算单个节点执行时间（秒）。"""
        base = {
            "reasoning": 10,
            "file.read": 5,
            "file.write": 8,
            "file.list": 5,
            "web.search": 15,
            "shell.run": 20,
            "inbox.list": 5,
            "inbox.organize": 10,
        }
        return base.get(node.action_id, 10)
