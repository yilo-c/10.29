"""思忆集核心功能端到端检测脚本。
测试三大核心功能：
1. AI 对话管道（从消息到 AI 响应）
2. Intent 路由（区分聊天 vs 任务）
3. CC Bridge 任务执行路径（含确认对话框）
"""
from __future__ import annotations

__test__ = False  # standalone runner, not a pytest test file

import sys
import time
