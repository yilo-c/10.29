"""AI pattern-action JSON-RPC handler: pattern.execute.

从原 _ai.py 拆分而来（模式动作域）。
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("siyi.backend.handlers.ai_pattern")


def register_ai_pattern(registry, context) -> None:
    """Register AI pattern-action JSON-RPC methods."""

    # ── pattern.execute ──
    async def pattern_execute(action_id: str = "", text: str = "", **kwargs: Any) -> dict[str, str]:
        """Execute a pattern action."""
        if not action_id or not text:
            return {"result": "", "error": "缺少 action_id 或 text"}
        try:
            from services.pattern_actions import get_registry

            reg = get_registry()
            result = reg.execute(action_id, text)
            return {"result": result}
        except Exception as exc:
            logger.warning("pattern.execute failed: %s", exc)
            return {"result": "", "error": str(exc)}

    registry.register("pattern.execute", pattern_execute)
