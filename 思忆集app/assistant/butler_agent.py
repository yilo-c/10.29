# 兼容层：butler_agent 已迁入 assistant/agents/。
# 外部 from assistant.butler_agent import ... 仍可用。
# 显式补私有符号（tests 会直接访问 _agent_cache / _pending_contexts）。
from assistant.agents.butler_agent import *  # noqa: F401,F403
from assistant.agents.butler_agent import (  # noqa: F401
    _agent_cache,
    _pending_contexts,
    _resolve_capability_router,
    invalidate_butler_agent_cache,
)
