"""AI-related JSON-RPC handlers（薄适配层）。

原实现已按 4 个域拆分为子模块：
  - ``_ai_health.py``    → ping / model.list（健康检查 + 模型发现）
  - ``_ai_translate.py`` → ai.parse_intent / translate.* / translations.*（意图与翻译）
  - ``_ai_pattern.py``   → pattern.execute（模式动作）
  - ``_ai_usage.py``     → usage.* / aggregation.*（用量与成本可观测）

本文件仅保留 ``register_ai`` 作为统一装配入口，把注册职责下推到各子模块，
不再保留任何实现逻辑。所有现有调用方（method_registry 装配、测试里
``from backend.handlers._ai import register_ai``）行为完全不受影响。
"""

from __future__ import annotations

from backend.handlers._ai_health import register_ai_health
from backend.handlers._ai_translate import register_ai_translate
from backend.handlers._ai_pattern import register_ai_pattern
from backend.handlers._ai_usage import register_ai_usage


def register_ai(registry, context) -> None:
    """Register AI-related JSON-RPC methods."""
    register_ai_health(registry, context)
    register_ai_translate(registry, context)
    register_ai_pattern(registry, context)
    register_ai_usage(registry, context)
