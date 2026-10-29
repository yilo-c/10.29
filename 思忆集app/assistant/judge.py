# 兼容层：judge 已迁入 assistant/routing/。
# 显式补私有符号（tests 直接 import）。
from assistant.routing.judge import *  # noqa: F401,F403
from assistant.routing.judge import (  # noqa: F401
    _is_short_translation,
    _resolve_local_translate_max_chars,
)
