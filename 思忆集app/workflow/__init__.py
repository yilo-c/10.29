"""workflow/ — 工作流 DAG 引擎（PyQt6 GUI 已移除，仅保留核心引擎）。

P1.6: 删除 PyQt6 死代码（editor_canvas.py / node_config.py）。
保留 models / execution_engine / repository 三个核心模块。
若未来 Electron 前端需要可视化编辑器，应在 desk-notes/ 用 React 重写，
通过新增 JSON-RPC workflow.* 方法暴露，而非保留 PyQt6 版本。
"""
from workflow.execution_engine import WorkflowExecutionEngine
from workflow.models import (
    ALL_NODE_TYPES,
    NODE_TYPE_AI_CALL,
    NODE_TYPE_CONDITION,
    NODE_TYPE_DELIVERY,
    NODE_TYPE_LABELS,
    NODE_TYPE_TOOL_ACTION,
    NODE_TYPE_TRIGGER,
    Connection,
    Workflow,
    WorkflowNode,
)
from workflow.repository import WorkflowRepository

__all__ = [
    "WorkflowExecutionEngine",
    "WorkflowRepository",
    "Workflow",
    "WorkflowNode",
    "Connection",
    "NODE_TYPE_TRIGGER",
    "NODE_TYPE_AI_CALL",
    "NODE_TYPE_TOOL_ACTION",
    "NODE_TYPE_CONDITION",
    "NODE_TYPE_DELIVERY",
    "ALL_NODE_TYPES",
    "NODE_TYPE_LABELS",
]
