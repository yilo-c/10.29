"""Multi-provider aggregation path for chat.send (A2A summary)."""
from __future__ import annotations
import logging
from typing import Any, AsyncIterator
logger = logging.getLogger("siyi.chat_paths.aggregate")

async def run_aggregate_path(
    context, message, params, aggregation, *,
    preferred_provider=None, preferred_model=None,
    send_chunk=None, stream=True,
) -> AsyncIterator[str]:
    """Multi-provider vote/merge aggregation."""
    aggregator = getattr(context, "aggregator", None)
    if aggregator is None:
        return
    try:
        strategy = aggregation.get("strategy", "vote")
        result = await aggregator.aggregate(
            [{"role": "user", "content": message}],
            strategy=strategy,
            temperature=params.get("temperature", 0.2),
            max_tokens=params.get("max_tokens", 2000),
        )
        text = result.get("text") or result.get("content") or ""
        if text:
            if send_chunk and stream:
                await send_chunk(text, chunk_type="chunk")
                await send_chunk("", chunk_type="done")
            yield text
    except Exception:
        logger.exception("aggregate path failed")
