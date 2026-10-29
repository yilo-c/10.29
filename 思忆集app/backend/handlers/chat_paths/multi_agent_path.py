"""Multi-Agent path — stub, falls through to next path."""
from __future__ import annotations
from typing import Any, AsyncIterator

async def run_multi_agent_path(context, message, params, send_chunk) -> AsyncIterator[str]:
    """Not yet wired — return empty to let chat fall through."""
    return
    yield  # pragma: no cover — makes this an async generator
