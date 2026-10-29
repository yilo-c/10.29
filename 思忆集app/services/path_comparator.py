# -*- coding: utf-8 -*-
"""Multi-scheme comparison — A/B analysis and recommendation.

Compares multiple solution paths across configurable dimensions,
generates structured comparison reports, and recommends the best option.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger("siyi.assistant.path_comparator")


@dataclass
class ComparisonDimension:
    """单个比较维度。"""

    name: str
    weight: float = 1.0
    higher_is_better: bool = True
    description: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "weight": self.weight,
            "higher_is_better": self.higher_is_better,
            "description": self.description,
        }


@dataclass
class PathOption:
    """一个待比较的方案选项。"""

    option_id: str
    label: str
    scores: dict[str, float] = field(default_factory=dict)  # dimension_name -> score (0-1)
    metadata: dict[str, Any] = field(default_factory=dict)
    pros: list[str] = field(default_factory=list)
    cons: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "option_id": self.option_id,
            "label": self.label,
            "scores": dict(self.scores),
            "metadata": dict(self.metadata),
            "pros": list(self.pros),
            "cons": list(self.cons),
        }


@dataclass
class ComparisonResult:
    """方案比较结果。"""

    options: list[PathOption] = field(default_factory=list)
    dimensions: list[ComparisonDimension] = field(default_factory=list)
    weighted_scores: dict[str, float] = field(default_factory=dict)  # option_id -> total score
    recommendation: str = ""  # recommended option_id
    confidence: float = 0.0
    summary: str = ""
    compared_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> dict[str, Any]:
        return {
            "options": [o.to_dict() for o in self.options],
            "dimensions": [d.to_dict() for d in self.dimensions],
            "weighted_scores": dict(self.weighted_scores),
            "recommendation": self.recommendation,
            "confidence": self.confidence,
            "summary": self.summary,
            "compared_at": self.compared_at,
        }


DEFAULT_DIMENSIONS = [
    ComparisonDimension("implementation_effort", weight=1.5, higher_is_better=False, description="实现工作量（越低越好）"),
    ComparisonDimension("maintainability", weight=1.2, higher_is_better=True, description="可维护性"),
    ComparisonDimension("performance", weight=1.0, higher_is_better=True, description="性能表现"),
    ComparisonDimension("scalability", weight=0.8, higher_is_better=True, description="可扩展性"),
    ComparisonDimension("risk", weight=1.3, higher_is_better=False, description="实施风险（越低越好）"),
]


class PathComparator:
    """多方案对比——对候选方案进行多维度评分和推荐。

    Usage:
        comparator = PathComparator()
        result = comparator.compare(
            options=[...],
            dimensions=[...],  # or use DEFAULT_DIMENSIONS
        )
        print(result.recommendation)
    """

    def __init__(self, dimensions: list[ComparisonDimension] | None = None):
        self._dimensions = dimensions or list(DEFAULT_DIMENSIONS)

    def set_dimensions(self, dimensions: list[ComparisonDimension]) -> None:
        self._dimensions = list(dimensions)

    def add_dimension(self, dimension: ComparisonDimension) -> None:
        self._dimensions.append(dimension)

    def get_dimensions(self) -> list[ComparisonDimension]:
        return list(self._dimensions)

    # ── Core comparison ──────────────────────────────────────────────

    def compare(
        self,
        options: list[PathOption],
        dimensions: list[ComparisonDimension] | None = None,
    ) -> ComparisonResult:
        """对多个方案进行多维度比较。

        Args:
            options: 待比较的方案列表。
            dimensions: 比较维度列表，默认使用预设维度。

        Returns:
            包含加权评分、推荐和摘要的 ComparisonResult。
        """
        if not options:
            return ComparisonResult()

        active_dims = dimensions or self._dimensions
        active_dims = list(active_dims)
        if not active_dims:
            return ComparisonResult(options=options)

        weighted_scores: dict[str, float] = {}
        for opt in options:
            total = 0.0
            total_weight = 0.0
            for dim in active_dims:
                score = opt.scores.get(dim.name, 0.5)
                score = max(0.0, min(1.0, float(score)))
                if not dim.higher_is_better:
                    score = 1.0 - score
                total += score * dim.weight
                total_weight += dim.weight

            weighted_scores[opt.option_id] = round(total / total_weight, 4) if total_weight > 0 else 0.0

        if not weighted_scores:
            return ComparisonResult(options=options, dimensions=active_dims)

        recommendation = max(weighted_scores, key=lambda k: weighted_scores[k])
        confidence = self._compute_confidence(weighted_scores)
        summary = self._build_summary(options, weighted_scores, recommendation)

        return ComparisonResult(
            options=options,
            dimensions=active_dims,
            weighted_scores=weighted_scores,
            recommendation=recommendation,
            confidence=confidence,
            summary=summary,
        )

    # ── Convenience methods ──────────────────────────────────────────

    def compare_simple(
        self,
        options: list[dict[str, Any]],
    ) -> ComparisonResult:
        """简化接口——直接从 dict 列表创建比较。

        Args:
            options: 每项包含 option_id, label, scores, [pros], [cons]。
        """
        path_options = []
        for opt in options:
            path_options.append(
                PathOption(
                    option_id=str(opt.get("option_id", "")),
                    label=str(opt.get("label", "")),
                    scores=dict(opt.get("scores", {})),
                    metadata=dict(opt.get("metadata", {})),
                    pros=list(opt.get("pros", [])),
                    cons=list(opt.get("cons", [])),
                )
            )
        return self.compare(path_options)

    def set_scores_from_metadata(
        self,
        options: list[PathOption],
        metadata_key: str,
        score_fn,
    ) -> list[PathOption]:
        """使用元数据中的值计算评分。

        Args:
            options: 方案列表。
            metadata_key: 元数据键名。
            score_fn: 接受值并返回 0-1 评分的函数。

        Returns:
            更新了评分的方案列表。
        """
        updated = []
        for opt in options:
            value = opt.metadata.get(metadata_key)
            if value is not None:
                try:
                    opt.scores[metadata_key] = max(0.0, min(1.0, float(score_fn(value))))
                except Exception as exc:
                    logger.warning("score calculation failed for %s: %s", opt.option_id, exc, exc_info=True)
            updated.append(opt)
        return updated

    # ── Helpers ──────────────────────────────────────────────────────

    @staticmethod
    def _compute_confidence(weighted_scores: dict[str, float]) -> float:
        """计算推荐置信度——分差越大置信度越高。"""
        if len(weighted_scores) < 2:
            return 1.0
        sorted_scores = sorted(weighted_scores.values(), reverse=True)
        gap = sorted_scores[0] - sorted_scores[1]
        # Gap of 0.3 -> 0.6 confidence, gap of 0.5 -> 1.0 confidence
        return max(0.0, min(1.0, gap * 2.0))

    @staticmethod
    def _build_summary(
        options: list[PathOption],
        weighted_scores: dict[str, float],
        recommendation: str,
    ) -> str:
        """构建人类可读的比较摘要。"""
        opt_map = {o.option_id: o.label for o in options}
        rec_label = opt_map.get(recommendation, recommendation)
        parts = [f"推荐方案：{rec_label}（综合评分 {weighted_scores.get(recommendation, 0):.2f}）"]

        sorted_options = sorted(weighted_scores.items(), key=lambda x: x[1], reverse=True)
        for opt_id, score in sorted_options:
            label = opt_map.get(opt_id, opt_id)
            marker = ">>" if opt_id == recommendation else "  "
            parts.append(f"  {marker} {label}: {score:.2f}")
            opt_obj = next((o for o in options if o.option_id == opt_id), None)
            if opt_obj and opt_obj.pros:
                parts.append(f"    优势：{'、'.join(opt_obj.pros[:2])}")
            if opt_obj and opt_obj.cons:
                parts.append(f"    劣势：{'、'.join(opt_obj.cons[:2])}")

        return "\n".join(parts)

    def reset_dimensions(self) -> None:
        """重置为默认维度。"""
        self._dimensions = list(DEFAULT_DIMENSIONS)
