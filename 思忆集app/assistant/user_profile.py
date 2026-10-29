# 兼容层：user_profile 已迁入 assistant/profile/。
# 外部调用方（ai/__init__.py、app_runtime/*、memory/context.py、tests）从旧路径
# import 的 UserProfileService / DEFAULT_PROFILE / logger 仍可用。
from assistant.profile.user_profile import (
    DEFAULT_PROFILE,
    UserProfileService,
    logger,
)
