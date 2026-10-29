# 兼容层：cc_bridge 已迁入 assistant/bridging/。保留此 re-export 以免破坏
# 外部 ``from assistant.cc_bridge import ...`` 调用方（ai/__init__.py、
# app_runtime/_context_builder.py、orchestration/drivers/*）。
from assistant.bridging.cc_bridge import (
    CCStatus,
    CCResult,
    CCBridge,
    _TaskHandle,
    _gen_task_id,
    logger,
)
