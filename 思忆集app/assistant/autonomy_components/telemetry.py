"""Non-blocking telemetry for assistant routing decisions."""
from __future__ import annotations

import logging

logger = logging.getLogger("siyi.assistant.autonomy.telemetry")


class AutonomyTelemetry:
    def __init__(self, manager) -> None:
        self.manager = manager

    def record_judge_decision(self, text: str, judge_result: dict) -> None:
        service = getattr(self.manager, "observability_service", None)
        if service is None:
            return
        try:
            service.track_event("assistant.judge_decision", {
                "text": str(text)[:200], "decision": dict(judge_result or {}),
            })
        except Exception:
            logger.debug("judge telemetry failed", exc_info=True)