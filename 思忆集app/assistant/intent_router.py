# 兼容层：intent_router 已迁入 assistant/routing/。
# 外部 from assistant.intent_router import ... 仍可用。
from assistant.routing.intent_router import *  # noqa: F401,F403
