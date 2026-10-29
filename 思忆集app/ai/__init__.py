"""ai 子包 —— 包入口（M5 清理版）。

历史形态（2026-08-24 前）：本文件是 307 行 "auto-generated re-exports" 聚合，
从 ~50 个模块 try/except re-export 全部符号。全仓审计（M5）确认该聚合是死代码：

- **零消费**：没有任何生产/测试代码通过 ``from ai import <符号>`` 或
  ``ai.<符号>`` 消费 re-export 的名字 —— 消费一律走子模块导入
  （``from ai.client.base import ...`` / ``from ai.circuit_breaker import ...``
  / ``from ai import usage_stats``，后者由 Python 子模块机制自动完成）。
- **部分坏死**：``assistant.character_runtime`` 的 SINK_* / OutputSink 等名字
  在源模块不存在，re-export 运行时被 try/except 静默吞掉 —— 是坏死的公共 API。
- **纯副作用**：``import ai`` 曾因该聚合拖入 ~50 个模块；``from services.ai_service
  import *`` 还引入循环导入隐患（services.ai_service 又依赖 ai.client.*）。

处置：删除全部死 re-export，包 API 一律经子模块访问（与全仓既有用法一致）。
mypy 病灶：该文件 52 个 [attr-defined]/[no-redef] 错误随之清零。
"""
from __future__ import annotations

import logging

_log = logging.getLogger("siyi.ai.pkg")

# NOTE(P3-12): assistant.chat_ui 是 PyQt6 widget，被 ui/ai_chat_page.py 直接
# import，不得出现在本包 re-export 中（否则 headless backend 会透传拉起 PyQt6）。
# 本包不再提供任何 re-export —— 一律通过 ai.<submodule> 访问。
