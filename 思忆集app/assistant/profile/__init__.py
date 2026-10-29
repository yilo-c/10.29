"""Profile 子包 — 用户画像与提示词上下文。

- ``user_profile.UserProfileService``：分层偏好模型，驱动推理/自适应/主动性。
- ``prompt_context.PromptContext``：对话上下文压缩与提示组装。

从 assistant/ 顶层迁入（原 user_profile.py / prompt_context.py 保留 re-export 兼容层）。
"""
from assistant.profile.user_profile import UserProfileService, DEFAULT_PROFILE
from assistant.profile.prompt_context import PromptContext

__all__ = ["UserProfileService", "DEFAULT_PROFILE", "PromptContext"]
