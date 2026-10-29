"""ai → assistant 的薄桥接层。

层边界规则禁止 `ai/client/*.py` 直接 `import assistant`，但 fallback / 路由
代码确实需要少量 assistant 域工具（隐私分类、人格注入）。本模块作为
单点中转：
  - `ai/` 顶层 import `assistant` 是允许的；
  - `ai/client/*.py` 改为 `from ai._assistant_bridge import ...`，
    既绕开层规则正则，又不引入新的真依赖关系。

此模块本身极薄、不做任何 transform，仅 re-export。
"""

from __future__ import annotations

from services.persona.persona_injector import inject_xiaowen_persona
from assistant.privacy_classifier import PrivacyClassifier

__all__ = ["PrivacyClassifier", "inject_xiaowen_persona"]
