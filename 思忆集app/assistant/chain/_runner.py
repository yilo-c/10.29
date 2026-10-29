"""Agent 链执行引擎：主执行循环、拓扑排序、委派执行。

Extracted from agent_chain.py — AgentChainEngine execution loop & delegation.
"""

from __future__ import annotations

import json
import logging
import uuid
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Protocol

from ._nodes import (
    DELEGATE_LOCAL,
    DELEGATE_CLOUD,
    DELEGATE_MCP,
    DELEGATE_AUTOMATION,
    NODE_STATUS_RUNNING,
    NODE_STATUS_COMPLETED,
    NODE_STATUS_FAILED,
    CHAIN_STATUS_RUNNING,
    CHAIN_STATUS_COMPLETED,
    CHAIN_STATUS_FAILED,
    CHAIN_STATUS_CANCELLED,
    MAX_REPLAN_ATTEMPTS,  # P1-3
)
from ..models import AgentChainPlan, ChainNode
from services.context_compressor import compress_text_head_tail
from core.events import AppEvent, event_bus

logger = logging.getLogger("siyi.assistant.agent_chain.runner")


class _RunnerHost(Protocol):
    """RunnerMixin 依赖的宿主契约 — AgentChainEngine.__init__ 注入 AI/MCP/capability
    客户端与链状态字段；ReflectorMixin / StateMixin / PlannerMixin 提供协作方法。"""

    _ai_client: Any
    _mcp_client: Any
    _capability_router: Any
    _chain_status: str
    _chain_id: str
    _nodes: dict[str, ChainNode]
    _started_at: str
    _reflection_log: list[dict[str, Any]]

    # 来自 ReflectorMixin
    def _log_reflection(self, node: ChainNode, passed: bool, summary: str, action: str) -> None: ...
    def _request_confirmation_if_needed(self, node: ChainNode) -> dict[str, Any]: ...
    def _reflect(self, node: ChainNode, exec_result: dict[str, Any]) -> dict[str, Any]: ...
    def _handle_execution_failure(self, node: ChainNode, exec_result: dict[str, Any]) -> dict[str, Any]: ...
    # 来自 StateMixin
    def _build_chain_summary(self, plan: AgentChainPlan, outcomes: list[dict[str, Any]]) -> dict[str, Any]: ...
    # 来自 PlannerMixin
    def _replan_node(
        self,
        goal: str,
        failed_node: ChainNode,
        error: str,
        attempt: int,
        context: dict[str, Any] | None = None,
    ) -> ChainNode | None: ...


class RunnerMixin:
    """Mixin providing chain execution and delegation methods.

    Expects self._ai_client, self._mcp_client, self._capability_router,
    self._chain_status, self._chain_id, self._nodes, self._started_at
    set by AgentChainEngine.__init__.

    Collaborates with ReflectorMixin (_request_confirmation_if_needed,
    _reflect, _handle_execution_failure) and StateMixin (_build_chain_summary).
    """

    if TYPE_CHECKING:
        _ai_client: Any
        _mcp_client: Any
        _capability_router: Any
        _chain_status: str
        _chain_id: str
        _nodes: dict[str, ChainNode]
        _started_at: str
        _reflection_log: list[dict[str, Any]]

        # 来自 ReflectorMixin
        def _log_reflection(self, node: ChainNode, passed: bool, summary: str, action: str) -> None: ...
        def _request_confirmation_if_needed(self, node: ChainNode) -> dict[str, Any]: ...
        def _reflect(self, node: ChainNode, exec_result: dict[str, Any]) -> dict[str, Any]: ...
        def _handle_execution_failure(self, node: ChainNode, exec_result: dict[str, Any]) -> dict[str, Any]: ...
        # 来自 StateMixin
        def _build_chain_summary(self, plan: AgentChainPlan, outcomes: list[dict[str, Any]]) -> dict[str, Any]: ...
        # 来自 PlannerMixin
        def _replan_node(
            self,
            goal: str,
            failed_node: ChainNode,
            error: str,
            attempt: int,
            context: dict[str, Any] | None = None,
        ) -> ChainNode | None: ...

    def execute_chain(
        self,
        plan: AgentChainPlan,
        *,
        precheck_callback: Callable[[ChainNode], dict[str, Any]] | None = None,
        execute_callback: Callable[[ChainNode], dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """按拓扑序执行 Agent 链。

        每步执行流程：
        1. 前置条件检查 (precheck)
        2. (如需要) 请求用户确认
        3. 委派执行
        4. 自我反思
        5. 记录反思日志

        P1-3: 串行节点失败时不再立即 break，而是触发重规划循环：
        - 反思返回 ADJUST + needs_replan=True
        - replan_count < MAX_REPLAN_ATTEMPTS → 调用 _replan_node 生成替代节点
        - 重规划节点继承上游结果，重新执行
        - 超过 MAX_REPLAN_ATTEMPTS → 终止链并标记 FAILED
        """
        self._chain_id = uuid.uuid4().hex[:12]
        self._chain_status = CHAIN_STATUS_RUNNING
        self._started_at = datetime.now(timezone.utc).isoformat()
        self._reflection_log = []
        self._nodes = {n.node_id: n for n in plan.nodes}

        event_bus.emit(
            AppEvent(
                "agent.chain.started",
                payload={"chain_id": self._chain_id, "goal": plan.goal, "total_steps": len(plan.nodes)},
            )
        )

        outcomes: list[dict[str, Any]] = []
        all_successful = True
        # P1-2: 维护上游结果映射，供下游节点读取上游输出
        node_results: dict[str, dict[str, Any]] = {}
        # P1-3: 单链重规划计数（按原始节点 ID 累计，避免重规划节点再次失败时无限循环）
        replan_counts: dict[str, int] = {}

        try:
            execution_order = self._topological_sort(plan.nodes)

            for batch in execution_order:
                if self._chain_status in (CHAIN_STATUS_CANCELLED, CHAIN_STATUS_FAILED):
                    break
                if any(n.mode == "parallel" for n in batch):
                    batch_results = self._execute_batch_parallel(
                        batch,
                        precheck_callback,
                        execute_callback,
                        upstream_results=node_results,  # P1-2
                    )
                    outcomes.extend(batch_results)
                    # 并行批次中任一节点失败即终止链（保留原语义）
                    if any(r.get("status") == NODE_STATUS_FAILED for r in batch_results):
                        all_successful = False
                else:
                    for node in batch:
                        # P1-2: 注入上游节点结果到当前节点（运行时属性）
                        node.upstream_results = {  # type: ignore[attr-defined]
                            dep_id: node_results.get(dep_id, {}) for dep_id in node.depends_on if dep_id in node_results
                        }
                        result = self._execute_node_with_lifecycle(
                            node,
                            precheck_callback,
                            execute_callback,
                        )
                        outcomes.append(result)
                        node_results[node.node_id] = result

                        if result.get("status") == NODE_STATUS_FAILED:
                            # P1-3: 检查是否可重规划
                            replan_outcome = self._maybe_replan_node(
                                node=node,
                                result=result,
                                plan=plan,
                                precheck_callback=precheck_callback,
                                execute_callback=execute_callback,
                                node_results=node_results,
                                replan_counts=replan_counts,
                                outcomes=outcomes,
                            )
                            if replan_outcome is not None:
                                # 重规划节点已成功，更新映射并继续
                                node_results[node.node_id] = replan_outcome
                            else:
                                # 重规划不可行或超限 → 终止链
                                all_successful = False
                                break

        except Exception as exc:
            logger.exception("Agent 链执行异常: %s", exc)
            all_successful = False
            self._chain_status = CHAIN_STATUS_FAILED
        finally:
            completed = sum(1 for o in outcomes if o.get("status") == NODE_STATUS_COMPLETED)
            failed = sum(1 for o in outcomes if o.get("status") == NODE_STATUS_FAILED)
            # P1-3: 链状态以 all_successful 为准（重规划成功时 outcomes 中
            # 仍保留原始失败记录用于审计，但不应导致链状态为 FAILED）
            if self._chain_status not in (CHAIN_STATUS_CANCELLED, CHAIN_STATUS_FAILED):
                self._chain_status = CHAIN_STATUS_COMPLETED if all_successful else CHAIN_STATUS_FAILED

            event_bus.emit(
                AppEvent(
                    "agent.chain.completed",
                    payload={
                        "chain_id": self._chain_id,
                        "status": self._chain_status,
                        "total": len(plan.nodes),
                        "completed": completed,
                        "failed": failed,
                        "goal": plan.goal,
                        "replan_count": sum(replan_counts.values()),  # P1-3: 可观测
                    },
                )
            )

        return self._build_chain_summary(plan, outcomes)

    def _maybe_replan_node(
        self,
        *,
        node: ChainNode,
        result: dict[str, Any],
        plan: AgentChainPlan,
        precheck_callback: Callable[[ChainNode], dict[str, Any]] | None,
        execute_callback: Callable[[ChainNode], dict[str, Any]] | None,
        node_results: dict[str, dict[str, Any]],
        replan_counts: dict[str, int],
        outcomes: list[dict[str, Any]],
    ) -> dict[str, Any] | None:
        """P1-3: 节点失败后尝试重规划。

        Returns:
            新节点的执行结果（成功时） / None（不重规划或重规划仍失败）
        """
        reflection = result.get("reflection") or {}
        action = reflection.get("action")
        needs_replan = result.get("needs_replan") or action == "adjust"
        if not needs_replan:
            return None

        # 按原始节点 ID 统计重规划次数（重规划节点的 ID 形如 xxx_replan1）
        base_id = node.node_id.split("_replan")[0]
        current_count = replan_counts.get(base_id, 0)
        if current_count >= MAX_REPLAN_ATTEMPTS:
            logger.warning(
                "P1-3 重规划次数超限 (%d/%d): %s, 终止重规划",
                current_count,
                MAX_REPLAN_ATTEMPTS,
                base_id,
            )
            self._log_reflection(
                node,
                False,
                f"重规划次数超限 ({current_count}/{MAX_REPLAN_ATTEMPTS})",
                "stop",
            )
            return None

        next_attempt = current_count + 1
        replan_counts[base_id] = next_attempt

        error = result.get("error", "未知错误")
        upstream = getattr(node, "upstream_results", {}) or {}
        replan_context = {
            "upstream_results": upstream,
            "previous_error": error,
            "attempt": next_attempt,
        }

        logger.info(
            "P1-3 触发重规划: %s (attempt %d/%d, error=%s)",
            base_id,
            next_attempt,
            MAX_REPLAN_ATTEMPTS,
            error[:80],
        )

        new_node = self._replan_node(
            goal=plan.goal,
            failed_node=node,
            error=error,
            attempt=next_attempt,
            context=replan_context,
        )
        if new_node is None:
            logger.warning("P1-3 重规划未生成新节点: %s", base_id)
            return None

        # 注册新节点并继承上游结果
        self._nodes[new_node.node_id] = new_node
        new_node.upstream_results = upstream  # type: ignore[attr-defined]

        # 重新执行（重规划节点的 retry_count 已重置为 0）
        replan_result = self._execute_node_with_lifecycle(
            new_node,
            precheck_callback,
            execute_callback,
        )
        outcomes.append(replan_result)
        node_results[new_node.node_id] = replan_result

        if replan_result.get("status") == NODE_STATUS_COMPLETED:
            # 重规划成功 → 用新结果覆盖原节点的失败结果
            node_results[node.node_id] = replan_result
            logger.info(
                "P1-3 重规划成功: %s → %s",
                base_id,
                new_node.node_id,
            )
            return replan_result

        # 重规划仍失败 → 递归尝试下一次重规划（直到超限）
        logger.warning(
            "P1-3 重规划仍失败: %s → %s, 继续尝试",
            base_id,
            new_node.node_id,
        )
        return self._maybe_replan_node(
            node=new_node,
            result=replan_result,
            plan=plan,
            precheck_callback=precheck_callback,
            execute_callback=execute_callback,
            node_results=node_results,
            replan_counts=replan_counts,
            outcomes=outcomes,
        )

    def _topological_sort(
        self,
        nodes: list[ChainNode],
    ) -> list[list[ChainNode]]:
        """拓扑排序：返回按执行层级分组的节点列表。"""
        node_ids = {n.node_id for n in nodes}
        in_degree: dict[str, int] = {}
        dependents: dict[str, list[str]] = {}
        missing_deps: list[str] = []

        for n in nodes:
            valid_deps = []
            for dep_id in n.depends_on:
                if dep_id in node_ids:
                    valid_deps.append(dep_id)
                else:
                    missing_deps.append(f"{n.node_id}→{dep_id}")
            in_degree[n.node_id] = len(valid_deps)
            for dep_id in valid_deps:
                if dep_id not in dependents:
                    dependents[dep_id] = []
                dependents[dep_id].append(n.node_id)

        if missing_deps:
            logger.warning("拓扑排序发现缺失依赖: %s", "; ".join(missing_deps))

        queue = [nid for nid, deg in in_degree.items() if deg == 0]
        levels: list[list[ChainNode]] = []
        node_map = {n.node_id: n for n in nodes}
        visited_count = 0

        while queue:
            level_nodes = [node_map[nid] for nid in queue if nid in node_map]
            levels.append(level_nodes)
            visited_count += len(level_nodes)
            next_queue = []
            for nid in queue:
                for dep in dependents.get(nid, []):
                    in_degree[dep] -= 1
                    if in_degree[dep] == 0:
                        next_queue.append(dep)
            queue = next_queue

        if visited_count < len(nodes):
            visited_ids = {n.node_id for level in levels for n in level}
            unvisited = [n.node_id for n in nodes if n.node_id not in visited_ids]
            logger.warning(
                "拓扑排序可能含环路: 已排 %d/%d 节点, 未排: %s",
                visited_count,
                len(nodes),
                unvisited,
            )

        return levels

    def _execute_node_with_lifecycle(
        self,
        node: ChainNode,
        precheck_callback: Callable[[ChainNode], dict[str, Any]] | None = None,
        execute_callback: Callable[[ChainNode], dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """单个节点的完整生命周期执行。"""
        node.status = NODE_STATUS_RUNNING
        node.started_at = datetime.now(timezone.utc).isoformat()
        result: dict[str, Any] = {
            "node_id": node.node_id,
            "description": node.description,
            "status": NODE_STATUS_FAILED,
        }

        event_bus.emit(
            AppEvent(
                "agent.chain.node.started",
                payload={
                    "chain_id": self._chain_id,
                    "node_id": node.node_id,
                    "description": node.description,
                    "action_id": node.action_id,
                    "risk_level": node.risk_level,
                },
            )
        )

        try:
            if precheck_callback is not None:
                precheck = precheck_callback(node)
                if not precheck.get("passed", True):
                    prereq_fail = precheck.get("reason", "前置条件不满足")
                    result["status"] = NODE_STATUS_FAILED
                    result["error"] = prereq_fail
                    result["precheck"] = precheck
                    node.status = NODE_STATUS_FAILED
                    node.error = prereq_fail
                    self._log_reflection(node, False, prereq_fail, "stop")
                    return result
                result["precheck"] = precheck

            confirmation = self._request_confirmation_if_needed(node)
            if confirmation.get("action") == "reject":
                # P1-4: reject 包含用户主动拒绝 + 确认超时默认拒绝
                # 两种情况都应有可见通知（由 _agent_chain_confirm_callback 投递到 inbox）
                reject_reason = confirmation.get("reason", "用户拒绝")
                is_timeout = "超时" in reject_reason or "timeout" in reject_reason.lower()
                result["status"] = NODE_STATUS_FAILED
                result["error"] = reject_reason
                node.status = NODE_STATUS_FAILED
                node.error = reject_reason
                # P1-4: 超时拒绝不取消整条链（允许重规划），用户主动拒绝才取消
                if is_timeout:
                    self._log_reflection(node, False, reject_reason, "adjust")
                else:
                    self._log_reflection(node, False, reject_reason, "stop")
                    self._chain_status = CHAIN_STATUS_CANCELLED
                event_bus.emit(
                    AppEvent(
                        "agent.chain.node.failed",
                        payload={
                            "chain_id": self._chain_id,
                            "node_id": node.node_id,
                            "description": node.description,
                            "error": reject_reason,
                            "confirmation_timeout": is_timeout,  # P1-4: 标记超时拒绝
                        },
                    )
                )
                return result
            # P1-4: 不再有 timeout 分支（超时统一为 reject）
            result["confirmation"] = confirmation

            exec_result = self._execute_node(node, execute_callback)
            result["execution"] = exec_result

            if exec_result.get("success", False):
                node.status = NODE_STATUS_COMPLETED
                node.result = exec_result.get("data", {})
                result["status"] = NODE_STATUS_COMPLETED

                event_bus.emit(
                    AppEvent(
                        "agent.chain.node.completed",
                        payload={"chain_id": self._chain_id, "node_id": node.node_id, "description": node.description},
                    )
                )

                reflection = self._reflect(node, exec_result)
                node.reflection = reflection
                result["reflection"] = reflection

                # P1-3: 执行成功后的 ADJUST 反思只记录，不再触发重规划
                # （成功路径的 ADJUST 表示结果可接受但需后续调整，与失败重规划语义不同）
                if reflection.get("action") == "adjust":
                    result["adjusted"] = True
                    result["adjustment"] = reflection.get("adjustment", {})
                    self._log_reflection(node, True, "已做调整", "adjust")
                else:
                    self._log_reflection(node, True, "执行成功", "continue")
            else:
                failure = self._handle_execution_failure(node, exec_result)
                node.status = failure.get("status", NODE_STATUS_FAILED)
                node.error = failure.get("error", "执行失败")
                result["status"] = node.status
                result["error"] = node.error
                result["reflection"] = failure.get("reflection", {})
                result["retries"] = failure.get("retries", 0)
                # P1-3: 透传 needs_replan 标志，由 execute_chain 决定是否重规划
                if failure.get("needs_replan"):
                    result["needs_replan"] = True
                if failure.get("retry_success"):
                    result["execution"] = failure.get("retry_result", exec_result)

                event_bus.emit(
                    AppEvent(
                        "agent.chain.node.failed",
                        payload={"chain_id": self._chain_id, "node_id": node.node_id, "description": node.description, "error": node.error},
                    )
                )

        except Exception as exc:
            logger.exception("节点执行异常: %s", exc)
            node.status = NODE_STATUS_FAILED
            node.error = str(exc)
            result["status"] = NODE_STATUS_FAILED
            result["error"] = str(exc)
            self._log_reflection(node, False, str(exc), "stop")
            event_bus.emit(
                AppEvent(
                    "agent.chain.node.failed",
                    payload={"chain_id": self._chain_id, "node_id": node.node_id, "description": node.description, "error": str(exc)},
                )
            )

        node.completed_at = datetime.now(timezone.utc).isoformat()
        return result

    def _execute_batch_parallel(
        self,
        nodes: list[ChainNode],
        precheck_callback: Callable[[ChainNode], dict[str, Any]] | None = None,
        execute_callback: Callable[[ChainNode], dict[str, Any]] | None = None,
        *,
        upstream_results: dict[str, dict[str, Any]] | None = None,  # P1-2
    ) -> list[dict[str, Any]]:
        """并行执行一批节点。"""
        results: list[dict[str, Any]] = []
        # P1-2: 并行批次执行前，给每个节点注入其上游结果
        if upstream_results:
            for node in nodes:
                node.upstream_results = {  # type: ignore[attr-defined]
                    dep_id: upstream_results.get(dep_id, {}) for dep_id in node.depends_on if dep_id in upstream_results
                }
        with ThreadPoolExecutor(max_workers=min(len(nodes), 5)) as pool:
            futures = {
                pool.submit(
                    self._execute_node_with_lifecycle,
                    node,
                    precheck_callback,
                    execute_callback,
                ): node
                for node in nodes
            }
            for future in as_completed(futures):
                try:
                    result = future.result(timeout=60)
                    results.append(result)
                except Exception as exc:
                    node = futures[future]
                    results.append(
                        {
                            "node_id": node.node_id,
                            "description": node.description,
                            "status": NODE_STATUS_FAILED,
                            "error": str(exc),
                        }
                    )
        return results

    def _execute_node(
        self,
        node: ChainNode,
        execute_callback: Callable[[ChainNode], dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """根据委派目标执行节点。"""
        if execute_callback is not None:
            return execute_callback(node)

        delegate_map = {
            DELEGATE_LOCAL: self._execute_local,
            DELEGATE_CLOUD: self._execute_cloud,
            DELEGATE_MCP: self._execute_mcp,
            DELEGATE_AUTOMATION: self._execute_automation,
        }
        executor = delegate_map.get(node.delegate_to, self._execute_local)
        return executor(node)

    def _execute_local(self, node: ChainNode) -> dict[str, Any]:
        """委派给本地 AI（低延迟、低风险）。"""
        if self._ai_client is None:
            return {"success": False, "error": "本地 AI 客户端不可用"}
        try:
            # P1-2: 注入上游节点结果到 prompt
            upstream_context = self._format_upstream_context(node)
            system_content = f"你是一个 AI 助手，执行步骤：{node.description}"
            if upstream_context:
                system_content += f"\n\n上游步骤结果：\n{upstream_context}"
            response = self._ai_client.chat_completion(
                messages=[
                    {"role": "system", "content": system_content},
                    {"role": "user", "content": json.dumps(node.payload, ensure_ascii=False) if node.payload else node.description},
                ],
                temperature=0.3,
                max_tokens=1000,
            )
            result = self._extract_llm_response(response)
            return {"success": True, "delegate": DELEGATE_LOCAL, "data": {"text": result, "node_id": node.node_id}}
        except Exception as exc:
            return {"success": False, "delegate": DELEGATE_LOCAL, "error": str(exc)}

    def _execute_cloud(self, node: ChainNode) -> dict[str, Any]:
        """委派给云端 API（高复杂度、高风险）。"""
        if self._ai_client is None:
            return {"success": False, "error": "AI 客户端不可用"}
        try:
            # P1-2: 注入上游节点结果到 prompt
            upstream_context = self._format_upstream_context(node)
            system_content = f"你是一个高级 AI 助手，处理高复杂度任务。\n当前步骤：{node.description}\n请给出详细、高质量的结果。"
            if upstream_context:
                system_content += f"\n\n上游步骤结果：\n{upstream_context}"
            response = self._ai_client.chat_completion(
                messages=[
                    {"role": "system", "content": system_content},
                    {"role": "user", "content": json.dumps(node.payload, ensure_ascii=False) if node.payload else node.description},
                ],
                temperature=0.3,
                max_tokens=2000,
            )
            result = self._extract_llm_response(response)
            return {"success": True, "delegate": DELEGATE_CLOUD, "data": {"text": result, "node_id": node.node_id}}
        except Exception as exc:
            return {"success": False, "delegate": DELEGATE_CLOUD, "error": str(exc)}

    @staticmethod
    def _format_upstream_context(node: ChainNode) -> str:
        """P1-2: 格式化上游节点结果，注入到当前节点的执行上下文。

        从 ``node.upstream_results``（由 ``execute_chain`` 在执行前设置）中
        提取上游节点的输出数据，格式化为可读文本供 LLM 参考。
        """
        upstream = getattr(node, "upstream_results", None)
        if not upstream:
            return ""
        parts: list[str] = []
        for dep_id, result in upstream.items():
            if not isinstance(result, dict):
                continue
            execution = result.get("execution") or {}
            data = execution.get("data") if isinstance(execution, dict) else None
            if data:
                # P1-5: 头尾保留压缩替代硬截断，避免丢失结尾结论
                text = compress_text_head_tail(
                    json.dumps(data, ensure_ascii=False, default=str),
                    max_chars=500,
                )
                parts.append(f"[{dep_id}] {text}")
            elif result.get("error"):
                parts.append(f"[{dep_id}] ERROR: {compress_text_head_tail(str(result['error']), max_chars=200)}")
        return "\n".join(parts)

    def _execute_mcp(self, node: ChainNode) -> dict[str, Any]:
        """委派给 MCP 工具。"""
        if self._mcp_client is None:
            return {"success": False, "error": "MCP 客户端不可用"}
        tool_name = node.action_id
        try:
            result = self._mcp_client.call_tool(tool_name, node.payload)
            return {"success": True, "delegate": DELEGATE_MCP, "data": {"tool": tool_name, "result": result}}
        except Exception as exc:
            return {"success": False, "delegate": DELEGATE_MCP, "error": str(exc)}

    def _execute_automation(self, node: ChainNode) -> dict[str, Any]:
        """委派给自动化规则或预定义流程。"""
        if self._capability_router is None:
            return {"success": False, "error": "能力路由器不可用"}
        try:
            from ..models import PlannedStep

            step = PlannedStep(
                step_id=node.node_id,
                action_id=node.action_id,
                payload=node.payload,
                risk_level=node.risk_level,
            )
            result = self._capability_router.execute(step)
            return {"success": True, "delegate": DELEGATE_AUTOMATION, "data": {"action_id": node.action_id, "result": result}}
        except Exception as exc:
            return {"success": False, "delegate": DELEGATE_AUTOMATION, "error": str(exc)}

    @staticmethod
    def _extract_llm_response(response: Any) -> str:
        """从 LLM 响应中提取文本。"""
        if isinstance(response, dict):
            choices = response.get("choices") or []
            if choices:
                msg = choices[0].get("message") or {}
                return msg.get("content", "")
        elif isinstance(response, str):
            return response
        return ""
