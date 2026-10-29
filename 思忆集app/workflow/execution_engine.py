"""工作流执行引擎 — 将 Workflow DAG 转为 AgentChainPlan 并执行。

支持两种执行模式：
1. 同步模式（run_workflow）：串行执行所有节点，向后兼容
2. 异步模式（run_workflow_async）：拓扑排序 + 并行执行无依赖节点

条件节点支持真正的分支路由：根据表达式求值结果选择 true/false 路径，
未匹配的分支会被跳过（其下游节点若没有其他可达路径也会被跳过）。
"""
from __future__ import annotations

import ast
import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import Any

from assistant.models import ChainNode, AgentChainPlan
from workflow.models import (
    NODE_TYPE_AI_CALL,
    NODE_TYPE_CONDITION,
    NODE_TYPE_DELIVERY,
    NODE_TYPE_TOOL_ACTION,
    NODE_TYPE_TRIGGER,
    Workflow,
)

logger = logging.getLogger("siyi.workflow.execution")

# P0 修复：AST 白名单求值器，替代 eval()，彻底杜绝反射逃逸
# 仅允许以下 AST 节点类型：常量、名称、调用、比较、布尔运算、二元运算、一元运算
_SAFE_FUNC_NAMES = frozenset({
    "len", "str", "int", "float", "bool", "abs", "min", "max", "sum",
})


class _SafeExprEvaluator(ast.NodeVisitor):
    """安全表达式求值器：白名单遍历 AST，拒绝任何危险节点。"""

    def __init__(self, variables: dict[str, Any]):
        self._vars = variables

    def evaluate(self, expr: str) -> Any:
        tree = ast.parse(expr, mode="eval")
        return self._eval(tree.body)

    def _eval(self, node: ast.AST) -> Any:
        if isinstance(node, ast.Constant):
            return node.value
        if isinstance(node, ast.Name):
            if node.id in self._vars:
                return self._vars[node.id]
            # P0 修复：禁止访问 dunder 名称
            if node.id.startswith("__") and node.id.endswith("__"):
                raise ValueError(f"禁止访问系统属性: {node.id}")
            raise NameError(f"名称 '{node.id}' 未定义")
        if isinstance(node, ast.Subscript):
            value = self._eval(node.value)
            key = self._eval(node.slice)
            return value[key]
        if isinstance(node, ast.Attribute):
            # P0 修复：禁止属性访问，阻断反射逃逸链
            raise ValueError(f"禁止属性访问: {ast.dump(node)}")
        if isinstance(node, ast.BoolOp):
            values = [self._eval(v) for v in node.values]
            if isinstance(node.op, ast.And):
                result = True
                for v in values:
                    if not v:
                        return v
                    result = v
                return result
            else:  # Or
                for v in values:
                    if v:
                        return v
                return values[-1] if values else False
        if isinstance(node, ast.Compare):
            left = self._eval(node.left)
            for op, comp in zip(node.ops, node.comparators):
                right = self._eval(comp)
                if not self._apply_compare(op, left, right):
                    return False
                left = right
            return True
        if isinstance(node, ast.BinOp):
            left = self._eval(node.left)
            right = self._eval(node.right)
            return self._apply_binop(node.op, left, right)
        if isinstance(node, ast.UnaryOp):
            operand = self._eval(node.operand)
            if isinstance(node.op, ast.Not):
                return not operand
            if isinstance(node.op, ast.USub):
                return -operand
            if isinstance(node.op, ast.UAdd):
                return +operand
            raise ValueError(f"不支持的一元运算: {type(node.op).__name__}")
        if isinstance(node, ast.Call):
            if not isinstance(node.func, ast.Name):
                raise ValueError("禁止调用非常量函数")
            func_name = node.func.id
            if func_name not in _SAFE_FUNC_NAMES:
                raise ValueError(f"禁止调用函数: {func_name}")
            args = [self._eval(a) for a in node.args]
            if node.keywords:
                raise ValueError("不支持关键字参数")
            func = self._vars.get(func_name)
            if func is None:
                raise ValueError(f"函数未就绪: {func_name}")
            return func(*args)
        raise ValueError(f"不允许的 AST 节点: {type(node).__name__}")

    @staticmethod
    def _apply_compare(op: ast.cmpop, left: Any, right: Any) -> bool:
        if isinstance(op, ast.Eq):
            return left == right
        if isinstance(op, ast.NotEq):
            return left != right
        if isinstance(op, ast.Lt):
            return left < right
        if isinstance(op, ast.LtE):
            return left <= right
        if isinstance(op, ast.Gt):
            return left > right
        if isinstance(op, ast.GtE):
            return left >= right
        if isinstance(op, ast.In):
            return left in right
        if isinstance(op, ast.NotIn):
            return left not in right
        raise ValueError(f"不支持的比较运算: {type(op).__name__}")

    @staticmethod
    def _apply_binop(op: ast.operator, left: Any, right: Any) -> Any:
        if isinstance(op, ast.Add):
            return left + right
        if isinstance(op, ast.Sub):
            return left - right
        if isinstance(op, ast.Mult):
            return left * right
        if isinstance(op, ast.Div):
            return left / right
        if isinstance(op, ast.Mod):
            return left % right
        raise ValueError(f"不支持的二元运算: {type(op).__name__}")


def _safe_eval(expr: str, variables: dict[str, Any]) -> Any:
    """安全求值表达式，仅允许白名单 AST 节点。"""
    try:
        return _SafeExprEvaluator(variables).evaluate(expr)
    except SyntaxError as e:
        # 语句（如 import x）在 eval 模式下会抛 SyntaxError
        raise ValueError(f"禁止的语法: {e.msg}") from e


class WorkflowExecutionEngine:
    """将可视化工作流映射到 AgentChainEngine 执行。"""

    def __init__(self, agent_chain=None, capability_router=None):
        self._agent_chain = agent_chain
        self._capability_router = capability_router

    def run_workflow(self, workflow: Workflow) -> dict[str, Any]:
        ok, msg = workflow.validate_dag()
        if not ok:
            return {"status": "error", "error": msg}

        plan = self._build_chain_plan(workflow)
        logger.info("执行工作流: %s (%d nodes)", workflow.name, len(plan.nodes))

        if self._agent_chain is not None:
            return self._agent_chain.execute_chain(plan)
        else:
            # 无引擎时退化为直接调用 capability router
            return self._execute_direct(workflow, plan)

    async def run_workflow_async(self, workflow: Workflow) -> dict[str, Any]:
        """异步执行工作流，支持并行执行和条件分支。

        与 run_workflow 的区别：
        1. 拓扑排序后，对入度为 0 的节点用 asyncio.gather 并行执行
        2. 条件节点根据表达式求值结果选择 true/false 分支
        3. 不可达的节点（被条件分支排除）会被跳过

        Returns:
            与 run_workflow 相同格式的结果字典
        """
        ok, msg = workflow.validate_dag()
        if not ok:
            return {"status": "error", "error": msg}

        plan = self._build_chain_plan(workflow)
        logger.info("异步执行工作流: %s (%d nodes)", workflow.name, len(plan.nodes))

        if self._agent_chain is not None and hasattr(self._agent_chain, "execute_chain_async"):
            return await self._agent_chain.execute_chain_async(plan)
        else:
            return await self._execute_direct_async(workflow, plan)

    def _build_chain_plan(self, workflow: Workflow) -> AgentChainPlan:
        """将 Workflow (可视化 DAG) 转为 AgentChainPlan (执行 DAG)。"""
        nodes: list[ChainNode] = []
        id_map: dict[str, str] = {}  # workflow_node_id -> chain_node_id

        for wf_node in workflow.nodes:
            if not wf_node.enabled:
                continue
            if wf_node.node_type == NODE_TYPE_TRIGGER:
                continue  # trigger 不转为执行节点

            chain_id = f"wf_{wf_node.node_id[:8]}"
            id_map[wf_node.node_id] = chain_id
            chain_node = self._wf_node_to_chain(wf_node, chain_id)
            nodes.append(chain_node)

        # 映射连接为 depends_on
        for conn in workflow.connections:
            src_wf_id = conn.source_node_id
            tgt_wf_id = conn.target_node_id
            src_chain_id = id_map.get(src_wf_id)
            tgt_chain_id = id_map.get(tgt_wf_id)
            if src_chain_id and tgt_chain_id:
                for node in nodes:
                    if node.node_id == tgt_chain_id:
                        if src_chain_id not in node.depends_on:
                            node.depends_on.append(src_chain_id)
                        break

        plan = AgentChainPlan(
            goal=f"执行工作流: {workflow.name}",
            nodes=nodes,
        )
        plan.created_at = datetime.now(timezone.utc).isoformat()
        return plan

    def _wf_node_to_chain(self, wf_node, chain_id: str) -> ChainNode:
        """将 WorkflowNode 转为 ChainNode。"""
        cfg = wf_node.config
        base = ChainNode(
            node_id=chain_id,
            description=wf_node.label or wf_node.node_type,
            risk_level=wf_node.risk_level,
        )

        if wf_node.node_type == NODE_TYPE_AI_CALL:
            base.action_id = "reasoning"
            base.delegate_to = "cloud"
            base.payload = {
                "system_prompt": cfg.get("system_prompt", ""),
                "temperature": cfg.get("temperature", 0.7),
            }
            if cfg.get("model_override"):
                base.payload["model"] = cfg["model_override"]

        elif wf_node.node_type == NODE_TYPE_TOOL_ACTION:
            base.action_id = cfg.get("action_id", "")
            base.delegate_to = "automation"
            base.payload = dict(cfg.get("action_payload", {}) if isinstance(cfg.get("action_payload"), dict) else {})

        elif wf_node.node_type == NODE_TYPE_CONDITION:
            base.action_id = "condition"
            base.delegate_to = "local"
            base.payload = {
                "expression": cfg.get("expression", ""),
            }

        elif wf_node.node_type == NODE_TYPE_DELIVERY:
            base.action_id = "deliver"
            base.delegate_to = "automation"
            base.payload = {
                "channel": cfg.get("channel", "inbox"),
                "message_template": cfg.get("message_template", ""),
            }

        return base

    def _execute_direct(self, workflow: Workflow, plan: AgentChainPlan) -> dict[str, Any]:
        """直接通过 capability router 执行（无 AgentChainEngine 时的回退）。

        同步串行执行，向后兼容。条件节点仅记录表达式但不做分支路由
        （分支路由需要异步模式 run_workflow_async）。
        """
        results: dict[str, Any] = {"workflow_id": workflow.workflow_id, "steps": [], "status": "completed"}

        for chain_node in plan.nodes:
            step_result = {"node_id": chain_node.node_id, "action": chain_node.action_id, "status": "skipped"}

            if self._capability_router and chain_node.action_id:
                try:
                    resp = self._capability_router.route_action(
                        chain_node.action_id,
                        chain_node.payload,
                        source="workflow",
                    )
                    step_result["status"] = "completed"
                    step_result["output"] = resp
                except Exception as e:
                    step_result["status"] = "error"
                    step_result["error"] = str(e)
                    results["status"] = "partial_failure"

            results["steps"].append(step_result)

        return results

    async def _execute_direct_async(
        self, workflow: Workflow, plan: AgentChainPlan
    ) -> dict[str, Any]:
        """异步执行：拓扑排序 + 并行 + 条件分支。

        执行流程：
        1. 构建依赖图（节点 ID → 依赖列表 + 后继列表）
        2. 拓扑排序，每轮收集入度为 0 的节点
        3. 对同一轮的节点用 asyncio.gather 并行执行
        4. 条件节点求值后，标记未选中分支的下游节点为"跳过"
        5. 被跳过的节点不会执行，其下游若仅依赖被跳过节点也会被跳过
        """
        results: dict[str, Any] = {
            "workflow_id": workflow.workflow_id,
            "steps": [],
            "status": "completed",
        }

        # 构建节点查找表
        node_map: dict[str, ChainNode] = {n.node_id: n for n in plan.nodes}

        # 构建依赖图：node_id → (前驱集合, 后继列表)
        # 后继列表包含分支标记：(target_id, branch_label)
        # branch_label: None（普通连接）/ "true" / "false"
        predecessors: dict[str, set[str]] = {n.node_id: set() for n in plan.nodes}
        successors: dict[str, list[tuple[str, str | None]]] = {n.node_id: [] for n in plan.nodes}

        # 从 workflow.connections 提取分支标签
        # Connection 模型可能有 branch/label/port 字段标记 true/false
        wf_conn_branches: dict[tuple[str, str], str | None] = {}
        for conn in workflow.connections:
            # 尝试从 conn 的属性中提取分支标签
            branch = None
            for attr in ("branch", "label", "port", "condition_branch"):
                val = getattr(conn, attr, None) or (conn.config or {}).get(attr) if hasattr(conn, "config") else None
                if val:
                    branch = str(val).lower()
                    break
            # 通过 source_port 推断分支
            if branch is None:
                src_port = getattr(conn, "source_port", None) or ""
                if src_port:
                    src_port_lower = str(src_port).lower()
                    if src_port_lower in ("true", "yes", "1", "if_true"):
                        branch = "true"
                    elif src_port_lower in ("false", "no", "0", "if_false"):
                        branch = "false"
            wf_conn_branches[(conn.source_node_id, conn.target_node_id)] = branch

        # 填充依赖图（使用 chain_id）
        wf_to_chain: dict[str, str] = {}
        for wf_node in workflow.nodes:
            chain_id = f"wf_{wf_node.node_id[:8]}"
            wf_to_chain[wf_node.node_id] = chain_id

        for conn in workflow.connections:
            src_chain = wf_to_chain.get(conn.source_node_id)
            tgt_chain = wf_to_chain.get(conn.target_node_id)
            if src_chain and tgt_chain and src_chain in node_map and tgt_chain in node_map:
                branch = wf_conn_branches.get((conn.source_node_id, conn.target_node_id))
                successors[src_chain].append((tgt_chain, branch))
                predecessors[tgt_chain].add(src_chain)

        # 已完成节点的输出（用于条件求值和数据传递）
        node_outputs: dict[str, Any] = {}
        # 被跳过的节点集合（条件分支未选中）
        skipped_nodes: set[str] = set()
        # 已执行的节点
        executed: set[str] = set()

        # 拓扑排序 + 分轮并行执行
        remaining = set(node_map.keys())

        while remaining:
            # 收集当前可执行的节点：所有前驱已执行或被跳过
            ready: list[str] = []
            for node_id in list(remaining):
                preds = predecessors[node_id]
                # 如果所有前驱都已处理（执行或跳过），则当前节点可处理
                if preds.issubset(executed | skipped_nodes):
                    # 检查是否应该跳过：如果所有前驱都被跳过，则跳过
                    if preds and preds.issubset(skipped_nodes):
                        skipped_nodes.add(node_id)
                        remaining.discard(node_id)
                        results["steps"].append({
                            "node_id": node_id,
                            "action": node_map[node_id].action_id,
                            "status": "skipped",
                            "reason": "all predecessors skipped",
                        })
                    else:
                        ready.append(node_id)

            if not ready:
                # 没有就绪节点但还有剩余 → 可能是环或全部被跳过
                for node_id in list(remaining):
                    if node_id not in skipped_nodes:
                        skipped_nodes.add(node_id)
                        results["steps"].append({
                            "node_id": node_id,
                            "action": node_map[node_id].action_id,
                            "status": "skipped",
                            "reason": "unreachable",
                        })
                remaining.clear()
                break

            # 并行执行就绪节点
            tasks = [self._execute_node_async(node_map[nid], node_outputs) for nid in ready]
            step_results = await asyncio.gather(*tasks, return_exceptions=True)

            for nid, sr in zip(ready, step_results):
                remaining.discard(nid)
                executed.add(nid)

                if isinstance(sr, BaseException):
                    results["steps"].append({
                        "node_id": nid,
                        "action": node_map[nid].action_id,
                        "status": "error",
                        "error": str(sr),
                    })
                    results["status"] = "partial_failure"
                    # 出错节点视为"已处理"但无输出
                    node_outputs[nid] = None
                    continue

                results["steps"].append(sr)
                node_outputs[nid] = sr.get("output")

                # 条件节点：求值并标记未选中分支的后继为跳过
                if sr.get("action") == "condition":
                    condition_result = sr.get("output", {})
                    taken_branch = "true" if condition_result.get("result") else "false"
                    not_taken = "false" if taken_branch == "true" else "true"

                    # 标记未选中分支的直接后继为跳过
                    for succ_id, branch in successors[nid]:
                        if branch == not_taken:
                            skipped_nodes.add(succ_id)
                            # 递归标记其后继（仅依赖此分支的）
                            self._mark_downstream_skipped(
                                succ_id, successors, predecessors, skipped_nodes, executed
                            )

        return results

    def _mark_downstream_skipped(
        self,
        node_id: str,
        successors: dict[str, list[tuple[str, str | None]]],
        predecessors: dict[str, set[str]],
        skipped: set[str],
        executed: set[str],
    ) -> None:
        """递归标记被跳过节点的下游（仅依赖被跳过节点的）为跳过。"""
        for succ_id, _ in successors.get(node_id, []):
            if succ_id in skipped or succ_id in executed:
                continue
            # 如果该后继的所有前驱都已跳过，则也跳过
            preds = predecessors[succ_id]
            if preds.issubset(skipped):
                skipped.add(succ_id)
                self._mark_downstream_skipped(succ_id, successors, predecessors, skipped, executed)

    async def _execute_node_async(
        self, chain_node: ChainNode, context_outputs: dict[str, Any]
    ) -> dict[str, Any]:
        """异步执行单个节点。

        对于条件节点，求值表达式并返回 {"result": bool}。
        对于其他节点，调用 capability_router（在线程中执行以避免阻塞）。
        """
        step_result: dict[str, Any] = {
            "node_id": chain_node.node_id,
            "action": chain_node.action_id,
            "status": "skipped",
        }

        if chain_node.action_id == "condition":
            # 条件节点：本地求值
            expression = chain_node.payload.get("expression", "")
            try:
                result = self._evaluate_condition(expression, context_outputs)
                step_result["status"] = "completed"
                step_result["output"] = {"result": bool(result), "expression": expression}
            except Exception as e:
                step_result["status"] = "error"
                step_result["error"] = f"条件求值失败: {e}"
                step_result["output"] = {"result": False, "expression": expression}
            return step_result

        if self._capability_router and chain_node.action_id:
            try:
                # 在线程中执行同步的 route_action，避免阻塞事件循环
                resp = await asyncio.to_thread(
                    self._capability_router.route_action,
                    chain_node.action_id,
                    chain_node.payload,
                    "workflow",
                )
                step_result["status"] = "completed"
                step_result["output"] = resp
            except Exception as e:
                step_result["status"] = "error"
                step_result["error"] = str(e)
        else:
            step_result["status"] = "skipped"
            step_result["reason"] = "no capability_router or action_id"

        return step_result

    def _evaluate_condition(
        self, expression: str, context_outputs: dict[str, Any]
    ) -> bool:
        """安全求值条件表达式。

        支持的表达式格式：
        - "True" / "False" — 字面量
        - "node_id.output.field == 'value'" — 引用上游节点输出
        - "len(node_id.output) > 0" — 简单函数调用
        - "a > b and b > c" — 布尔组合

        安全限制：
        - 仅允许字母、数字、下划线、点号、引号、比较运算符、逻辑运算符
        - 禁止 import、exec、eval、__、open 等危险操作
        - 使用 AST 白名单求值器，彻底杜绝反射逃逸
        """
        if not expression or not expression.strip():
            return True  # 空表达式默认为 True

        expr = expression.strip()

        # 构建求值上下文：将上游节点输出注入为变量
        # 变量名格式：node_id（点号替换为下划线）
        eval_locals: dict[str, Any] = {
            "True": True, "False": False, "None": None,
            "len": len, "str": str, "int": int, "float": float, "bool": bool,
            "abs": abs, "min": min, "max": max, "sum": sum,
        }

        # 注入上游输出（变量名 = 节点 ID 中的点号替换为下划线）
        for node_id, output in context_outputs.items():
            var_name = node_id.replace(".", "_").replace("-", "_")
            # P0 修复：深度快照剥离对象方法，防止通过反射链逃逸
            try:
                eval_locals[var_name] = json.loads(json.dumps(output, default=str))
            except (TypeError, ValueError):
                eval_locals[var_name] = str(output)

        try:
            result = _safe_eval(expr, eval_locals)
            return bool(result)
        except (ValueError, NameError):
            # Review fix #5: 安全违规(ValueError)和未定义变量(NameError)
            # 都必须向上传播，让调用方标记节点为 error，
            # 而非被下面的 except Exception 静默吞掉返回 False
            raise
        except Exception as e:
            logger.warning("条件表达式求值失败: %s → %s", expr, e)
            return False
