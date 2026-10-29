"""Optional decision optimization and memory recording."""
from __future__ import annotations

import logging

logger = logging.getLogger("siyi.assistant.autonomy.optimizer")


class AutonomyDecisionOptimizer:
    def __init__(self, manager) -> None:
        self.manager = manager

    def optimize_decision_path(self, goal, steps, profile_snapshot):
        optimizer = getattr(self.manager, "task_optimizer", None)
        estimator = getattr(self.manager, "effort_estimator", None)
        if optimizer is None and estimator is None:
            return steps
        try:
            step_dicts = [step.to_dict() if hasattr(step, "to_dict") else {} for step in steps]
            if estimator is not None:
                for step, data in zip(steps, step_dicts):
                    estimate = estimator.estimate(data, {"goal": goal})
                    step.metadata = dict(getattr(step, "metadata", {}) or {})
                    step.metadata["effort_estimate"] = estimate.to_dict() if hasattr(estimate, "to_dict") else str(estimate)
            return steps
        except Exception:
            logger.warning("decision optimization failed", exc_info=True)
            return steps

    def record_decision(self, goal, steps, result, profile_snapshot) -> None:
        decision_memory = getattr(self.manager, "decision_memory", None)
        if decision_memory is None:
            return
        try:
            from memory.decision import DecisionRecord
            decision_memory.store(DecisionRecord(
                decision_id=f"goal_{hash(goal) & 0xFFFF_FFFF:08x}",
                goal=str(goal)[:200], context=str(profile_snapshot.get("context", ""))[:500],
                chosen_path="execute_and_record", alternatives=[],
                reasoning="planned via AssistantPlannerLayer",
                outcome="completed" if not int(result.get("failed", 0) or 0) else "failed",
                tags=["auto", "assistant"], confidence=0.7,
            ))
        except Exception:
            logger.warning("decision memory recording failed", exc_info=True)