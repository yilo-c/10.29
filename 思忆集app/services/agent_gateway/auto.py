"""AutoGateway —— 内核探测 + 失败自动切换（A1 增强）。

``agent_kernel.type = "auto"`` 时启用：启动时按候选顺序探测可用内核
（health().available == True 者入选），选定当前内核；连续失败达到阈值后
自动切换到下一个候选，而不是降级到 butler（比"配置驱动 + 重启生效"更接近
"自己换内核"）。

行为契约：
* 候选顺序固定为 ``("dsh", "claude_code", "codex")``（成本/成熟度优先）；
  配置里可显式指定 ``auto.candidates`` 覆盖顺序。
* ``submit`` 失败判定：``AgentRun.status in ("failed", "unavailable")``。
* 连续失败 ``max_failures`` 次（默认 2）→ 切换下一个候选，计数清零；
  成功后计数清零。切换信息写入 ``switch_history``（供 kernel.status 观测）。
* 所有候选都不可用时：行为同 NoneGateway（返回 unavailable，不崩溃）。
* 显式 ``profile`` 透传给当前适配器（M4 dsh profile 成本分级不受影响）。

线程安全：gateway 实例由 backend handler 经 ``asyncio.to_thread`` 串行调用
（同一时刻只有一个 submit 在跑），因此不做锁；切换计数非精确并发安全，
但最坏情况只是多跑一次失败才切换，可接受。
"""

from __future__ import annotations

import logging
import time
from typing import Any, Iterator

from .adapters import ClaudeCodeGateway, CodexGateway, DshGateway, NoneGateway
from .protocol import AgentRun, AgentRunEvent, BaseGateway

logger = logging.getLogger("siyi.agent_gateway.auto")

# 候选默认顺序（成本/成熟度优先；auto.candidates 可覆盖）
DEFAULT_CANDIDATE_ORDER: tuple[str, ...] = ("dsh", "claude_code", "codex")

# 各内核默认构造配置（与 factory.DEFAULT_AGENT_KERNEL_CONFIG 对齐；AutoGateway
# 单独维护一份，避免与 factory 的耦合环）。
_CANDIDATE_DEFAULTS: dict[str, dict[str, Any]] = {
    "dsh": {"profile": "headless", "timeout_s": 240},
    "claude_code": {"model": None, "max_turns": 8, "timeout_s": 240},
    "codex": {"model": None, "timeout_s": 240},
}


def _build_candidate(kernel_type: str, cfg: dict[str, Any]) -> BaseGateway:
    """按内核类型构造候选适配器（与 factory 等价；失败返回 NoneGateway）。"""
    kwargs = dict(_CANDIDATE_DEFAULTS.get(kernel_type, {}))
    kwargs.update({k: v for k, v in (cfg or {}).items() if v is not None})
    if kernel_type == "dsh":
        return DshGateway(**kwargs)
    if kernel_type == "claude_code":
        return ClaudeCodeGateway(**kwargs)
    if kernel_type == "codex":
        return CodexGateway(**kwargs)
    return NoneGateway()


class AutoGateway(BaseGateway):
    """按候选顺序探测 + 失败自动切换的执行内核包装器。"""

    name = "auto"

    def __init__(
        self,
        candidates: list[str] | None = None,
        cfg: dict[str, Any] | None = None,
        max_failures: int = 2,
    ) -> None:
        # 候选顺序：显式 candidates 优先，否则默认顺序
        order = [c for c in (candidates or DEFAULT_CANDIDATE_ORDER)]
        cfg = cfg or {}
        per_kernel = {k: (cfg.get(k) or {}) for k in order}
        self.max_failures = max(1, int(max_failures))
        # 构造全部候选（无论是否可用，health 探测时才知道）
        self._pool: dict[str, BaseGateway] = {k: _build_candidate(k, per_kernel[k]) for k in order}
        self._order = list(order)
        self._active: str | None = None
        self._available: list[str] = []
        self._consecutive_failures = 0
        self._switch_count = 0
        self._last_switch_at: float | None = None
        self._switch_history: list[dict[str, Any]] = []
        self._probe()

    # ── 探测 ──────────────────────────────────────────────
    def _probe(self) -> None:
        """按候选顺序探测可用性，选定第一个可用者为 active。"""
        self._available = []
        for kernel_type in self._order:
            gw = self._pool[kernel_type]
            try:
                health = gw.health()
                if health.get("available"):
                    self._available.append(kernel_type)
            except Exception as exc:
                logger.debug("auto probe %s failed: %s", kernel_type, exc)
        self._active = self._available[0] if self._available else None
        if self._active:
            logger.info("AutoGateway active=%s available=%s", self._active, self._available)
        else:
            logger.warning("AutoGateway: no kernel available; candidates=%s", self._order)

    def _switch_to(self, kernel_type: str | None, reason: str) -> None:
        """切换到指定内核（None = 无可用，进入 none 态）。"""
        prev = self._active
        self._active = kernel_type
        self._consecutive_failures = 0
        if kernel_type is not None and kernel_type != prev:
            self._switch_count += 1
            self._last_switch_at = time.time()
        self._switch_history.append(
            {
                "at": time.time(),
                "from": prev,
                "to": kernel_type,
                "reason": reason,
            }
        )
        self._switch_history = self._switch_history[-20:]
        logger.info("AutoGateway switch: %s -> %s (%s)", prev, kernel_type, reason)

    # ── 核心提交 ──────────────────────────────────────────
    def _active_gateway(self) -> BaseGateway:
        if self._active is None:
            return NoneGateway()
        return self._pool[self._active]

    def capabilities(self) -> dict[str, bool]:
        """能力声明委托给**当前 active** 候选（批5 单一真源，2026-09-18）。

        此前 AutoGateway 未覆盖本方法 → 恒返回 BaseGateway 的默认声明
        （``resume=False`` / ``stream=False`` …），与真实生效内核脱节。而
        ``kernel.status.details.capabilities`` 正是前端判断"能否续跑"的
        唯一数据源（见 backend/handlers/_kernel.py），auto 档下会给出与实际
        内核不符的答案 —— 换内核悄悄变样。

        none 态（无可用候选）由 ``NoneGateway`` 回默认声明，语义不变。
        """
        try:
            return dict(self._active_gateway().capabilities())
        except Exception:  # noqa: BLE001 — 能力探测失败按默认声明降级
            logger.debug("AutoGateway capabilities probe failed; falling back to defaults", exc_info=True)
            return dict(self._default_capabilities)

    def submit(self, prompt: str, timeout_s: int | None = None, profile: str | None = None) -> AgentRun:
        gw = self._active_gateway()
        # NoneGateway.submit 不接受 profile（BaseGateway 签名），仅透传给真实适配器
        if isinstance(gw, NoneGateway):
            run = gw.submit(prompt, timeout_s=timeout_s)
        else:
            run = gw.submit(prompt, timeout_s=timeout_s, profile=profile)
        if run.status in ("failed", "unavailable"):
            self._consecutive_failures += 1
            if self._consecutive_failures >= self.max_failures:
                self._failover(reason=f"{self._active} failed {self._consecutive_failures}x")
                # 切换后重试一次新内核（避免把本次失败结果直接返回给用户）
                gw2 = self._active_gateway()
                if gw2 is not gw and not isinstance(gw2, NoneGateway):
                    run2 = gw2.submit(prompt, timeout_s=timeout_s, profile=profile)
                    if run2.status == "succeeded":
                        return run2
                    # 重试也失败：返回原始失败（保留 error_kind 归一化）
        else:
            self._consecutive_failures = 0
        return run

    def _failover(self, reason: str) -> None:
        """切换到下一个候选；无更多候选时进入 none 态。"""
        if not self._available:
            self._switch_to(None, reason)
            return
        current = self._active
        if current is None:
            # none 态恢复：重新探测
            self._probe()
            return
        idx = self._available.index(current) if current in self._available else -1
        if idx >= 0 and idx + 1 < len(self._available):
            self._switch_to(self._available[idx + 1], reason)
        else:
            self._switch_to(None, reason)

    # ── 流式 / 会话 / 取消（透传给当前适配器）─────────────
    def submit_stream(self, prompt: str, timeout_s: int | None = None, profile: str | None = None) -> Iterator[AgentRunEvent]:
        # P0：status 先行（即时反馈），submit 的失败切换逻辑保持不变
        yield AgentRunEvent(kind="status", payload={"phase": "started"})
        run = self.submit(prompt, timeout_s=timeout_s, profile=profile)
        if run.result is not None:
            yield AgentRunEvent(kind="delta", payload={"text": run.result})
        if run.status == "succeeded":
            yield AgentRunEvent(kind="result", payload={"text": run.result or "", "usage": (run.raw or {}).get("usage")})
        elif run.status == "unavailable":
            yield AgentRunEvent(kind="error", payload={"error_kind": "unavailable", "message": run.error or "unavailable"})
        else:
            yield AgentRunEvent(kind="error", payload={"error_kind": (run.raw or {}).get("error_kind", "internal"), "message": run.error or "failed"})

    def resume(self, session_ref: str, prompt: str, timeout_s: int | None = None, profile: str | None = None) -> AgentRun:
        return self._active_gateway().resume(session_ref, prompt, timeout_s=timeout_s, profile=profile)

    def cancel(self, session_ref: str) -> bool:
        return self._active_gateway().cancel(session_ref)

    # ── 健康与观测 ────────────────────────────────────────
    def health(self) -> dict[str, Any]:
        active = self._active
        return {
            "kernel": self.name,
            "available": active is not None,
            "active": active,
            "available_candidates": list(self._available),
            "candidate_order": list(self._order),
            "switch_count": self._switch_count,
            "last_switch_at": self._last_switch_at,
            "consecutive_failures": self._consecutive_failures,
            "max_failures": self.max_failures,
            "switch_history": list(self._switch_history),
            "per_kernel": {k: self._pool[k].health() for k in self._order},
        }
