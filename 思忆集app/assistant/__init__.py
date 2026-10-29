"""Lazy-loaded re-exports for assistant subpackage.

Modules are loaded on first access to avoid circular imports
(core.__init__ -> app_context -> assistant.* -> core).
"""

import importlib as _importlib

_MODULE_NAMES = [
    "action_dispatcher",
    "agent_chain",
    "at_reference",
    "autonomy",
    "budget_manager",
    "capability",
    "capability_router",
    "cc_bridge",
    "character_runtime",
    # NOTE(P3-12): "chat_ui" is intentionally absent — it is a PyQt6 widget
    # consumed directly by ui/ai_chat_page.py and must not be loaded by the
    # lazy __getattr__ walker, which would drag PyQt6 into the headless backend
    # whenever an unknown attribute is accessed on the `assistant` package.
    "checkpoint",
    "codex",
    "computer_use",
    "cost_tracker",
    "delivery_route",
    "dispatch_result",
    "effort_estimator",
    "environment",
    "events",
    "execution",
    "execution_history",
    "execution_plan",
    "inbox",
    "intent_router",
    "judge",
    "judge_decision",
    "judge_mixin",
    "judge_supertag",
    "local_router",
    "models",
    "path_comparator",
    "planner",
    "prompt_context",
    "runtime",
    "security_layer",
    "source_registry",
    "state_machine",
    "suggestion_planner",
    "tag_service",
    "task",
    "task_lifecycle",
    "task_manager",
    "task_optimizer",
    "transaction",
    "user_profile",
]


def __getattr__(name):
    for mod_name in _MODULE_NAMES:
        try:
            mod = _importlib.import_module(f".{mod_name}", __package__)
        except ImportError:
            continue
        if hasattr(mod, name):
            return getattr(mod, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
