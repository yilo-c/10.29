"""Execution 子包 — 能力执行与上下文处理。

- ``execution``：AssistantExecutionLayer 执行层。
- ``action_broker``：ActionBroker 统一能力执行入口闸门。
- ``context_compressor``：对话上下文压缩（token 预算）。
- ``environment``：AssistantEnvironmentProbe 环境探测。

从 assistant/ 顶层迁入（原文件保留 re-export 兼容层）。
"""

from assistant.execution.execution import AssistantExecutionLayer
from services.action_broker import ActionBroker, ActionRequest, ActionResult, ExecutionReceipt
from services.context_compressor import ContextCompressor, compress_context, compress_context_smart
from assistant.execution.environment import AssistantEnvironmentProbe

__all__ = [
    "AssistantExecutionLayer",
    "ActionBroker",
    "ActionRequest",
    "ActionResult",
    "ExecutionReceipt",
    "ContextCompressor",
    "compress_context",
    "compress_context_smart",
    "AssistantEnvironmentProbe",
]
