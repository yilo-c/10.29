"""工作流编辑器数据模型。

定义 WorkflowNode, Connection, Workflow 三个核心 dataclass，
全部支持 JSON 序列化。

用法：
    wf = Workflow(name="每日简报")
    node = WorkflowNode(node_type="trigger", label="定时触发",
                        config={"cron_expr": "0 9 * * 1-5"})
    wf.nodes.append(node)
    data = wf.to_dict()
    wf2 = Workflow.from_dict(data)
"""
from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any

# ── 节点类型常量 ─────────────────────────────────────────────────

NODE_TYPE_TRIGGER = "trigger"
NODE_TYPE_AI_CALL = "ai_call"
NODE_TYPE_TOOL_ACTION = "tool_action"
NODE_TYPE_CONDITION = "condition"
NODE_TYPE_DELIVERY = "delivery"

ALL_NODE_TYPES = (
    NODE_TYPE_TRIGGER,
    NODE_TYPE_AI_CALL,
    NODE_TYPE_TOOL_ACTION,
    NODE_TYPE_CONDITION,
    NODE_TYPE_DELIVERY,
)

NODE_TYPE_LABELS = {
    NODE_TYPE_TRIGGER: "定时触发",
    NODE_TYPE_AI_CALL: "AI 调用",
    NODE_TYPE_TOOL_ACTION: "工具动作",
    NODE_TYPE_CONDITION: "条件判断",
    NODE_TYPE_DELIVERY: "结果投递",
}

# ── 数据模型 ─────────────────────────────────────────────────────


@dataclass
class WorkflowNode:
    """工作流中的一个节点。

    node_type 决定 config 字段的 schema：
        trigger:     cron_expr(str), run_once(bool), run_at(str, ISO)
        ai_call:     system_prompt(str), model_override(str), temperature(float)
        tool_action: action_id(str), action_payload(dict)
        condition:   expression(str)
        delivery:    channel(str), message_template(str)
    """
    node_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    node_type: str = NODE_TYPE_TRIGGER
    label: str = ""
    pos_x: float = 0.0
    pos_y: float = 0.0
    config: dict[str, Any] = field(default_factory=dict)
    risk_level: str = "low"
    enabled: bool = True

    def __post_init__(self) -> None:
        if not self.label:
            self.label = NODE_TYPE_LABELS.get(self.node_type, self.node_type)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> WorkflowNode:
        return cls(**data)


@dataclass
class Connection:
    """节点之间的连线。"""
    conn_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    source_node_id: str = ""
    target_node_id: str = ""
    source_port: str = "out"
    target_port: str = "in"
    label: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Connection:
        return cls(**data)


@dataclass
class Workflow:
    """一个完整的自动化工作流定义。"""
    workflow_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    name: str = ""
    description: str = ""
    nodes: list[WorkflowNode] = field(default_factory=list)
    connections: list[Connection] = field(default_factory=list)
    enabled: bool = True
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def touch(self) -> None:
        self.updated_at = datetime.now(timezone.utc).isoformat()

    def get_node(self, node_id: str) -> WorkflowNode | None:
        for n in self.nodes:
            if n.node_id == node_id:
                return n
        return None

    def get_connections_for_node(self, node_id: str) -> list[Connection]:
        return [c for c in self.connections if c.source_node_id == node_id or c.target_node_id == node_id]

    def validate_dag(self) -> tuple[bool, str]:
        """检查是否有环（拓扑排序验证）。"""
        in_degree: dict[str, int] = {n.node_id: 0 for n in self.nodes}
        adj: dict[str, list[str]] = {n.node_id: [] for n in self.nodes}

        for conn in self.connections:
            if conn.source_node_id in adj and conn.target_node_id in in_degree:
                adj[conn.source_node_id].append(conn.target_node_id)
                in_degree[conn.target_node_id] += 1

        queue = [nid for nid, deg in in_degree.items() if deg == 0]
        visited = 0

        while queue:
            nid = queue.pop(0)
            visited += 1
            for neighbor in adj.get(nid, []):
                in_degree[neighbor] -= 1
                if in_degree[neighbor] == 0:
                    queue.append(neighbor)

        if visited != len(self.nodes):
            return False, "工作流包含循环依赖"
        return True, "ok"

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["nodes"] = [n.to_dict() for n in self.nodes]
        d["connections"] = [c.to_dict() for c in self.connections]
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Workflow:
        nodes = [WorkflowNode.from_dict(n) for n in data.pop("nodes", [])]
        connections = [Connection.from_dict(c) for c in data.pop("connections", [])]
        wf = cls(**data)
        wf.nodes = nodes
        wf.connections = connections
        return wf
