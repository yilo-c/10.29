"""小问人格系统 — 数字生命伴侣的人格定义与注入。

设计文档：docs/companion-agent-design.md
"""

from __future__ import annotations

from .persona_definition import XIAOWEN_PERSONA, XIAOWEN_SYSTEM_PROMPT_TEMPLATE
from .persona_injector import inject_xiaowen_persona, build_persona_system_prompt

__all__ = [
    "XIAOWEN_PERSONA",
    "XIAOWEN_SYSTEM_PROMPT_TEMPLATE",
    "inject_xiaowen_persona",
    "build_persona_system_prompt",
]
