"""AI 路由常量 — 各层共享的执行器与模型标识。

供 ai/client/（本层）和 assistant/（编排层）共同引用，保持依赖方向：
ai/ 层不依赖 assistant/。
"""

ROUTE_EXECUTOR_LOCAL = "local"          # 本地 qwen 执行（简单/隐私任务）
ROUTE_EXECUTOR_DEEPSEEK = "deepseek"    # DeepSeek 执行（复杂任务）
ROUTE_EXECUTOR_MCP = "mcp"              # MCP 工具执行
ROUTE_EXECUTOR_KIRO = "kiro"            # Kiro-gateway Claude 执行（编码/复杂推理）
