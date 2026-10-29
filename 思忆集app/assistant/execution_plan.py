"""执行前计划生成与用户确认（Windsurf Cascade 借鉴）。

复杂操作前 Judge 预生成执行计划，用户确认后执行。
用户可编辑计划（跳过/重排步骤），拒绝则记录意图不执行。
"""
from __future__ import annotations

import json
import logging
from collections.abc import Callable
from datetime import datetime, timezone, timedelta
from enum import Enum
from typing import Any

logger = logging.getLogger("siyi.assistant.execution_plan")

# ── 计划步骤风险等级与耗时估算 ──────────────────────────────

RISK_ESTIMATED_TIMES: dict[str, int] = {
    "low": 10,       # 低风险：约 10 秒
    "medium": 30,    # 中风险：约 30 秒
    "high": 120,     # 高风险：约 2 分钟
}

RISK_LABELS: dict[str, str] = {
    "low": "低风险",
    "medium": "中风险",
    "high": "高风险",
}

RISK_IMPACTS: dict[str, str] = {
    "low": "仅读取数据，不会产生任何修改",
    "medium": "会写入本地数据，可回滚",
    "high": "会修改系统状态或外部数据，需谨慎确认",
}


class PlanStatus(str, Enum):
    PENDING = "pending"           # 等待用户确认
    APPROVED = "approved"         # 用户已确认
    REJECTED = "rejected"         # 用户拒绝
    EDITED = "edited"             # 用户编辑后确认
    EXPIRED = "expired"           # 超时未确认


class ExecutionPlanStep:
    """计划中的单个步骤。"""

    def __init__(
        self,
        step_id: str,
        action_id: str,
        description: str,
        *,
        risk_level: str = "low",
        estimated_seconds: int | None = None,
        file_paths: list[str] | None = None,
        enabled: bool = True,
    ):
        self.step_id = step_id
        self.action_id = action_id
        self.description = description
        self.risk_level = risk_level
        self.estimated_seconds = estimated_seconds or RISK_ESTIMATED_TIMES.get(risk_level, 15)
        self.file_paths = list(file_paths or [])
        self.enabled = enabled

    @property
    def risk_label(self) -> str:
        return RISK_LABELS.get(self.risk_level, "未知")

    @property
    def impact_description(self) -> str:
        return RISK_IMPACTS.get(self.risk_level, "")

    @property
    def estimated_display(self) -> str:
        secs = self.estimated_seconds
        if secs < 60:
            return f"约 {secs} 秒"
        return f"约 {secs // 60} 分钟"

    def to_dict(self) -> dict[str, Any]:
        return {
            "step_id": self.step_id,
            "action_id": self.action_id,
            "description": self.description,
            "risk_level": self.risk_level,
            "risk_label": self.risk_label,
            "impact_description": self.impact_description,
            "estimated_seconds": self.estimated_seconds,
            "estimated_display": self.estimated_display,
            "file_paths": self.file_paths,
            "enabled": self.enabled,
        }


class ExecutionPlan:
    """执行计划：由 Judge 生成，用户确认后执行。

    用法:
        plan = ExecutionPlan(goal="更新配置文件")
        plan.add_step("step_1", "file.read", "读取当前配置", risk_level="low")
        plan.add_step("step_2", "file.write", "写入新配置", risk_level="medium", file_paths=["config.json"])

        # 展示给用户
        plan_summary = plan.get_plan_summary()

        # 用户确认
        plan.approve()

        # 或用户编辑
        plan.edit(disabled_steps=["step_2"])

        # 或用户拒绝
        plan.reject(reason="风险太大")
    """

    def __init__(
        self,
        plan_id: str,
        goal: str,
        *,
        source: str = "judge",
        reasoning: str = "",
        expires_minutes: int = 30,
    ):
        self.plan_id = plan_id
        self.goal = goal
        self.source = source
        self.reasoning = reasoning
        self._steps: list[ExecutionPlanStep] = []
        self.status: PlanStatus = PlanStatus.PENDING
        self._reject_reason: str = ""
        self._created_at = datetime.now(timezone.utc)
        self._expires_at = self._created_at + timedelta(minutes=expires_minutes)

    def add_step(
        self,
        step_id: str,
        action_id: str,
        description: str,
        *,
        risk_level: str = "low",
        estimated_seconds: int | None = None,
        file_paths: list[str] | None = None,
    ) -> ExecutionPlanStep:
        """添加一个计划步骤。"""
        step = ExecutionPlanStep(
            step_id=step_id,
            action_id=action_id,
            description=description,
            risk_level=risk_level,
            estimated_seconds=estimated_seconds,
            file_paths=file_paths,
        )
        self._steps.append(step)
        return step

    def get_steps(self) -> list[ExecutionPlanStep]:
        """返回当前有效的步骤列表（按顺序、考虑 enable/disable）。"""
        return [s for s in self._steps if s.enabled]

    def is_expired(self) -> bool:
        return datetime.now(timezone.utc) > self._expires_at

    # ── 用户操作 ─────────────────────────────────────────

    def approve(self) -> None:
        """用户确认执行。"""
        if self.is_expired():
            self.status = PlanStatus.EXPIRED
            return
        self.status = PlanStatus.APPROVED

    def reject(self, *, reason: str = "用户拒绝") -> None:
        """用户拒绝执行。"""
        self.status = PlanStatus.REJECTED
        self._reject_reason = reason

    def edit(
        self,
        *,
        disabled_steps: list[str] | None = None,
        reorder: list[str] | None = None,
    ) -> None:
        """用户编辑计划后确认。

        Args:
            disabled_steps: 要禁用的 step_id 列表
            reorder: 重排后的 step_id 列表（仅包含启用的步骤）
        """
        if disabled_steps:
            for step in self._steps:
                if step.step_id in disabled_steps:
                    step.enabled = False

        if reorder:
            enabled = [s for s in self._steps if s.enabled]
            id_to_step = {s.step_id: s for s in enabled}
            reordered = []
            for sid in reorder:
                if sid in id_to_step:
                    reordered.append(id_to_step[sid])
            # 保留未在 reorder 中但启用的步骤
            seen = set(reorder)
            for s in enabled:
                if s.step_id not in seen:
                    reordered.append(s)
            # 替换 steps（维持 disabled 状态）
            enabled_set = set(s.step_id for s in reordered)
            self._steps = reordered + [s for s in self._steps if not s.enabled]

        self.status = PlanStatus.EDITED

    # ── 显示 ─────────────────────────────────────────────

    def get_plan_summary(self) -> dict[str, Any]:
        """获取计划的完整摘要（供 UI 展示）。"""
        enabled_steps = self.get_steps()
        total_seconds = sum(s.estimated_seconds for s in enabled_steps)
        risks = set(s.risk_level for s in enabled_steps)
        highest_risk = "low"
        risk_rank = {"low": 1, "medium": 2, "high": 3}
        for r in risks:
            if risk_rank.get(r, 0) > risk_rank.get(highest_risk, 0):
                highest_risk = r

        return {
            "plan_id": self.plan_id,
            "goal": self.goal,
            "source": self.source,
            "reasoning": self.reasoning,
            "status": self.status.value,
            "total_steps": len(enabled_steps),
            "total_estimated_seconds": total_seconds,
            "total_estimated_display": self._format_duration(total_seconds),
            "highest_risk": highest_risk,
            "highest_risk_label": RISK_LABELS.get(highest_risk, ""),
            "created_at": self._created_at.isoformat(),
            "expires_at": self._expires_at.isoformat(),
            "is_expired": self.is_expired(),
            "steps": [s.to_dict() for s in self._steps],  # all steps (including disabled)
            "enabled_steps": [s.to_dict() for s in enabled_steps],
            "reject_reason": self._reject_reason if self.status == PlanStatus.REJECTED else "",
        }

    @staticmethod
    def _format_duration(total_seconds: int) -> str:
        if total_seconds < 60:
            return f"约 {total_seconds} 秒"
        minutes = total_seconds // 60
        seconds = total_seconds % 60
        if seconds == 0:
            return f"约 {minutes} 分钟"
        return f"约 {minutes} 分 {seconds} 秒"

    def to_dict(self) -> dict[str, Any]:
        return self.get_plan_summary()


# ── 计划存储 ────────────────────────────────────────────────

class ExecutionPlanStore:
    """存储和管理执行计划。"""

    def __init__(self, data_store):
        self._data_store = data_store

    def save_plan(self, plan: ExecutionPlan) -> None:
        """保存计划到 data_store。"""
        def _updater(plans: Any) -> list[dict[str, Any]]:
            normalized = list(plans) if isinstance(plans, list) else []
            replacement = plan.to_dict()
            updated = False
            for idx, item in enumerate(normalized):
                if isinstance(item, dict) and item.get("plan_id") == plan.plan_id:
                    normalized[idx] = replacement
                    updated = True
                    break
            if not updated:
                normalized.append(replacement)
            return normalized[-100:]

        self._data_store.update_json("assistant_execution_plans", [], _updater)

    def load_plan(self, plan_id: str) -> dict[str, Any] | None:
        """加载指定计划。"""
        plans = self._data_store.load_json("assistant_execution_plans", [])
        normalized = list(plans) if isinstance(plans, list) else []
        for item in normalized:
            if isinstance(item, dict) and item.get("plan_id") == plan_id:
                return dict(item)
        return None

    def list_pending_plans(self) -> list[dict[str, Any]]:
        """列出所有等待用户确认的计划。"""
        plans = self._data_store.load_json("assistant_execution_plans", [])
        normalized = list(plans) if isinstance(plans, list) else []
        return [
            dict(item) for item in normalized
            if isinstance(item, dict) and item.get("status") == "pending"
        ]

    def list_recent_plans(self, limit: int = 20) -> list[dict[str, Any]]:
        """列出最近的计划（包含已确认/已拒绝）。"""
        plans = self._data_store.load_json("assistant_execution_plans", [])
        normalized = list(plans) if isinstance(plans, list) else []
        sorted_plans = sorted(
            [dict(item) for item in normalized if isinstance(item, dict)],
            key=lambda p: p.get("created_at", ""),
            reverse=True,
        )
        return sorted_plans[:limit]
