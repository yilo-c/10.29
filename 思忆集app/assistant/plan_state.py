# -*- coding: utf-8 -*-
"""B-P1a.1 计划跟踪核心 —— 壳内 agent 的"任务清单"数据结构与纯状态转移。

设计：零 IO、零墙钟直读（时间戳一律由调用方注入 `now`，守时钟纪律棘轮），可脱机单测。
用途：butler/规划器在循环里维护一份可勾的步骤清单（Plan），逐步 start/complete/fail/block，
产出可被前端 `ToolTimeline/AgentContextPanel` 渲染的进度事件；带步数/预算闸门防跑飞。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

Status = Literal["pending", "running", "done", "failed", "blocked", "skipped"]
_TERMINAL: frozenset[str] = frozenset({"done", "failed", "blocked", "skipped"})


@dataclass
class PlanStep:
    id: str
    title: str
    status: Status = "pending"
    detail: str = ""
    started_at: float | None = None
    finished_at: float | None = None


@dataclass
class Plan:
    goal: str
    steps: list[PlanStep] = field(default_factory=list)
    max_steps: int = 8  # 步数闸门（防长任务跑飞/烧预算）
    _seq: int = 0

    # ── 构造 ──
    def add_step(self, title: str, detail: str = "") -> PlanStep | None:
        """追加一步；已达 max_steps 则拒绝并返回 None（调用方据此收敛为"给结论"）。"""
        if len(self.steps) >= self.max_steps:
            return None
        self._seq += 1
        step = PlanStep(id=f"s{self._seq}", title=title.strip() or f"step {self._seq}", detail=detail)
        self.steps.append(step)
        return step

    # ── 状态转移（now 由调用方注入）──
    def get(self, step_id: str) -> PlanStep | None:
        return next((s for s in self.steps if s.id == step_id), None)

    def start(self, step_id: str, now: float) -> bool:
        s = self.get(step_id)
        if s is None or s.status != "pending":
            return False
        s.status, s.started_at = "running", now
        return True

    def complete(self, step_id: str, now: float, detail: str = "") -> bool:
        return self._finish(step_id, "done", now, detail)

    def fail(self, step_id: str, now: float, reason: str = "") -> bool:
        return self._finish(step_id, "failed", now, reason)

    def block(self, step_id: str, now: float, reason: str = "") -> bool:
        return self._finish(step_id, "blocked", now, reason)

    def skip(self, step_id: str, now: float, reason: str = "") -> bool:
        return self._finish(step_id, "skipped", now, reason)

    def _finish(self, step_id: str, status: Status, now: float, detail: str) -> bool:
        s = self.get(step_id)
        if s is None or s.status in _TERMINAL:
            return False
        s.status, s.finished_at = status, now
        if detail:
            s.detail = detail
        return True

    # ── 查询/进度 ──
    def next_pending(self) -> PlanStep | None:
        return next((s for s in self.steps if s.status == "pending"), None)

    def running(self) -> list[PlanStep]:
        return [s for s in self.steps if s.status == "running"]

    @property
    def done_count(self) -> int:
        return sum(1 for s in self.steps if s.status == "done")

    @property
    def total(self) -> int:
        return len(self.steps)

    @property
    def all_terminal(self) -> bool:
        return bool(self.steps) and all(s.status in _TERMINAL for s in self.steps)

    @property
    def has_failure(self) -> bool:
        return any(s.status in ("failed", "blocked") for s in self.steps)

    def remaining_steps(self) -> int:
        return max(0, self.max_steps - len(self.steps))

    def to_event_payload(self) -> dict:
        """前端进度事件载荷（供 tool_progress / exec.step kind=plan）。"""
        return {
            "kind": "plan",
            "goal": self.goal,
            "total": self.total,
            "done": self.done_count,
            "remaining_steps": self.remaining_steps(),
            "steps": [{"id": s.id, "title": s.title, "status": s.status, "detail": s.detail} for s in self.steps],
            "next": (self.next_pending().id if self.next_pending() else None),
        }
