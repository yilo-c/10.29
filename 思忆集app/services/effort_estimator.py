# -*- coding: utf-8 -*-
"""Effort estimation — task cost prediction based on complexity analysis.

Provides time, token, and API call estimates for tasks,
using baseline reference data and contextual adjustments.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger("siyi.assistant.effort_estimator")


# ── Baseline reference data ──────────────────────────────────────────


@dataclass
class ComplexityBaseline:
    """各复杂度级别的基准估算数据。"""

    level: str
    estimated_minutes: int
    estimated_tokens: int
    estimated_api_calls: int
    confidence: float  # 0-1, data source reliability

    def to_dict(self) -> dict[str, Any]:
        return {
            "level": self.level,
            "estimated_minutes": self.estimated_minutes,
            "estimated_tokens": self.estimated_tokens,
            "estimated_api_calls": self.estimated_api_calls,
            "confidence": self.confidence,
        }


DEFAULT_BASELINES = [
    ComplexityBaseline("trivial", 2, 500, 0, 0.9),
    ComplexityBaseline("low", 10, 2000, 1, 0.85),
    ComplexityBaseline("medium", 30, 8000, 3, 0.8),
    ComplexityBaseline("high", 90, 25000, 8, 0.7),
    ComplexityBaseline("critical", 240, 80000, 20, 0.5),
]


@dataclass
class EffortEstimate:
    """单个任务的精力估算结果。"""

    task_id: str
    task_goal: str
    estimated_minutes: float
    estimated_tokens: int
    estimated_api_calls: int
    confidence: float
    complexity_level: str = "medium"
    details: dict[str, Any] = field(default_factory=dict)
    estimated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "task_goal": self.task_goal,
            "estimated_minutes": self.estimated_minutes,
            "estimated_tokens": self.estimated_tokens,
            "estimated_api_calls": self.estimated_api_calls,
            "confidence": self.confidence,
            "complexity_level": self.complexity_level,
            "details": dict(self.details),
            "estimated_at": self.estimated_at,
        }

    @property
    def cost_usd_approximate(self) -> float:
        """近似 USD 成本（基于 token 数估算）。"""
        return round(self.estimated_tokens * 0.000003, 4)  # ~$3/M tokens


class EffortEstimator:
    """精力估算——基于复杂度分析的自动任务成本预估。

    Usage:
        estimator = EffortEstimator()
        estimate = estimator.estimate(task_dict)
        print(f"预计耗时: {estimate.estimated_minutes} 分钟")
    """

    def __init__(self, baselines: list[ComplexityBaseline] | None = None):
        self._baselines = baselines or list(DEFAULT_BASELINES)
        self._history: list[EffortEstimate] = []

    def set_baselines(self, baselines: list[ComplexityBaseline]) -> None:
        self._baselines = list(baselines)

    def get_baselines(self) -> list[ComplexityBaseline]:
        return list(self._baselines)

    # ── Core estimation ──────────────────────────────────────────────

    def estimate(
        self,
        task: dict[str, Any],
        context: dict[str, Any] | None = None,
    ) -> EffortEstimate:
        """估算单个任务的精力和成本。

        Args:
            task: 任务数据，包含 goal, complexity, steps, priority 等字段。
            context: 可选的上下文信息（用户效率系数、可用资源等）。

        Returns:
            包含时间、token、API 调用数估算的 EffortEstimate。
        """
        task_id = str(task.get("id") or task.get("task_id", ""))
        task_goal = str(task.get("goal") or task.get("title", ""))

        # Determine base complexity
        complexity = self._determine_complexity(task)
        baseline = self._find_baseline(complexity)
        if baseline is None:
            baseline = ComplexityBaseline("medium", 30, 8000, 3, 0.8)

        ctx = context or {}

        # Adjust for step count
        steps = task.get("steps") or task.get("step_count", 0)
        if isinstance(steps, (list, tuple)):
            step_count = len(steps)
        else:
            step_count = int(steps) if steps else 1

        step_multiplier = 1.0 + (step_count - 1) * 0.5 if step_count > 1 else 1.0

        # User efficiency factor
        efficiency = float(ctx.get("user_efficiency", 1.0))
        efficiency = max(0.2, min(5.0, efficiency))

        # Compute estimates
        estimated_minutes = baseline.estimated_minutes * step_multiplier / efficiency
        estimated_tokens = int(baseline.estimated_tokens * step_multiplier)
        estimated_api_calls = int(baseline.estimated_api_calls * step_multiplier)

        # Confidence adjustment
        confidence = baseline.confidence
        if step_count > 5:
            confidence *= 0.9
        if efficiency != 1.0:
            confidence *= max(0.5, min(1.0, 1.0 - abs(1.0 - efficiency) * 0.1))

        result = EffortEstimate(
            task_id=task_id,
            task_goal=task_goal,
            estimated_minutes=round(estimated_minutes, 1),
            estimated_tokens=estimated_tokens,
            estimated_api_calls=estimated_api_calls,
            confidence=round(confidence, 3),
            complexity_level=complexity,
            details={
                "step_count": step_count,
                "step_multiplier": round(step_multiplier, 2),
                "efficiency": round(efficiency, 2),
                "baseline_minutes": baseline.estimated_minutes,
                "baseline_tokens": baseline.estimated_tokens,
                "baseline_api_calls": baseline.estimated_api_calls,
            },
        )

        self._history.append(result)
        return result

    def estimate_batch(
        self,
        tasks: list[dict[str, Any]],
        context: dict[str, Any] | None = None,
    ) -> list[EffortEstimate]:
        """批量估算多个任务。"""
        return [self.estimate(t, context) for t in tasks]

    # ── Complexity analysis ──────────────────────────────────────────

    def _determine_complexity(self, task: dict[str, Any]) -> str:
        """根据任务属性判断复杂度级别。"""
        explicit = str(task.get("complexity", "")).lower()
        if explicit in ("trivial", "low", "medium", "high", "critical"):
            return explicit

        # Auto-determine from task properties
        score = 0

        # Number of steps
        steps = task.get("steps") or task.get("step_count", 0)
        if isinstance(steps, (list, tuple)):
            score += len(steps) * 2
        elif isinstance(steps, (int, float)):
            score += int(steps) * 2

        # Risk level indicators
        risk = str(task.get("risk_level", "low")).lower()
        risk_scores = {"low": 0, "medium": 3, "high": 5}
        score += risk_scores.get(risk, 0)

        # Dependency count
        deps = task.get("depends_on") or task.get("dependencies", [])
        if isinstance(deps, (list, tuple)):
            score += len(deps)

        # Presence of payload/data requirements
        if task.get("payload"):
            score += 2

        # Map score to level
        if score <= 2:
            return "trivial"
        elif score <= 5:
            return "low"
        elif score <= 10:
            return "medium"
        elif score <= 18:
            return "high"
        else:
            return "critical"

    # ── History ──────────────────────────────────────────────────────

    def get_recent_estimates(self, limit: int = 20) -> list[EffortEstimate]:
        """获取最近的估算记录。"""
        return list(self._history[-limit:])

    def get_average_accuracy(self) -> dict[str, float]:
        """获取历史估算准确度统计（如果有反馈数据）。"""
        if not self._history:
            return {"average_minutes": 0, "average_tokens": 0, "count": 0}
        avg_min = sum(e.estimated_minutes for e in self._history) / len(self._history)
        avg_tok = sum(e.estimated_tokens for e in self._history) / len(self._history)
        return {
            "average_minutes": round(avg_min, 1),
            "average_tokens": int(avg_tok),
            "count": len(self._history),
        }

    # ── Helpers ──────────────────────────────────────────────────────

    def _find_baseline(self, level: str) -> ComplexityBaseline | None:
        for b in self._baselines:
            if b.level == level:
                return b
        return None
