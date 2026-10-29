"""Cross-session memory recall for chat.send context enrichment."""
from __future__ import annotations
import logging
from typing import Any
logger = logging.getLogger("siyi.chat_paths.cross_session")

async def recall_cross_session(context, message: str, limit: int = 3) -> list[dict[str, Any]]:
    memory = getattr(context, "memory_index_service", None)
    if memory is None:
        return []
    try:
        results = await memory.search(message, limit=limit)
        return results or []
    except Exception:
        logger.debug("cross-session recall failed", exc_info=True)
        return []
