"""User-input dispatch for the autonomous assistant."""
from __future__ import annotations

import logging
from typing import Any

from ..dispatch_result import DispatchResult
from ..intent_router import INTENT_AUTOMATE, INTENT_CHAT, INTENT_EXECUTE

logger = logging.getLogger("siyi.assistant.autonomy.dispatcher")
_RUN_GOAL_KEYS = {"source", "delivery_channel", "chat_session_id", "cancellation_event"}


class AutonomyDispatcher:
    COMPUTER_USE_KEYWORDS = frozenset({"点击", "鼠标", "键盘", "截图", "屏幕", "浏览器", "网页"})
    COMPUTER_USE_TRIGGERS = frozenset({"computer use", "computer-use", "电脑操作"})

    def __init__(self, manager) -> None:
        self.manager = manager

    def _is_computer_use_goal(self, text: str) -> bool:
        normalized = str(text or "").lower()
        return any(item in normalized for item in self.COMPUTER_USE_KEYWORDS | self.COMPUTER_USE_TRIGGERS)

    @staticmethod
    def _classification_data(classification: Any) -> dict[str, Any]:
        if hasattr(classification, "to_dict"):
            return classification.to_dict()
        if isinstance(classification, dict):
            return dict(classification)
        return {"intent": INTENT_CHAT, "reason": "invalid_classification"}

    def dispatch_user_input(self, text: str, **kwargs: Any) -> DispatchResult:
        if getattr(self.manager, "intent_router", None) is None:
            return DispatchResult.not_handled(
                intent=INTENT_CHAT,
                data={"intent": INTENT_CHAT, "classification": {"reason": "router_unavailable"}},
            )
        classification = self.manager.intent_router.classify(text)
        data = self._classification_data(classification)
        intent = data.get("intent", INTENT_CHAT)
        if intent == INTENT_EXECUTE:
            run_goal_kwargs = {key: value for key, value in kwargs.items() if key in _RUN_GOAL_KEYS}
            try:
                result = self.manager.run_goal(text, **run_goal_kwargs)
            except Exception as exc:
                logger.exception("assistant goal dispatch failed")
                return DispatchResult.not_handled(
                    intent,
                    {"intent": intent, "classification": data, "error": str(exc)},
                )
            return DispatchResult.handled(intent, {"intent": intent, "classification": data, "result": result})
        if intent == INTENT_AUTOMATE:
            return self._handle_automate(text, classification)
        return self._handle_chat(text, classification)

    def _handle_execute(self, text: str, classification: Any, **kwargs: Any) -> DispatchResult:
        result = self.manager.run_goal(text, **kwargs)
        return DispatchResult.handled(
            INTENT_EXECUTE,
            {"intent": INTENT_EXECUTE, "classification": self._classification_data(classification), "result": result},
        )

    def _handle_automate(self, text: str, classification: Any) -> DispatchResult:
        return DispatchResult.not_handled(
            INTENT_AUTOMATE,
            {"intent": INTENT_AUTOMATE, "classification": self._classification_data(classification)},
        )

    def _handle_chat(self, text: str, classification: Any) -> DispatchResult:
        return DispatchResult.not_handled(
            INTENT_CHAT,
            {"intent": INTENT_CHAT, "classification": self._classification_data(classification)},
        )