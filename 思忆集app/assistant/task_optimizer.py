"""Task decision optimizer — multi-dimension sorting and prioritization.

Provides weighted scoring across speed, quality, and cost dimensions,
enabling informed task execution decisions based on current context.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

logger = logging.getLogger("siyi.assistant.task_optimizer")


class OptimizationGoal(StrEnum):
    """优化目标定义——速度 vs 质量 vs 成本优先级。"""
    SPEED = "speed"
    QUALITY = "quality"
    COST = "cost"
    BALANCED = "balanced"


@dataclass
class WeightConfig:
    """各维度权重配置。"""
    speed: float = 0.3
    quality: float = 0.4
    cost: float = 0.3

    @classmethod
    def for_goal(cls, goal: OptimizationGoal) -> WeightConfig:
        profiles = {
            OptimizationGoal.SPEED: cls(speed=0.7, quality=0.2, cost=0.1),
            OptimizationGoal.QUALITY: cls(speed=0.1, quality=0.8, cost=0.1),
            OptimizationGoal.COST: cls(speed=0.2, quality=0.2, cost=0.6),
            OptimizationGoal.BALANCED: cls(),
        }
        return profiles.get(goal, cls())

    def to_dict(self) -> dict[str, float]:
        return {"speed": self.speed, "quality": self.quality, "cost": self.cost}


@dataclass
class TaskScore:
    """单个任务的评分结果。"""
    task_id: str
    goal: str
    speed_score: float = 0.0
    quality_score: float = 0.0
    cost_score: float = 0.0
    overall: float = 0.0
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "goal": self.goal,
            "speed_score": self.speed_score,
            "quality_score": self.quality_score,
            "cost_score": self.cost_score,
            "overall": self.overall,
            "details": dict(self.details),
        }


@dataclass
class OptimizationResult:
    """优化决策结果。"""
    ranked_tasks: list[TaskScore] = field(default_factory=list)
    recommended_task_id: str = ""
    goal: str = OptimizationGoal.BALANCED.value
    context_summary: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "recommended_task_id": self.recommended_task_id,
            "goal": self.goal,
            "context_summary": self.context_summary,
            "ranked_tasks": [t.to_dict() for t in self.ranked_tasks],
        }


class TaskOptimizer:
    """任务决策优化器——基于多维度权重对候选任务进行排序和推荐。

    Usage:
        optimizer = TaskOptimizer(weight_config=WeightConfig.for_goal(OptimizationGoal.BALANCED))
        result = optimizer.optimize(tasks, context)
    """

    def __init__(self, weight_config: WeightConfig | None = None):
        self._weight_config = weight_config or WeightConfig()
        self._speed_estimator = None  # Optional: EffortEstimator reference
        self._budget_aware = False

    # ── Configuration ────────────────────────────────────────────────

    def set_weight_config(self, config: WeightConfig) -> None:
        self._weight_config = config

    def set_speed_estimator(self, estimator) -> None:
        self._speed_estimator = estimator

    def set_budget_aware(self, enabled: bool) -> None:
        self._budget_aware = enabled

    def get_weight_profile(self) -> dict[str, float]:
        return self._weight_config.to_dict()

    # ── Core optimization ────────────────────────────────────────────

    def optimize(
        self,
        tasks: list[dict[str, Any]],
        context: dict[str, Any] | None = None,
    ) -> OptimizationResult:
        """对候选任务列表进行多维度排序。

        Args:
            tasks: 候选任务列表，每项包含 id, goal, priority, estimated_cost 等字段。
            context: 当前上下文（用户状态、可用资源、时间约束等）。

        Returns:
            包含排序结果和推荐的 OptimizationResult。
        """
        if not tasks:
            return OptimizationResult()

        ctx = context or {}
        scored: list[TaskScore] = []

        for task in tasks:
            task_id = str(task.get("id") or task.get("task_id", ""))
            goal = str(task.get("goal") or task.get("title", ""))

            speed_score = self._score_speed(task, ctx)
            quality_score = self._score_quality(task, ctx)
            cost_score = self._score_cost(task, ctx)

            overall = (
                self._weight_config.speed * speed_score
                + self._weight_config.quality * quality_score
                + self._weight_config.cost * cost_score
            )

            scored.append(TaskScore(
                task_id=task_id,
                goal=goal,
                speed_score=speed_score,
                quality_score=quality_score,
                cost_score=cost_score,
                overall=round(overall, 4),
                details={
                    "priority": task.get("priority", "normal"),
                    "estimated_cost": task.get("estimated_cost"),
                    "complexity": task.get("complexity", "medium"),
                },
            ))

        scored.sort(key=lambda s: s.overall, reverse=True)
        recommended_id = scored[0].task_id if scored else ""

        return OptimizationResult(
            ranked_tasks=scored,
            recommended_task_id=recommended_id,
            goal=str(self._weight_config.to_dict().get("goal", OptimizationGoal.BALANCED.value)),
            context_summary=self._summarize_context(ctx),
        )

    # ── Dimension scorers ────────────────────────────────────────────

    def _score_speed(self, task: dict[str, Any], context: dict[str, Any]) -> float:
        """估算速度得分——越快得分越高。"""
        complexity = str(task.get("complexity", "medium")).lower()
        complexity_map = {"low": 0.9, "medium": 0.6, "high": 0.3, "critical": 0.1}
        base = complexity_map.get(complexity, 0.5)

        if self._speed_estimator is not None:
            try:
                est = self._speed_estimator.estimate(task)
                est_minutes = est.get("estimated_minutes", 30)
                if est_minutes <= 5:
                    base = min(1.0, base + 0.3)
                elif est_minutes > 120:
                    base = max(0.1, base - 0.2)
            except Exception:
                logger.debug("Non-critical error (see details)", exc_info=True)


        # Urgency boost
        urgency = context.get("urgency", "")
        if urgency == "high" and base < 0.8:
            base += 0.15

        return max(0.0, min(1.0, base))

    def _score_quality(self, task: dict[str, Any], context: dict[str, Any]) -> float:
        """估算质量得分——高优先级/重要任务得分越高。"""
        priority = str(task.get("priority", "normal")).lower()
        priority_map = {"critical": 0.95, "high": 0.8, "normal": 0.5, "low": 0.2}
        base = priority_map.get(priority, 0.5)

        # User focus alignment
        focus_topics = context.get("focus_topics") or []
        goal_text = str(task.get("goal", "")).lower()
        if focus_topics and goal_text:
            matched = sum(1 for t in focus_topics if t.lower() in goal_text)
            if matched > 0:
                base = min(1.0, base + 0.1 * matched)

        # Dependency chain value
        blocked_count = int(task.get("blocked_count", 0))
        if blocked_count > 0:
            base = min(1.0, base + 0.05 * min(blocked_count, 3))

        return max(0.0, min(1.0, base))

    def _score_cost(self, task: dict[str, Any], context: dict[str, Any]) -> float:
        """估算成本得分——成本越低得分越高。"""
        estimated_cost = task.get("estimated_cost")
        if estimated_cost is None:
            estimated_cost = task.get("cost", 50)
        try:
            estimated_cost = float(estimated_cost)
        except (ValueError, TypeError):
            estimated_cost = 50.0

        budget_remaining = context.get("budget_remaining")
        if budget_remaining is not None:
            try:
                budget_remaining = float(budget_remaining)
                if budget_remaining <= 0:
                    return 0.0
                ratio = estimated_cost / budget_remaining
                if ratio > 1.0:
                    return 0.1
                return max(0.1, 1.0 - ratio)
            except ValueError:
                logger.debug("Non-critical error (see details)", exc_info=True)


        cost_map = {"low": 0.9, "medium": 0.5, "high": 0.2}
        cost_level = str(task.get("cost_level", "medium")).lower()
        return cost_map.get(cost_level, 0.5)

    # ── Helpers ──────────────────────────────────────────────────────

    @staticmethod
    def _summarize_context(context: dict[str, Any]) -> str:
        parts = []
        if context.get("urgency"):
            parts.append(f"紧急度={context['urgency']}")
        if context.get("focus_topics"):
            topics = context.get("focus_topics", [])
            if isinstance(topics, list):
                parts.append(f"关注主题={'、'.join(str(t) for t in topics[:3])}")
        if context.get("budget_remaining") is not None:
            parts.append(f"预算剩余={context['budget_remaining']}")
        return " | ".join(parts) if parts else "默认上下文"

    def status_summary(self) -> dict[str, Any]:
        """返回优化器当前状态摘要。"""
        return {
            "weight_config": self._weight_config.to_dict(),
            "budget_aware": self._budget_aware,
            "has_speed_estimator": self._speed_estimator is not None,
        }
