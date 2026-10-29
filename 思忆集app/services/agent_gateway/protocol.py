"""AgentGateway 端口协议（M1 —— 执行内核可替换的分层边界）。

功能路径层只依赖本模块的 ``AgentGateway`` 抽象，不 import 任何具体
agent 实现。换内核 = 换 ``services.agent_gateway.factory`` 里的适配器，
功能路径代码零改动（见 docs/kernel-layering-design-2026-08-15.md）。

约定：
* 所有方法为同步实现；异步调用方（backend handler）必须用
  ``asyncio.to_thread`` 包裹（AGENTS.md 同步 IO 约定）。
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Iterator, Protocol, runtime_checkable


@dataclass
class AgentRun:
    """一次内核执行的结果（与具体内核无关的统一形状）。"""

    run_id: str = ""
    status: str = "pending"  # succeeded / failed / unavailable
    result: str | None = None
    error: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.status == "succeeded"


@dataclass
class AgentRunEvent:
    """submit_stream 产出的增量事件（契约 §2.2）。

    kind ∈ {status, delta, tool, confirm, result, error}：
      - status ：阶段变化（payload={"phase": "started|running|..."}）
      - delta  ：增量文本（payload={"text": "..."}）
      - tool   ：内核工具调用（payload={"tool": "...", "args": {...}}，结构化工具步骤）
      - confirm：内核请求用户确认（payload={"confirmation_id", "action_id", "title", "detail"}，
        前端据此渲染审批提示；批准经 confirmation.resolve 回写 store）
      - result ：终态成功（payload={"text": ..., "usage": {...}|None}）
      - error  ：终态失败（payload={"error_kind": ..., "message": ...}）
    """

    kind: str
    payload: dict[str, Any] = field(default_factory=dict)
    ts: float = field(default_factory=time.monotonic)


class BaseGateway:
    """v2 网关基类：为所有适配器提供 submit_stream / resume / cancel 默认实现（契约 §2.2）。

    适配器只需实现 submit / health；流式为 submit 的确定性包装：
      status(started) → [delta]* → result|error（单一终态）。

    能力特征声明（M3）：适配器按实际实现覆盖 ``capabilities`` 或重写
    ``_default_capabilities``，声明其原生支持的事件能力。前端据此决定
    是否渲染工具步骤 / 审批提示 / 续跑按钮 —— 避免"换内核悄悄变样"。
    """

    name: str = ""

    # 能力特征：每个布尔表示该内核是否原生产出对应 AgentRunEvent / 支持对应方法。
    # 默认值按 BaseGateway 的兜底实现（无原生流式/工具/审批事件，resume 退化为 submit，无取消）。
    _default_capabilities: dict[str, bool] = {
        "stream": False,  # 原生增量事件（非 submit 事后抹平）
        "tool": False,  # 结构化工具调用事件（kind=tool）
        "confirm": False,  # 请求用户确认事件（kind=confirm）
        "resume": False,  # 真正在既有会话上续跑
        "cancel": False,  # 可取消运行中任务
    }

    def capabilities(self) -> dict[str, bool]:
        """返回该内核的能力特征声明（只读，前端按此渲染）。"""
        return dict(self._default_capabilities)

    def supports_resume(self) -> bool:
        """本内核是否**真正**支持在既有会话上续跑（``capabilities()["resume"]``）。

        单一真源入口：调用方一律经本方法（或模块级 :func:`supports_resume`）
        取判据，**不得**各自凭文档/注释另行推断。历史教训（2026-09-18 批5）：
        ``gateway_path.run_gateway_resume`` 的 docstring 声称"在既有会话上续跑"，
        而同文件 ``:503`` 与前端 ChatView 又断言"DSH 退化为 submit、无法续跑"
        —— 同一事实两处各算一遍且互相矛盾。真值只在能力声明里。
        """
        return bool(self.capabilities().get("resume", False))

    def submit(self, prompt: str, timeout_s: int | None = None) -> AgentRun:
        raise NotImplementedError

    def health(self) -> dict[str, Any]:
        raise NotImplementedError

    def submit_stream(self, prompt: str, timeout_s: int | None = None, profile: str | None = None) -> Iterator[AgentRunEvent]:
        # P0（2026-08-16）：status(started) 先行产出 -- 用户发出请求瞬间即可
        # 在前端看到"内核已启动"notice，长任务期间由渐进式管道心跳保活。
        yield AgentRunEvent(kind="status", payload={"phase": "started"})
        run = self.submit(prompt, timeout_s=timeout_s)
        if run.result is not None:
            yield AgentRunEvent(kind="delta", payload={"text": run.result})
        if run.status == "succeeded":
            yield AgentRunEvent(
                kind="result",
                payload={"text": run.result or "", "usage": (run.raw or {}).get("usage")},
            )
        elif run.status == "unavailable":
            yield AgentRunEvent(
                kind="error",
                payload={"error_kind": "unavailable", "message": run.error or "unavailable"},
            )
        else:
            yield AgentRunEvent(
                kind="error",
                payload={
                    "error_kind": (run.raw or {}).get("error_kind", "internal"),
                    "message": run.error or "failed",
                },
            )

    def resume(self, session_ref: str, prompt: str, timeout_s: int | None = None, profile: str | None = None) -> AgentRun:
        # 无会话能力的网关退化为普通 submit（契约 §2.2：session_ref 为忽略提示）
        return self.submit(prompt, timeout_s=timeout_s)

    def cancel(self, session_ref: str) -> bool:
        # 多数 CLI 内核无取消能力；返回 False 表示不支持（契约 §2.2）
        return False


@runtime_checkable
class AgentGateway(Protocol):
    """执行内核抽象端口。

    实现方 = 具体 agent 适配器（DSH / Claude Code / Codex / 未来任意）。
    """

    name: str

    def submit(self, prompt: str, timeout_s: int | None = None) -> AgentRun:
        """提交一次性任务，阻塞到内核返回最终答案。"""
        ...

    def health(self) -> dict[str, Any]:
        """内核可用性探测（CLI 是否在 PATH、配置是否完整等）。"""
        ...

    def submit_stream(self, prompt: str, timeout_s: int | None = None, profile: str | None = None) -> Iterator[AgentRunEvent]:
        """流式提交：增量产出 AgentRunEvent，终态为 result 或 error。"""
        ...

    def resume(self, session_ref: str, prompt: str, timeout_s: int | None = None, profile: str | None = None) -> AgentRun:
        """在既有会话上续跑（无会话能力时退化为 submit）。"""
        ...

    def cancel(self, session_ref: str) -> bool:
        """取消运行中任务；不支持返回 False。"""
        ...


def supports_resume(gw: Any) -> bool:
    """跨内核统一的「是否支持续跑」判据（批5 单一真源）。

    为什么要有这个自由函数而不是各处直接读 ``gw.capabilities()``：

    * 壳内（backend / services）与前端（经 ``kernel.status.details.capabilities``）
      必须读**同一处**声明，否则同一事实会被两处各算一遍 —— 2026-09-18 批5 的
      mcp:kernel 矛盾正是这么产生的（后端 gateway_path 文档说能续跑，前端
      ChatView 硬编码说不能）。
    * 缺 ``capabilities`` 的桩/旧实现按**保守**判 False（不声称能续跑）：
      宁可如实回落 butler resume 并给出 ``no_pending_task``，也不要伪造一次
      "续跑"实为新会话（会重复执行、重复计费且与 checkpoint 语义错位）。
    """
    probe = getattr(gw, "supports_resume", None)
    if callable(probe):
        try:
            return bool(probe())
        except Exception:  # noqa: BLE001 — 能力探测失败按不支持降级
            return False
    caps = getattr(gw, "capabilities", None)
    if callable(caps):
        try:
            return bool((caps() or {}).get("resume", False))
        except Exception:  # noqa: BLE001
            return False
    return False
