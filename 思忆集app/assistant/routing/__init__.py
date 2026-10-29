"""Routing 子包 — 意图路由与调度。

- ``intent_router``：意图分类（chat/execute/automate）与统一入口。
- ``judge``：JudgeScheduler 调度（DeepSeek 决策 + 本地 qwen 执行 + MCP）。
- ``judge_decision`` / ``judge_supertag`` / ``privacy_classifier``：judge 的辅助决策/标签/隐私分类。

从 assistant/ 顶层迁入（原文件保留 re-export 兼容层）。
"""
from assistant.routing.intent_router import AssistantIntentRouter, IntentClassification
from assistant.routing.judge import JudgeScheduler
from assistant.routing.judge_decision import JudgeDecision, extract_json_block, JUDGE_SYSTEM_PROMPT
from assistant.routing.judge_supertag import suggest_supertag
from assistant.routing.privacy_classifier import PrivacyClassifier, PrivacyLevel

__all__ = [
    "AssistantIntentRouter", "IntentClassification", "JudgeScheduler",
    "JudgeDecision", "extract_json_block", "JUDGE_SYSTEM_PROMPT",
    "suggest_supertag", "PrivacyClassifier", "PrivacyLevel",
]
