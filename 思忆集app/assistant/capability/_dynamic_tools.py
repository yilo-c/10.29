"""DynamicToolDomain — 动态工具能力域。

把 ToolSpecRegistry 中注册的所有工具动态暴露为 CapabilityRouter 的 handler。
无需为每个工具手写 CapabilityDomain — 所有 tool.* action_id 统一由此域处理。

接入方式：
    CapabilityRouter.__init__ 中：
        self.tool_registry = ToolSpecRegistry(config_path)
        self.tool_bridge = GenericToolBridge(self.tool_registry)
        self.dynamic_tool_domain = DynamicToolDomain(self)
        # 动态注册所有 tool.* handler
        for action_id in self.tool_registry.get_all_action_ids():
            self._handlers[action_id] = self.dynamic_tool_domain.dispatch
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import functools
import logging
from typing import Any

# A-8（2026-08-24）：原 `from ..generic_tool_bridge / from ..tool_spec` 指向 assistant/
# 下 07-18 事故遗留的无源码 .pyc（陈旧字节码进生产）。工具层已迁移至 services/tooling/
# （kernel-contract §5.4.1），这里改为迁移版，并删除遗留 .pyc。
from services.tooling.generic_tool_bridge import GenericToolBridge
from services.tooling.tool_spec import ToolSpecRegistry

# 显式再导出（mypy）：让 test_dynamic_tools_sourceless_import 的
# `from ... import GenericToolBridge, ToolSpecRegistry` 不再触发 attr-defined。
__all__ = ["DynamicToolDomain", "GenericToolBridge", "ToolSpecRegistry"]

logger = logging.getLogger("siyi.assistant.capability.dynamic_tools")


def _run_async(coro, timeout: int = 60):
    """在同步上下文中运行异步协程（线程池桥接）。"""
    try:
        asyncio.get_running_loop()
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(asyncio.run, coro).result(timeout=timeout + 10)
    except RuntimeError:
        return asyncio.run(coro)


class DynamicToolDomain:
    """动态工具能力域。

    统一处理所有 tool.* action_id 的调度请求，
    通过 GenericToolBridge 执行对应的 CLI 命令。
    """

    def __init__(self, router) -> None:
        self.router = router
        self._bridge: GenericToolBridge | None = None

    def _get_bridge(self) -> GenericToolBridge:
        if self._bridge is None:
            registry = getattr(self.router, "tool_registry", None)
            if registry is None:
                registry = ToolSpecRegistry()
            working_dir = getattr(self.router, "shell_execution", None)
            wd = working_dir.workspace_root if working_dir else None
            lifecycle_manager = getattr(self.router, "process_lifecycle_manager", None)
            self._bridge = GenericToolBridge(
                registry,
                working_dir=str(wd) if wd else None,
                process_lifecycle_manager=lifecycle_manager,
            )
        return self._bridge

    def dispatch(
        self,
        action_id: str,
        payload: dict[str, Any],
        *,
        source: str = "assistant",
    ) -> dict[str, Any]:
        """统一调度入口 — 处理所有 tool.* action_id。

        Args:
            action_id: 如 "tool.zcode.open"
            payload: 命令参数
            source: 调用上下文来源（V2.1 批次2：由调用方传入，**不再从 payload 读取**）。

        安全策略：
            - risk=high + confirmation!=auto → 自动进入 sandbox 审批队列
              （用户审批后才会真正执行）
            - risk=high + confirmation=auto → 拒绝执行（高风险不允许无审批）
            - risk=medium/low → 直接执行（ActionGuard 已做风险校验）
            - 已在审批上下文（_in_approval_var=True）→ 跳过审批直接执行
        """
        if not action_id:
            return {"success": False, "error": "missing action_id"}

        # V2.1 批次2（P0-8 动态工具来源伪造）：_source 改由调用上下文传入。
        # 之前 `payload.get("_source")` 让 LLM 可在参数里伪造 source（如伪装成 user
        # 执行 file.write）绕过 ActionGuard 的 llm_source_high_risk 拦截。
        # 同时剥离 payload 中的 _source，避免其作为命令参数透传给 CLI。
        if isinstance(payload, dict):
            payload = {k: v for k, v in payload.items() if k != "_source"}

        # ── ActionGuard 风险校验 ──
        action_guard = getattr(self.router, "action_guard", None)
        if action_guard is not None:
            guard_result = action_guard.validate(action_id, payload, source=source)
            if not guard_result.allowed:
                return {
                    "success": False,
                    "action_id": action_id,
                    "error": guard_result.reason,
                    "warnings": guard_result.warnings,
                    "risk_level": guard_result.risk_level,
                }

        # ── 高风险工具 sandbox 审批 ──
        sandbox_block = self._maybe_block_for_sandbox(action_id, payload)
        if sandbox_block is not None:
            return sandbox_block

        bridge = self._get_bridge()
        result = _run_async(bridge.execute(action_id, payload), timeout=300)
        return {
            "success": result.success,
            "action_id": result.action_id,
            "output": result.output,
            "error": result.error,
            "cli_path": result.cli_path,
            "duration_seconds": result.duration_seconds,
            "process_id": result.process_id,
            "extracted": result.extracted if result.extracted else {},
            "missing_secrets": result.missing_secrets if result.missing_secrets else [],
        }

    def _maybe_block_for_sandbox(self, action_id: str, payload: dict[str, Any]) -> dict[str, Any] | None:
        """根据 ToolCommand 的 risk/confirmation 决定是否进入 sandbox 审批。

        Returns:
            None: 直接执行（risk=low/medium，或已在审批上下文）
            dict: 阻止执行，返回 sandbox pending 信息（或拒绝消息）
        """
        registry = getattr(self.router, "tool_registry", None)
        if registry is None:
            return None
        found = registry.find_by_action_id(action_id)
        if not found:
            return None
        spec, cmd = found

        # 只有 high 风险才需要审批（P0-3 纵深：risk 一律视为不区分大小写，
        # 即使绕过 from_dict 归一化的构造路径，"High" 也命中 high 拦截）
        risk_lower = str(cmd.risk).lower()
        if risk_lower != "high":
            return None

        # high + auto → 拒绝（不允许无审批执行高风险）
        confirmation_lower = str(cmd.confirmation).lower()
        if confirmation_lower == "auto":
            return {
                "success": False,
                "action_id": action_id,
                "error": (f"高风险工具 '{action_id}' 必须配置 confirmation='first_time' 或 'every_time'，当前为 'auto' 已拒绝执行（防止误操作）"),
                "risk_level": "high",
            }

        try:
            from services.sandbox_manager import _in_approval_var, get_sandbox_manager
        except ImportError:
            logger.warning("sandbox_manager unavailable, executing high-risk tool without approval")
            return None

        # 已在审批上下文中 → 直接执行
        if _in_approval_var.get():
            return None

        sandbox = get_sandbox_manager()
        preview = self._build_tool_preview(spec, cmd, action_id, payload)
        pending_id = sandbox.add(action_id, dict(payload), preview)
        # P1-C 残留补刀：即时返回信封须脱敏（存储/事件副本由 add() 内部处理）
        from services.preview_redact import redact_preview_args

        redacted_preview = redact_preview_args(preview)
        logger.info(
            "High-risk tool '%s' blocked for sandbox approval: pending_id=%s",
            action_id,
            pending_id,
        )
        return {
            "requires_approval": True,
            "pending_id": pending_id,
            "action_id": action_id,
            "risk_level": "high",
            "confirmation": cmd.confirmation,
            "preview": redacted_preview,
            "message": (
                f"[等待用户批准] 高风险工具 {action_id} (risk={cmd.risk}, confirmation={cmd.confirmation}) 已提交审批 pending_id={pending_id}"
            ),
        }

    @staticmethod
    def _build_tool_preview(spec: Any, cmd: Any, action_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        """为高风险工具构建 sandbox 审批预览信息。"""
        return {
            "action_id": action_id,
            "tool_id": spec.tool_id,
            "command_name": cmd.name,
            "template": cmd.template,
            "stdin_template": cmd.stdin_template or None,
            "risk": cmd.risk,
            "confirmation": cmd.confirmation,
            "timeout": cmd.timeout,
            "payload": dict(payload),
            "env_keys": list(set([*spec.env_keys, *cmd.env_keys])),
            "description": cmd.description or spec.description,
        }

    def get_dynamic_handlers(self) -> dict[str, Any]:
        """返回所有动态工具的 handler 字典。

        每个 handler 是 functools.partial(dispatch, action_id)，
        这样 CapabilityRouter.execute 调用 handler(payload) 时，
        action_id 已被绑定。
        """
        registry = getattr(self.router, "tool_registry", None)
        if not registry:
            return {}
        handlers = {}
        for action_id in registry.get_all_action_ids():
            # partial 绑定 action_id，handler(payload) → dispatch(action_id, payload)
            handlers[action_id] = functools.partial(self.dispatch, action_id)
        return handlers

    def refresh(self) -> dict[str, Any]:
        """重新加载 ToolSpec 配置并返回新增的 action_id 列表。

        在通过 RPC 注册新工具后调用，让 CapabilityRouter 更新 _handlers。
        """
        registry = getattr(self.router, "tool_registry", None)
        if not registry:
            return {"new_actions": []}
        registry._load()
        new_handlers = self.get_dynamic_handlers()

        existing = getattr(self.router, "_handlers", {})
        added = []
        for action_id, handler in new_handlers.items():
            if action_id not in existing:
                existing[action_id] = handler
                added.append(action_id)
            else:
                # 已存在则覆盖（配置可能已更新）
                existing[action_id] = handler

        cap_registry = getattr(self.router, "capability_registry", None)
        if cap_registry is not None:
            self._register_to_capability_registry(cap_registry, registry)

        return {"new_actions": added, "total_actions": len(new_handlers)}

    def _register_to_capability_registry(self, cap_registry, tool_registry: ToolSpecRegistry) -> None:
        """把动态工具注册到 CapabilityRegistry。"""
        from services.capability_types import CapabilitySpec

        tool_group_id = "tool"
        if cap_registry.get_group(tool_group_id) is None:
            from services.capability_types import CapabilityGroup

            cap_registry.register_group(
                CapabilityGroup(
                    group_id=tool_group_id,
                    name="外部工具",
                    description="通过 ToolSpec 配置注册的外部 CLI 工具",
                )
            )

        for spec in tool_registry.list_enabled():
            for entry in spec.get_catalog_entries():
                action_id = entry["action_id"]
                spec_obj = CapabilitySpec(
                    capability_id=action_id,
                    description=entry["description"],
                    risk_level=entry.get("risk", "medium"),
                    group=tool_group_id,
                    source=f"tool_spec:{spec.tool_id}",
                    enabled=True,
                    payload_schema=dict(entry.get("payload_schema") or {}),
                    timeout_default=60,
                    confirmation_level=entry.get("confirmation", "auto"),
                )
                cap_registry.register_capability(spec_obj)
